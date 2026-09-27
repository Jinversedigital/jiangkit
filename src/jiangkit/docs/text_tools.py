#!/usr/bin/env python3
"""text_tools.py - Chinese text and subtitle utilities.

Subcommands:
  zh         Simplified <-> Traditional conversion via OpenCC (default s2twp)
  sub-shift  Shift .srt/.vtt timings by +/- seconds (optionally scale for fps change)
  sub-merge  Merge subtitle files (append sequentially / combine by time / bilingual)
  sub-conv   Convert between .srt and .vtt
  sub-text   Extract plain text (dialogue) from subtitles
  count      Character / CJK / word / line counts
  replace    Batch find-replace across files (dry-run by default)
"""
import argparse
import difflib
import fnmatch
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

OPENCC_CONFIGS = ["s2t", "t2s", "s2tw", "tw2s", "s2twp", "tw2sp", "s2hk", "hk2s", "t2tw", "tw2t"]


# ---------------------------------------------------------------- zh conversion
def get_converter(config):
    try:
        from opencc import OpenCC
    except ImportError:
        sys.exit("opencc not installed: pip install opencc-python-reimplemented")
    return OpenCC(config)


def cmd_zh(a):
    cc = get_converter(a.config)
    if a.text is not None:
        print(cc.convert(a.text))
        return
    if not a.inputs:
        print(cc.convert(sys.stdin.read()), end="")
        return
    for f in map(Path, a.inputs):
        out_text = cc.convert(f.read_text(encoding="utf-8"))
        if a.inplace:
            dest = f
        elif a.outdir:
            dest = Path(a.outdir) / f.name
            dest.parent.mkdir(parents=True, exist_ok=True)
        elif a.output and len(a.inputs) == 1:
            dest = Path(a.output)
        else:
            dest = f.with_name(f"{f.stem}.{a.config}{f.suffix}")
        dest.write_text(out_text, encoding="utf-8")
        print(f"{f} -> {dest}")


# ---------------------------------------------------------------- subtitles
@dataclass
class Cue:
    start: float  # seconds
    end: float
    text: str


TIME_RE = re.compile(r"(?:(\d+):)?(\d{1,2}):(\d{2})[,.](\d{1,3})")
ARROW_RE = re.compile(r"(\S+)\s*-->\s*(\S+)")


def parse_time(s):
    m = TIME_RE.fullmatch(s.strip())
    if not m:
        raise ValueError(f"Bad timestamp: {s}")
    h, mnt, sec, ms = m.groups()
    return int(h or 0) * 3600 + int(mnt) * 60 + int(sec) + int(ms.ljust(3, "0")) / 1000


def fmt_time(t, vtt=False):
    t = max(0.0, t)
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{'.' if vtt else ','}{ms:03d}"


def parse_subs(text):
    """Parse SRT or WebVTT text into a list of Cues."""
    text = text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    cues = []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = block.split("\n")
        idx = next((i for i, l in enumerate(lines) if "-->" in l), None)
        if idx is None:
            continue  # WEBVTT header, NOTE, STYLE blocks, etc.
        m = ARROW_RE.search(lines[idx])
        cues.append(Cue(parse_time(m.group(1)), parse_time(m.group(2)),
                        "\n".join(lines[idx + 1:]).strip()))
    return cues


def render_srt(cues):
    return "\n".join(f"{i}\n{fmt_time(c.start)} --> {fmt_time(c.end)}\n{c.text}\n"
                     for i, c in enumerate(cues, 1))


def render_vtt(cues):
    body = "\n".join(f"{fmt_time(c.start, True)} --> {fmt_time(c.end, True)}\n{c.text}\n"
                     for c in cues)
    return "WEBVTT\n\n" + body


def load_subs(path):
    return parse_subs(Path(path).read_text(encoding="utf-8-sig"))


def save_subs(cues, path):
    p = Path(path)
    p.write_text(render_vtt(cues) if p.suffix.lower() == ".vtt" else render_srt(cues),
                 encoding="utf-8")
    print(f"Wrote {len(cues)} cues -> {p}")


def cmd_sub_shift(a):
    cues = load_subs(a.input)
    ratio = a.from_fps / a.to_fps if (a.from_fps and a.to_fps) else 1.0
    after = parse_time(a.after) if a.after else 0.0
    for c in cues:
        if c.start >= after:
            c.start = c.start * ratio + a.seconds
            c.end = c.end * ratio + a.seconds
    cues = [c for c in cues if c.end > 0]
    save_subs(cues, a.output or a.input)


