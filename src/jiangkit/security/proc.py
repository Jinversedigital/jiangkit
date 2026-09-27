"""Safe subprocess helpers and ffmpeg argument sanitising.

Rules enforced across JiangKit:
* commands are argument lists; shell=True is never combined with user input
* every user-supplied media path handed to ffmpeg/ffprobe is converted to an absolute
  filesystem path, so it can never be parsed as an option ("-f ...") or a protocol URL
  ("concat:", "http://", "pipe:", ...)
* values interpolated into ffmpeg filtergraphs are validated against strict patterns
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess  # nosec B404 - subprocess is required to drive external tools; every call uses an argv list
from pathlib import Path
from typing import Sequence

_PROTO = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*:")
_WIN_DRIVE = re.compile(r"^[A-Za-z]:[\\/]")


class UnsafeArgumentError(ValueError):
    pass


def run(cmd: Sequence[str], **kw) -> subprocess.CompletedProcess:
    """subprocess.run that refuses string commands / shell=True."""
    if isinstance(cmd, (str, bytes)) or kw.get("shell"):
        raise UnsafeArgumentError("commands must be argument lists without shell=True")
    kw.setdefault("timeout", None)
    return subprocess.run(list(map(str, cmd)), **kw)  # noqa: S603  (list argv, no shell)  # nosec B603 - argv list without shell; executable and arguments are fixed or validated


def media_path(p: str | os.PathLike, *, must_exist: bool = False) -> str:
    """Return an absolute local path string safe to pass to ffmpeg as an input or output."""
    s = os.fspath(p)
    if not s or "\x00" in s or "\n" in s:
        raise UnsafeArgumentError("empty or control characters in path")
    if _PROTO.match(s) and not _WIN_DRIVE.match(s):
        raise UnsafeArgumentError(f"URLs/protocols are not accepted as media paths: {s!r}")
    ap = Path(s).expanduser().resolve()
    if must_exist and not ap.is_file():
        raise UnsafeArgumentError(f"input file not found: {s}")
    return str(ap)


# ------------------------------------------------------------------ validators for filter values
_TIME = re.compile(r"^(\d{1,3}:)?(\d{1,2}:)?\d{1,6}(\.\d{1,6})?$")
_RATE = re.compile(r"^\d{1,4}(\.\d{1,4})?(/\d{1,5})?$")
_SIZE = re.compile(r"^\d{2,5}x\d{2,5}$")
_FONT = re.compile(r"^[\w .\-\u3000-\u9fff\uac00-\ud7af]{1,64}$")
_LANG = re.compile(r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})?$")


def _mk(pattern: re.Pattern, what: str):
    def check(value: str) -> str:
        v = str(value).strip()
        if not pattern.match(v):
            raise argparse.ArgumentTypeError(f"invalid {what}: {value!r}")
        return v
    check.__name__ = what
    return check


ff_time = _mk(_TIME, "time (seconds or HH:MM:SS.ms)")
ff_rate = _mk(_RATE, "rate (e.g. 30, 29.97, 1/10)")
ff_size = _mk(_SIZE, "size WxH")
ff_font = _mk(_FONT, "font name")
ff_lang = _mk(_LANG, "language code")


def ff_int(lo: int, hi: int):
    def check(value: str) -> int:
        try:
            v = int(value)
        except ValueError as e:
            raise argparse.ArgumentTypeError(f"not an integer: {value!r}") from e
        if not lo <= v <= hi:
            raise argparse.ArgumentTypeError(f"must be between {lo} and {hi}")
        return v
    return check


def escape_filter_path(p: str) -> str:
    """Escape a path for use inside a single-quoted ffmpeg filter option value.

    Only paths made of 'boring' characters are accepted (callers copy anything else to a
    temporary file first, see video_tools.burn_subs) so no filtergraph metacharacter can leak.
    """
    s = str(Path(p).resolve())
    if not re.match(r"^[\w\-./ :\\　-鿿]+$", s):
        raise UnsafeArgumentError(f"unsupported characters for an ffmpeg filter path: {s!r}")
    return s.replace("\\", "\\\\").replace(":", r"\:")


def concat_list_line(p: str) -> str:
    """One line for ffmpeg's concat demuxer list file, with quoting per the ffmpeg docs."""
    s = str(Path(p).resolve().as_posix())
    if "\n" in s or "\r" in s:
        raise UnsafeArgumentError("newline in path")
    return "file '" + s.replace("'", "'\\''") + "'\n"
