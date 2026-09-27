#!/usr/bin/env python3
"""file_tools.py - file management utilities.

Subcommands:
  rename    Batch rename with a template / regex / sequence numbers (dry-run by default)
  organize  Sort a folder's files into sub-folders by type or date (dry-run by default)
  dupes     Find duplicate files by content hash
  du        Disk usage report: largest files and folders
  zip       Create a zip (optional AES password via pyzipper)
  unzip     Extract a zip (supports AES / ZipCrypto passwords)
  backup    Incremental backup (copy only changed files) with a JSON manifest
"""
from jiangkit.security.csvsafe import SafeDictWriter, SafeWriter  # CSV formula-injection escaping
import argparse
import csv
import datetime as dt
import fnmatch
import getpass
import hashlib
import json
import os
import re
import shutil
import sys
import zipfile
from collections import defaultdict
from pathlib import Path

TYPE_MAP = {
    "Images": {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".heic",
               ".svg", ".psd", ".raw", ".cr2", ".nef", ".avif"},
    "Videos": {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".flv", ".wmv"},
    "Audio": {".mp3", ".wav", ".flac", ".aac", ".m4a", ".ogg", ".opus"},
    "Documents": {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".txt", ".md",
                  ".csv", ".json", ".odt", ".rtf", ".epub", ".srt", ".vtt"},
    "Archives": {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz"},
    "Code": {".py", ".js", ".ts", ".html", ".css", ".sh", ".ps1", ".java", ".c", ".cpp",
             ".go", ".rs", ".php", ".rb", ".jsx", ".tsx", ".vue", ".yml", ".yaml"},
    "Fonts": {".ttf", ".otf", ".woff", ".woff2", ".ttc"},
}
MANIFEST = ".backup_manifest.json"


def human(n):
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024


def sha256(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def list_files(folder, pattern="*", recursive=False):
    folder = Path(folder)
    it = folder.rglob("*") if recursive else folder.iterdir()
    return sorted(p for p in it if p.is_file() and fnmatch.fnmatch(p.name, pattern))


# ---------------------------------------------------------------- rename
def natural_key(p):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", p.name)]


def plan_renames(files, template=None, find=None, replace="", start=1, step=1,
                 lower=False, upper=False, flags=0):
    """Return list of (src, dst) tuples. Template tokens: {name} {ext} {n} {n:03d}
    {date} (file mtime YYYYMMDD) {parent}. Regex find/replace applied to the stem first."""
    if template:
        import string

        for _lit, field, _spec, _conv in string.Formatter().parse(template):
            # only plain {name}/{n:03d}-style fields: no attribute/index access (format-string injection)
            if field is not None and (not re.fullmatch(r"[A-Za-z_]*", field) or field not in ("", "name", "ext", "n", "date", "parent")):
                raise ValueError(f"unsupported template field: {{{field}}}")
    plans = []
    for i, f in enumerate(files):
        stem, ext = f.stem, f.suffix
        if find:
            stem = re.sub(find, replace, stem, flags=flags)
        new = stem
        if template:
            mtime = dt.datetime.fromtimestamp(f.stat().st_mtime)
            new = template.format(name=stem, ext=ext.lstrip("."), n=start + i * step,
                                  date=mtime.strftime("%Y%m%d"), parent=f.parent.name)
        if lower:
            new = new.lower()
        if upper:
            new = new.upper()
        # Keep original extension unless the template already contains one via {ext}
        if not (template and "{ext}" in template):
            new += ext
        if not new or new in (".", "..") or "/" in new or "\\" in new or "\x00" in new:
            raise ValueError(f"unsafe target name {new!r} (no path separators or '..')")
        plans.append((f, f.with_name(new)))
    return plans


def cmd_rename(a):
    files = list_files(a.folder, a.glob, a.recursive)
    files.sort(key=natural_key if a.sort == "name" else (lambda p: p.stat().st_mtime))
    flags = re.IGNORECASE if a.ignore_case else 0
    plans = [(s, d) for s, d in plan_renames(files, a.template, a.find, a.replace, a.start,
                                             a.step, a.lower, a.upper, flags) if s != d]
    targets = [d for _, d in plans]
    if len(set(targets)) != len(targets):
        sys.exit("Error: the plan would produce duplicate target names; adjust the template")
    sources = {s for s, _ in plans}
    for s, d in plans:
        if d.exists() and d not in sources:
            sys.exit(f"Error: target already exists: {d}")
    for s, d in plans:
        print(f"{s.name}  ->  {d.name}")
    if not a.apply:
        print(f"\n[dry-run] {len(plans)} files would be renamed. Add --apply to do it.")
        return
    # Two-phase rename via temporary names to handle swaps/chains safely
    temps = []
    for i, (s, d) in enumerate(plans):
        t = s.with_name(f".__rename_tmp_{os.getpid()}_{i}")
        s.rename(t)
        temps.append((t, d, s))
    log = Path(a.folder) / f"rename_log_{dt.datetime.now():%Y%m%d_%H%M%S}.csv"
    with open(log, "w", newline="", encoding="utf-8") as fh:
        w = SafeWriter(fh)
        w.writerow(["old", "new"])
        for t, d, s in temps:
            t.rename(d)
            w.writerow([str(s), str(d)])
    print(f"Renamed {len(plans)} files. Undo log: {log}")


# ---------------------------------------------------------------- organize
def category_for(path):
    ext = path.suffix.lower()
    for cat, exts in TYPE_MAP.items():
        if ext in exts:
            return cat
    return "Other"


def cmd_organize(a):
    folder = Path(a.folder)
    files = [p for p in folder.iterdir() if p.is_file() and not p.name.startswith(".")]
    moves = []
    for f in sorted(files):
        if a.by == "type":
            sub = Path(category_for(f))
        else:
            m = dt.datetime.fromtimestamp(f.stat().st_mtime)
            sub = Path(m.strftime(a.date_format))
        from jiangkit.security.paths import safe_join

        dest = safe_join(folder, *sub.parts, f.name)  # --date-format like "../x" can't escape the folder
        n = 1
        while dest.exists():
            dest = folder / sub / f"{f.stem}_{n}{f.suffix}"
            n += 1
        moves.append((f, dest))
    for s, d in moves:
        print(f"{s.name}  ->  {d.relative_to(folder)}")
    if not a.apply:
        print(f"\n[dry-run] {len(moves)} files would be {'copied' if a.copy else 'moved'}. "
              "Add --apply to do it.")
        return
    for s, d in moves:
        d.parent.mkdir(parents=True, exist_ok=True)
        (shutil.copy2 if a.copy else shutil.move)(str(s), str(d))
    print(f"Organized {len(moves)} files in {folder}")


# ---------------------------------------------------------------- dupes
def find_duplicates(folder, min_size=1):
    by_size = defaultdict(list)
    for p in Path(folder).rglob("*"):
        if p.is_file() and not p.is_symlink():
            s = p.stat().st_size
            if s >= min_size:
                by_size[s].append(p)
    groups = []
    for size, paths in by_size.items():
        if len(paths) < 2:
            continue
        by_hash = defaultdict(list)
        for p in paths:
            by_hash[sha256(p)].append(p)
        for h, ps in by_hash.items():
            if len(ps) > 1:
                groups.append({"hash": h, "size": size, "files": sorted(str(x) for x in ps)})
    groups.sort(key=lambda g: g["size"] * (len(g["files"]) - 1), reverse=True)
    return groups


def cmd_dupes(a):
    groups = find_duplicates(a.folder, a.min_size)
    wasted = sum(g["size"] * (len(g["files"]) - 1) for g in groups)
    for g in groups:
        print(f"\n[{human(g['size'])}] sha256 {g['hash'][:12]}")
        for i, f in enumerate(g["files"]):
            print(f"  {'KEEP ' if i == 0 else 'dup  '}{f}")
    print(f"\n{len(groups)} duplicate groups, {human(wasted)} reclaimable")
    if a.json:
        Path(a.json).write_text(json.dumps(groups, ensure_ascii=False, indent=2), "utf-8")
        print(f"Report -> {a.json}")
    if a.move_to:
        dest = Path(a.move_to)
        dups = [f for g in groups for f in g["files"][1:]]
        if not a.apply:
            print(f"[dry-run] {len(dups)} duplicates would be moved to {dest}. Add --apply.")
            return
        dest.mkdir(parents=True, exist_ok=True)
        for f in dups:
            target = dest / Path(f).name
            n = 1
            while target.exists():
                target = dest / f"{Path(f).stem}_{n}{Path(f).suffix}"
                n += 1
            shutil.move(f, target)
        print(f"Moved {len(dups)} duplicates to {dest}")


# ---------------------------------------------------------------- du
def disk_usage(folder):
    folder = Path(folder)
    files, dirs = [], defaultdict(int)
    for root, _, names in os.walk(folder):
        for n in names:
            p = Path(root, n)
            try:
                s = p.lstat().st_size
            except OSError:
                continue
            files.append((s, p))
            # Attribute size to every ancestor folder up to the root
            rel = p.parent.relative_to(folder)
            parts = rel.parts
            for i in range(len(parts) + 1):
                dirs[folder.joinpath(*parts[:i])] += s
    return files, dirs


def cmd_du(a):
    files, dirs = disk_usage(a.folder)
    total = dirs.get(Path(a.folder), 0)
    print(f"Total: {human(total)} in {len(files)} files\n")
    print(f"Top {a.top} largest files:")
    for s, p in sorted(files, reverse=True)[:a.top]:
        print(f"  {human(s):>10}  {p}")
    print(f"\nTop {a.top} largest folders:")
    for p, s in sorted(dirs.items(), key=lambda kv: kv[1], reverse=True)[:a.top]:
        print(f"  {human(s):>10}  {p}")
    if a.csv:
        with open(a.csv, "w", newline="", encoding="utf-8") as fh:
            w = SafeWriter(fh)
            w.writerow(["type", "bytes", "path"])
            for s, p in sorted(files, reverse=True):
                w.writerow(["file", s, str(p)])
            for p, s in sorted(dirs.items(), key=lambda kv: kv[1], reverse=True):
                w.writerow(["dir", s, str(p)])
        print(f"\nCSV -> {a.csv}")


# ---------------------------------------------------------------- zip / unzip
def get_password(a):
    if a.password_env:
        pw = os.environ.get(a.password_env)
        if not pw:
            sys.exit(f"Env var {a.password_env} is empty")
        return pw
    if a.password:
        return getpass.getpass("Password: ")
    return None


def cmd_zip(a):
    pw = get_password(a)
    out = Path(a.output)
    entries = []
    for src in map(Path, a.inputs):
        if src.is_dir():
            for p in sorted(src.rglob("*")):
                if p.is_file():
                    entries.append((p, Path(src.name) / p.relative_to(src)))
        else:
            entries.append((src, Path(src.name)))
    if pw:
        import pyzipper
        zf = pyzipper.AESZipFile(out, "w", compression=pyzipper.ZIP_DEFLATED,
                                 encryption=pyzipper.WZ_AES)
        zf.setpassword(pw.encode())
    else:
        zf = zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED)
    with zf:
        for p, arc in entries:
            zf.write(p, arc.as_posix())
    print(f"Zipped {len(entries)} files -> {out} ({human(out.stat().st_size)})"
          f"{' [AES-256 encrypted]' if pw else ''}")


def cmd_unzip(a):
    pw = get_password(a)
    import pyzipper
    dest = Path(a.outdir).resolve()
    from jiangkit.security.paths import UnsafePathError, safe_extract_zip

    with pyzipper.AESZipFile(a.input) as zf:
        if pw:
            zf.setpassword(pw.encode())
        try:
            # zip-slip (absolute/../drive paths), symlink members and zip-bomb limits are enforced here
            safe_extract_zip(zf, dest)
        except UnsafePathError as e:
            sys.exit(f"Refusing unsafe archive: {e}")
        except RuntimeError as e:
            sys.exit(f"Extraction failed (wrong/missing password?): {e}")
        n = len(zf.infolist())
    print(f"Extracted {n} entries -> {dest}")


# ---------------------------------------------------------------- backup
def cmd_backup(a):
    src, dst = Path(a.source).resolve(), Path(a.dest).resolve()
    if dst == src or src in dst.parents:
        sys.exit("Destination must not be inside the source")
    dst.mkdir(parents=True, exist_ok=True)
    mpath = dst / MANIFEST
    manifest = json.loads(mpath.read_text("utf-8")) if mpath.exists() else {"files": {}}
    old = manifest.get("files", {})
    excludes = a.exclude or []
    new, copied, skipped = {}, [], 0
    for p in sorted(src.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(src).as_posix()
        if any(fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(p.name, pat) for pat in excludes):
            continue
        st = p.stat()
        entry = {"size": st.st_size, "mtime": round(st.st_mtime, 3)}
        prev = old.get(rel)
        changed = (prev is None or prev["size"] != entry["size"]
                   or prev["mtime"] != entry["mtime"] or not (dst / rel).exists())
        if not changed and a.checksum:
            changed = prev.get("sha256") != sha256(p)
        if changed:
            entry["sha256"] = sha256(p)
            copied.append(rel)
            if not a.dry_run:
                (dst / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(p, dst / rel)
        else:
            entry["sha256"] = prev.get("sha256")
            skipped += 1
        new[rel] = entry
    removed = sorted(set(old) - set(new))
    if a.delete and removed and not a.dry_run:
        # Removed files are moved into a timestamped trash folder instead of deleted
        trash = dst / ".deleted" / dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        for rel in removed:
            if (dst / rel).exists():
                (trash / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(dst / rel), str(trash / rel))
    if not a.delete:
        for rel in removed:  # keep tracking files that still exist in the backup
            new[rel] = dict(old[rel], missing_in_source=True)
    if not a.dry_run:
        manifest = {"source": str(src), "updated": dt.datetime.now().isoformat(timespec="seconds"),
                    "files": new,
                    "history": (manifest.get("history", []) + [{
                        "time": dt.datetime.now().isoformat(timespec="seconds"),
                        "copied": len(copied), "unchanged": skipped,
                        "removed": len(removed) if a.delete else 0}])[-50:]}
        mpath.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), "utf-8")
    tag = "[dry-run] " if a.dry_run else ""
    for rel in copied:
        print(f"{tag}copy  {rel}")
    for rel in removed:
        print(f"{tag}{'trash' if a.delete else 'gone '} {rel}")
    print(f"{tag}{len(copied)} copied, {skipped} unchanged, {len(removed)} removed from source")


def build_parser():
    p = argparse.ArgumentParser(description="File management toolkit")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("rename", help="Batch rename (dry-run unless --apply)",
                       description="Template tokens: {name} {ext} {n} {n:03d} {date} {parent}")
    s.add_argument("folder")
    s.add_argument("-t", "--template", help='e.g. "photo_{n:03d}" or "{date}_{name}"')
    s.add_argument("--find", help="Regex to search in the file stem")
    s.add_argument("--replace", default="", help="Replacement (supports \\1 groups)")
    s.add_argument("-i", "--ignore-case", action="store_true")
    s.add_argument("-g", "--glob", default="*", help="Only files matching glob, e.g. '*.png'")
    s.add_argument("-r", "--recursive", action="store_true")
    s.add_argument("--start", type=int, default=1)
    s.add_argument("--step", type=int, default=1)
    s.add_argument("--sort", choices=["name", "mtime"], default="name")
    s.add_argument("--lower", action="store_true")
    s.add_argument("--upper", action="store_true")
    s.add_argument("--apply", action="store_true", help="Actually rename")
    s.set_defaults(func=cmd_rename)

    s = sub.add_parser("organize", help="Sort files into sub-folders (dry-run unless --apply)")
    s.add_argument("folder")
    s.add_argument("--by", choices=["type", "date"], default="type")
    s.add_argument("--date-format", default="%Y/%Y-%m", help="strftime for --by date")
    s.add_argument("--copy", action="store_true", help="Copy instead of move")
    s.add_argument("--apply", action="store_true")
    s.set_defaults(func=cmd_organize)

    s = sub.add_parser("dupes", help="Find duplicate files by SHA-256")
    s.add_argument("folder")
    s.add_argument("--min-size", type=int, default=1, help="Ignore files smaller than N bytes")
    s.add_argument("--json", help="Write JSON report")
    s.add_argument("--move-to", help="Move duplicates (all but first) to this folder")
    s.add_argument("--apply", action="store_true", help="Required with --move-to")
    s.set_defaults(func=cmd_dupes)

    s = sub.add_parser("du", help="Largest files / folders report")
    s.add_argument("folder")
    s.add_argument("-n", "--top", type=int, default=15)
    s.add_argument("--csv", help="Write full CSV report")
    s.set_defaults(func=cmd_du)

    for name, fn, hlp in [("zip", cmd_zip, "Create zip"), ("unzip", cmd_unzip, "Extract zip")]:
        s = sub.add_parser(name, help=hlp)
        if name == "zip":
            s.add_argument("inputs", nargs="+")
            s.add_argument("-o", "--output", required=True)
        else:
            s.add_argument("input")
            s.add_argument("-d", "--outdir", default=".")
        s.add_argument("-p", "--password", action="store_true", help="Prompt for a password")
        s.add_argument("--password-env", help="Read password from this environment variable")
        s.set_defaults(func=fn)

    s = sub.add_parser("backup", help="Incremental backup with manifest")
    s.add_argument("source")
    s.add_argument("dest")
    s.add_argument("--exclude", action="append", help="Glob to exclude (repeatable)")
    s.add_argument("--checksum", action="store_true", help="Also compare SHA-256 of unchanged files")
    s.add_argument("--delete", action="store_true",
                   help="Move files removed from source into dest/.deleted/<time>/")
    s.add_argument("-n", "--dry-run", action="store_true")
    s.set_defaults(func=cmd_backup)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