def cmd_sub_merge(a):
    tracks = [load_subs(f) for f in a.inputs]
    if a.mode == "append":
        out, offset = [], 0.0
        for t in tracks:
            for c in t:
                out.append(Cue(c.start + offset, c.end + offset, c.text))
            if out:
                offset = out[-1].end + a.gap
    elif a.mode == "combine":
        out = sorted((c for t in tracks for c in t), key=lambda c: (c.start, c.end))
    else:  # bilingual: pair cues with the first track's timing by overlap
        base, rest = tracks[0], tracks[1:]
        out = []
        for c in base:
            texts = [c.text]
            for t in rest:
                best = max(t, key=lambda o: min(o.end, c.end) - max(o.start, c.start),
                           default=None)
                if best and min(best.end, c.end) - max(best.start, c.start) > 0:
                    texts.append(best.text)
            out.append(Cue(c.start, c.end, "\n".join(texts)))
    save_subs(out, a.output)


def cmd_sub_conv(a):
    save_subs(load_subs(a.input), a.output)


def strip_tags(s):
    return re.sub(r"<[^>]+>|\{\\[^}]*\}", "", s)


def cmd_sub_text(a):
    lines = []
    for c in load_subs(a.input):
        t = strip_tags(c.text).replace("\n", " ").strip()
        if a.timestamps:
            t = f"[{fmt_time(c.start)[:8]}] {t}"
        if t and (a.keep_repeats or not lines or lines[-1] != t):
            lines.append(t)
    text = "\n".join(lines) + "\n"
    if a.output:
        Path(a.output).write_text(text, encoding="utf-8")
        print(f"Wrote {len(lines)} lines -> {a.output}")
    else:
        print(text, end="")


# ---------------------------------------------------------------- count
CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\U00020000-\U0002ffff"
                    r"\u3040-\u30ff\uac00-\ud7af]")
WORD_RE = re.compile(r"[A-Za-z0-9]+(?:['’\-][A-Za-z0-9]+)*")


def count_text(text):
    no_ws = re.sub(r"\s", "", text)
    cjk = len(CJK_RE.findall(text))
    words = len(WORD_RE.findall(text))
    return {"chars": len(text), "chars_no_space": len(no_ws), "cjk_chars": cjk,
            "latin_words": words, "mixed_word_count": cjk + words,
            "lines": text.count("\n") + (1 if text and not text.endswith("\n") else 0)}


def cmd_count(a):
    if a.text is not None:
        sources = [("<text>", a.text)]
    elif a.inputs:
        sources = [(f, Path(f).read_text(encoding="utf-8")) for f in a.inputs]
    else:
        sources = [("<stdin>", sys.stdin.read())]
    total = {}
    print(f"{'source':<30} {'chars':>7} {'no-sp':>7} {'CJK':>7} {'words':>7} {'mixed':>7} {'lines':>6}")
    for name, text in sources:
        c = count_text(text)
        for k, v in c.items():
            total[k] = total.get(k, 0) + v
        print(f"{str(name)[-30:]:<30} {c['chars']:>7} {c['chars_no_space']:>7} {c['cjk_chars']:>7}"
              f" {c['latin_words']:>7} {c['mixed_word_count']:>7} {c['lines']:>6}")
    if len(sources) > 1:
        print(f"{'TOTAL':<30} {total['chars']:>7} {total['chars_no_space']:>7} {total['cjk_chars']:>7}"
              f" {total['latin_words']:>7} {total['mixed_word_count']:>7} {total['lines']:>6}")


# ---------------------------------------------------------------- replace
def iter_files(paths, pattern, recursive):
    for p in map(Path, paths):
        if p.is_file():
            yield p
        elif p.is_dir():
            it = p.rglob("*") if recursive else p.iterdir()
            for f in sorted(it):
                if f.is_file() and fnmatch.fnmatch(f.name, pattern) and ".git" not in f.parts:
                    yield f


