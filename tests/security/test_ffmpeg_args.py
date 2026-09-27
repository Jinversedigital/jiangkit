"""ffmpeg argument / filtergraph injection protection."""
from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from jiangkit.security.proc import (UnsafeArgumentError, concat_list_line, escape_filter_path, ff_font, ff_int,
                                    ff_lang, ff_rate, ff_size, ff_time, media_path)

from ._util import jiang


@pytest.mark.parametrize("p", ["http://169.254.169.254/x.mp4", "https://evil/x.m3u8", "concat:a.mp4|/etc/passwd",
                               "file:/etc/passwd", "tcp://1.2.3.4:5",
                               "pipe:0", "", "a\nb", "a\x00b"])
def test_media_path_rejects_protocols(p):
    with pytest.raises(UnsafeArgumentError):
        media_path(p)


def test_media_path_subfile_trick_becomes_plain_file(tmp_path, monkeypatch):
    # ffmpeg only detects a protocol when the string starts with it; an absolute path never does.
    monkeypatch.chdir(tmp_path)
    assert media_path("subfile,,start,0,end,0,,:/etc/passwd").startswith(str(tmp_path) + "/")


def test_media_path_makes_option_like_names_absolute(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    out = media_path("-i")
    assert out.startswith("/") and Path(out).name == "-i"
    assert media_path("-y.mp4").startswith(str(tmp_path))


@pytest.mark.parametrize("fn,bad", [(ff_time, "1;rm -rf /"), (ff_time, "00:00:01,drawtext=x"), (ff_rate, "30[out]"),
                                    (ff_size, "1080x1920;x"), (ff_font, "Arial'\\:x"), (ff_lang, "en';x")])
def test_validators_reject_filter_metachars(fn, bad):
    with pytest.raises(argparse.ArgumentTypeError):
        fn(bad)


def test_validators_accept_normal():
    assert ff_time("00:01:02.5") == "00:01:02.5"
    assert ff_rate("29.97") == "29.97"
    assert ff_size("1080x1920") == "1080x1920"
    assert ff_font("Noto Sans CJK TC") == "Noto Sans CJK TC"
    assert ff_lang("zh-TW") == "zh-TW"
    assert ff_int(1, 10)("5") == 5
    with pytest.raises(argparse.ArgumentTypeError):
        ff_int(1, 10)("11")


@pytest.mark.parametrize("name", ["a'b.srt", "a;b.srt", "a[0].srt", "a,b.srt", "a=b.srt"])
def test_filter_path_rejects_metachars(tmp_path, name):
    with pytest.raises(UnsafeArgumentError):
        escape_filter_path(str(tmp_path / name))


def test_filter_path_escapes_colon(tmp_path):
    assert "\\:" in escape_filter_path("C:/x/y.srt") or ":" not in escape_filter_path(str(tmp_path / "y.srt"))


def test_concat_line_quotes_single_quote(tmp_path):
    line = concat_list_line(str(tmp_path / "it's.mp4"))
    assert line.startswith("file '") and "'\\''" in line
    with pytest.raises(UnsafeArgumentError):
        concat_list_line(str(tmp_path / "a\nfile '/etc/passwd"))


def test_video_cli_rejects_injected_time(tmp_path):
    r = jiang("media", "video", "trim", tmp_path / "in.mp4", "--start", "1;touch /tmp/pwn", "-t", "2", "-o", tmp_path / "o.mp4")
    assert r.returncode == 2
    assert not Path("/tmp/pwn").exists()


def test_video_cli_rejects_url_input(tmp_path):
    r = jiang("media", "video", "info", "http://169.254.169.254/latest/meta-data/")
    assert r.returncode in (1, 5), r.stderr


def test_no_shell_true_in_ffmpeg_callers():
    src = Path(__file__).resolve().parents[2] / "src" / "jiangkit"
    for f in ["media/video_tools.py", "experimental/data_sonify.py"]:
        text = (src / f).read_text()
        assert "shell=True" not in text, f
