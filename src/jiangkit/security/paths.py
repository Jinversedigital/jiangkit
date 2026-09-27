"""Path traversal and zip-slip protection."""
from __future__ import annotations

import os
import stat
import zipfile
from pathlib import Path, PurePosixPath, PureWindowsPath


class UnsafePathError(ValueError):
    """Raised when a path would escape its allowed base directory."""


def is_within(base: str | os.PathLike, target: str | os.PathLike) -> bool:
    base_r = Path(base).resolve()
    tgt_r = Path(target).resolve()
    return tgt_r == base_r or base_r in tgt_r.parents


def safe_join(base: str | os.PathLike, *parts: str) -> Path:
    """Join *parts* onto *base* and guarantee the result stays inside *base*.

    Rejects absolute parts, drive letters, '..' escapes and symlinks that point outside.
    """
    base_p = Path(base).resolve()
    for part in parts:
        s = str(part)
        if "\x00" in s:
            raise UnsafePathError("NUL byte in path")
        if PurePosixPath(s).is_absolute() or PureWindowsPath(s).is_absolute() or PureWindowsPath(s).drive:
            raise UnsafePathError(f"absolute path not allowed: {s!r}")
    target = base_p.joinpath(*parts).resolve()
    if not (target == base_p or base_p in target.parents):
        raise UnsafePathError(f"path escapes base directory: {'/'.join(map(str, parts))!r}")
    return target


def safe_filename(name: str, default: str = "file", max_len: int = 150) -> str:
    """Reduce an untrusted name (e.g. an upload's filename) to a safe single path component."""
    name = str(name).replace("\\", "/").split("/")[-1]
    name = "".join(ch for ch in name if ch.isprintable() and ch not in '<>:"|?*\x00')
    name = name.strip().lstrip(".")
    if not name:
        name = default
    if len(name) > max_len:
        stem, dot, ext = name.rpartition(".")
        name = (stem[: max_len - len(ext) - 1] + "." + ext) if dot and len(ext) < 12 else name[:max_len]
    return name


def safe_extract_zip(zf: zipfile.ZipFile, dest: str | os.PathLike, *, max_total_bytes: int = 2 * 1024**3,
                     max_files: int = 20000, max_ratio: float = 200.0, pwd: bytes | None = None) -> list[Path]:
    """Extract a zip archive safely.

    * zip-slip: every member must resolve inside *dest* (absolute paths, '..', drive letters rejected)
    * symlink members are skipped (they could point outside dest)
    * zip-bomb guards: member count, total uncompressed size, and per-member compression ratio
    """
    dest_p = Path(dest).resolve()
    dest_p.mkdir(parents=True, exist_ok=True)
    infos = zf.infolist()
    if len(infos) > max_files:
        raise UnsafePathError(f"archive has too many entries ({len(infos)} > {max_files})")
    total = sum(i.file_size for i in infos)
    if total > max_total_bytes:
        raise UnsafePathError(f"archive expands to {total} bytes (> {max_total_bytes})")
    out: list[Path] = []
    for info in infos:
        mode = (info.external_attr >> 16) & 0xFFFF
        if stat.S_ISLNK(mode):
            continue  # never materialise symlinks from untrusted archives
        if info.compress_size and info.file_size / max(info.compress_size, 1) > max_ratio and info.file_size > 10 * 1024**2:
            raise UnsafePathError(f"suspicious compression ratio for {info.filename!r}")
        fn = info.filename.replace("\\", "/")
        if fn.startswith("/") or PureWindowsPath(info.filename).drive:
            raise UnsafePathError(f"absolute path in archive: {info.filename!r}")
        target = safe_join(dest_p, *[p for p in fn.split("/") if p not in ("",)])
        if info.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        written = 0
        with zf.open(info, pwd=pwd) as src, open(target, "wb") as dst:
            while True:
                chunk = src.read(1 << 20)
                if not chunk:
                    break
                written += len(chunk)
                if written > info.file_size + 1024:  # header lied about size
                    raise UnsafePathError(f"member {info.filename!r} larger than declared")
                dst.write(chunk)
        out.append(target)
    return out