def cmd_replace(a):
    flags = re.IGNORECASE if a.ignore_case else 0
    rx = re.compile(a.find if a.regex else re.escape(a.find), flags)
    repl = a.replace if a.regex else a.replace.replace("\\", "\\\\")
    changed_files, total = 0, 0
    for f in iter_files(a.paths, a.glob, a.recursive):
        try:
            old = f.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue  # skip binary / unreadable files
        new, n = rx.subn(repl, old)
        if n == 0:
            continue
        changed_files += 1
        total += n
        print(f"{f}: {n} replacement(s)")
        if a.diff:
            sys.stdout.writelines(difflib.unified_diff(
                old.splitlines(True), new.splitlines(True), str(f), str(f) + " (new)", n=0))
        if a.apply:
            if a.backup:
                shutil.copy2(f, f.with_name(f.name + ".bak"))
            f.write_text(new, encoding="utf-8")
    verb = "Replaced" if a.apply else "[dry-run] Would replace"
    print(f"\n{verb} {total} occurrence(s) in {changed_files} file(s)."
          + ("" if a.apply else " Add --apply to write changes."))


def build_parser():
    p = argparse.ArgumentParser(description="Chinese text & subtitle toolkit")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("zh", help="Simplified/Traditional conversion (OpenCC)",
                       description="s2twp = Simplified -> Taiwan Traditional with phrases; "
                                   "tw2sp = reverse")
    s.add_argument("inputs", nargs="*", help="Files (omit to read stdin)")
    s.add_argument("-c", "--config", default="s2twp", choices=OPENCC_CONFIGS)
    s.add_argument("-t", "--text", help="Convert this string instead of files")
    s.add_argument("-o", "--output", help="Output file (single input)")
    s.add_argument("-d", "--outdir", help="Output folder (multiple inputs)")
    s.add_argument("--inplace", action="store_true", help="Overwrite input files")
    s.set_defaults(func=cmd_zh)

    s = sub.add_parser("sub-shift", help="Shift subtitle timing")
    s.add_argument("input")
    s.add_argument("-s", "--seconds", type=float, required=True, help="e.g. 1.5 or -0.8")
    s.add_argument("--after", help="Only shift cues starting at/after HH:MM:SS,mmm")
    s.add_argument("--from-fps", type=float, help="Retime from this fps (e.g. 25)")
    s.add_argument("--to-fps", type=float, help="...to this fps (e.g. 23.976)")
    s.add_argument("-o", "--output", help="Default: overwrite input")
    s.set_defaults(func=cmd_sub_shift)

    s = sub.add_parser("sub-merge", help="Merge subtitle files")
    s.add_argument("inputs", nargs="+")
    s.add_argument("-o", "--output", required=True)
    s.add_argument("-m", "--mode", choices=["append", "combine", "bilingual"], default="append",
                   help="append: one after another; combine: interleave by time; "
                        "bilingual: stack texts of overlapping cues onto the first file's timing")
    s.add_argument("--gap", type=float, default=0.0, help="Seconds between files in append mode")
    s.set_defaults(func=cmd_sub_merge)

    s = sub.add_parser("sub-conv", help="Convert srt <-> vtt (by output extension)")
    s.add_argument("input")
    s.add_argument("-o", "--output", required=True)
    s.set_defaults(func=cmd_sub_conv)

    s = sub.add_parser("sub-text", help="Extract dialogue text")
    s.add_argument("input")
    s.add_argument("-o", "--output")
    s.add_argument("--timestamps", action="store_true")
    s.add_argument("--keep-repeats", action="store_true")
    s.set_defaults(func=cmd_sub_text)

    s = sub.add_parser("count", help="Character / word counts")
    s.add_argument("inputs", nargs="*")
    s.add_argument("-t", "--text")
    s.set_defaults(func=cmd_count)

    s = sub.add_parser("replace", help="Batch find/replace (dry-run unless --apply)")
    s.add_argument("paths", nargs="+", help="Files or folders")
    s.add_argument("-f", "--find", required=True)
    s.add_argument("-r", "--replace", required=True)
    s.add_argument("--regex", action="store_true", help="Treat --find as regex (\\1 groups ok)")
    s.add_argument("-i", "--ignore-case", action="store_true")
    s.add_argument("-g", "--glob", default="*", help="File name glob inside folders")
    s.add_argument("-R", "--recursive", action="store_true")
    s.add_argument("--diff", action="store_true", help="Show unified diff")
    s.add_argument("--backup", action="store_true", help="Write .bak copies when applying")
    s.add_argument("--apply", action="store_true")
    s.set_defaults(func=cmd_replace)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except (ValueError, FileNotFoundError) as e:
        sys.exit(f"Error: {e}")


if __name__ == "__main__":
    main()
