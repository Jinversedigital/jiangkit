"""Tests for video_tools.py on generated test videos (ffmpeg lavfi sources)."""
import subprocess
import urllib.request
from pathlib import Path

import pytest
from PIL import Image

from jiangkit.media import video_tools as V


def make_video(path, w=1280, h=720, dur=4, audio=True, rate=30):
    args = ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
            "-i", f"testsrc2=size={w}x{h}:rate={rate}:duration={dur}"]
    if audio:
        args += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={dur}:sample_rate=48000"]
    args += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-shortest"]
    args += (["-c:a", "aac"] if audio else []) + [str(path)]
    subprocess.run(args, check=True)
    return path


def dur(p):
    return float(V.probe(str(p))["format"]["duration"])


@pytest.fixture(scope="module")
def vids(tmp_path_factory):
    d = tmp_path_factory.mktemp("vid")
    return {"land": make_video(d / "land.mp4"),
            "port": make_video(d / "port.mp4", 720, 1280, 3, audio=False),
            "dir": d}


def test_info(vids, capsys):
    assert V.main(["info", str(vids["land"])]) == 0
    assert "1280x720" in capsys.readouterr().out


def test_trim(vids):
    d = vids["dir"]
    assert V.main(["trim", str(vids["land"]), "-ss", "1", "-t", "2", "-o", str(d / "t.mp4")]) == 0
    assert abs(dur(d / "t.mp4") - 2.0) < 0.15
    assert V.main(["trim", str(vids["land"]), "-ss", "0", "-to", "3", "--copy", "-o", str(d / "tc.mp4")]) == 0
    assert 2.5 < dur(d / "tc.mp4") < 3.6


def test_concat_mixed(vids):
    out = vids["dir"] / "cat.mp4"
    # landscape with audio + portrait without audio → normalised & silence padded
    assert V.main(["concat", str(vids["land"]), str(vids["port"]), "-o", str(out)]) == 0
    assert abs(dur(out) - 7.0) < 0.3
    assert V.video_size(str(out)) == (1280, 720) and V.has_audio(str(out))


def test_concat_copy(vids):
    out = vids["dir"] / "catc.mp4"
    assert V.main(["concat", str(vids["land"]), str(vids["land"]), "--copy", "-o", str(out)]) == 0
    assert abs(dur(out) - 8.0) < 0.3


@pytest.mark.parametrize("mode", ["blur", "crop"])
def test_vertical(vids, mode):
    out = vids["dir"] / f"v_{mode}.mp4"
    assert V.main(["vertical", str(vids["land"]), "--mode", mode, "-o", str(out)]) == 0
    assert V.video_size(str(out)) == (1080, 1920)
    assert V.has_audio(str(out))


def test_frames(vids):
    d = vids["dir"] / "frames"
    assert V.main(["frames", str(vids["land"]), "--fps", "2", "-d", str(d)]) == 0
    frames = sorted(d.glob("frame_*.jpg"))
    assert 7 <= len(frames) <= 9
    assert Image.open(frames[0]).size == (1280, 720)


def test_gif(vids):
    out = vids["dir"] / "a.gif"
    assert V.main(["gif", str(vids["land"]), "-t", "2", "--width", "320", "-o", str(out)]) == 0
    im = Image.open(out)
    assert im.format == "GIF" and im.size[0] == 320 and im.n_frames >= 20


SRT = "1\n00:00:00,500 --> 00:00:02,000\n你好，世界！\n\n2\n00:00:02,200 --> 00:00:03,800\n第二句字幕 test\n"


def test_subs_burn_and_mux(vids):
    d = vids["dir"]
    srt = d / "sub.srt"
    srt.write_text(SRT, encoding="utf-8")
    assert V.main(["subs", str(vids["land"]), str(srt), "-o", str(d / "burn.mp4")]) == 0
    # burned frame should differ from original at the bottom centre during a subtitle
    def frame(p, t):
        f = d / f"f_{Path(p).stem}.png"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", str(t), "-i", str(p),
                        "-frames:v", "1", str(f)], check=True)
        return Image.open(f).convert("L")
    a, b = frame(vids["land"], 1.0), frame(d / "burn.mp4", 1.0)
    box = (340, 560, 940, 700)
    diff = sum(abs(x - y) for x, y in zip(a.crop(box).tobytes(), b.crop(box).tobytes()))
    assert diff > 100000
    assert V.main(["subs", str(vids["land"]), str(srt), "--mode", "mux", "-o", str(d / "mux.mp4")]) == 0
    streams = [s["codec_type"] for s in V.probe(str(d / "mux.mp4"))["streams"]]
    assert "subtitle" in streams
    assert V.main(["subs", str(vids["land"]), str(srt), "--mode", "mux", "-o", str(d / "mux.mkv")]) == 0


@pytest.mark.parametrize("fmt", ["mp3", "wav", "m4a"])
def test_audio(vids, fmt):
    out = vids["dir"] / f"a.{fmt}"
    assert V.main(["audio", str(vids["land"]), "-f", fmt, "-o", str(out)]) == 0
    streams = V.probe(str(out))["streams"]
    assert len(streams) == 1 and streams[0]["codec_type"] == "audio"


def test_srt_format():
    s = V.segments_to_srt([{"start": 0, "end": 1.5, "text": " 嗨 "}, {"start": 3661.25, "end": 3662, "text": "x"}])
    assert s.startswith("1\n00:00:00,000 --> 00:00:01,500\n嗨\n")
    assert "01:01:01,250 --> 01:01:02,000" in s


def test_dry_run(vids, capsys):
    assert V.main(["--dry-run", "vertical", str(vids["land"]), "-o", "/nonexistent/x.mp4"]) == 0
    assert "boxblur" in capsys.readouterr().err


JFK = "https://github.com/SYSTRAN/faster-whisper/raw/master/tests/data/jfk.flac"


def test_autosub_real_speech(vids):
    """Optional: needs faster-whisper + network for the tiny model & sample audio."""
    pytest.importorskip("faster_whisper")
    d = vids["dir"]
    flac = d / "jfk.flac"
    try:
        urllib.request.urlretrieve(JFK, flac)
    except Exception as e:
        pytest.skip(f"cannot download speech sample: {e}")
    vid = d / "jfk.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=gray:s=640x360:r=25",
                    "-i", str(flac), "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    str(vid)], check=True)
    try:
        rc = V.main(["autosub", str(vid), "--model", "tiny", "--language", "en", "--device", "cpu", "--burn"])
    except Exception as e:  # model download failure etc.
        pytest.skip(f"whisper model unavailable: {e}")
    assert rc == 0
    text = (d / "jfk.srt").read_text(encoding="utf-8").lower()
    assert "country" in text and "-->" in text
    assert V.video_size(str(d / "jfk_subbed.mp4")) == (640, 360)
