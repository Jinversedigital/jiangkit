import csv
import shutil
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

import jiangkit.experimental as _pkg  # noqa: E402
ROOT = Path(_pkg.__file__).resolve().parent
from jiangkit.experimental import data_sonify as ds  # noqa: E402


def make_csv(path, n=80):
    rng = np.random.default_rng(3)
    t = np.arange(n)
    temp = 20 + 5 * np.sin(t / 8) + rng.normal(0, 0.3, n)
    temp[50] = 60                       # spike anomaly
    sales = 100 + t + rng.normal(0, 2, n)
    sales[20] = -80                     # dip anomaly
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["day", "temp", "sales", "label"])
        for i in range(n):
            w.writerow([f"d{i}", round(temp[i], 2), "" if i == 5 else round(sales[i], 1), "abc"])
    return path


def test_read_and_detect(tmp_path):
    cols, data = ds.read_numeric_csv(str(make_csv(tmp_path / "d.csv")))
    assert cols == ["temp", "sales"]          # text columns dropped
    assert not np.isnan(data).any()           # blank cell filled
    an = ds.find_anomalies(cols, data, 3.0)
    assert {(a.column, a.row) for a in an} == {("temp", 50), ("sales", 20)}
    assert next(a for a in an if a.column == "sales").z < 0


def test_alert_is_audible(tmp_path):
    cols, data = ds.read_numeric_csv(str(make_csv(tmp_path / "d.csv")))
    an = ds.find_anomalies(cols, data, 3.0)
    sr, spr = 8000, 0.1
    with_alerts = ds.render(cols, data, an, spr, sr, True)
    without = ds.render(cols, data, [], spr, sr, True)
    seg = slice(50 * int(spr * sr), 51 * int(spr * sr))
    # high-frequency energy around the anomaly row is larger when the alert plays
    def hf(x):
        spec = np.abs(np.fft.rfft(x.mean(axis=1)))
        freqs = np.fft.rfftfreq(len(x), 1 / sr)
        return spec[freqs > 1200].sum() / (spec.sum() + 1e-9)
    assert hf(with_alerts[seg]) > 2 * hf(without[seg])


def test_pitch_follows_value():
    f = ds.value_to_freq(np.array([0.0, 0.5, 1.0]), 0, quantize=True)
    assert f[0] < f[1] < f[2]


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg missing")
def test_cli_wav_and_synced_video(tmp_path):
    csvp = make_csv(tmp_path / "d.csv")
    wav, mp4 = tmp_path / "o.wav", tmp_path / "o.mp4"
    r = subprocess.run([sys.executable, str(ROOT / "data_sonify.py"), str(csvp), "--wav", str(wav),
                        "--video", str(mp4), "--duration", "6"], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert r.stdout.count("ALERT") == 2
    with wave.open(str(wav)) as w:
        assert w.getnchannels() == 2
        dur = w.getnframes() / w.getframerate()
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                            "-of", "csv=p=0", str(mp4)], capture_output=True, text=True)
    assert abs(float(probe.stdout.strip()) - dur) < 0.3
    # the red playhead must move to the right over time
    xs = []
    for t in (1.0, 4.0):
        png = tmp_path / f"f{t}.png"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", str(t), "-i", str(mp4),
                        "-frames:v", "1", str(png)], check=True)
        a = np.asarray(Image.open(png).convert("RGB")).astype(int)
        red = ((a[:, :, 0] > 200) & (a[:, :, 1] < 70) & (a[:, :, 2] < 70)).sum(axis=0)
        xs.append(int(np.argmax(red)))
    assert xs[1] > xs[0] + 100


def test_help():
    r = subprocess.run([sys.executable, str(ROOT / "data_sonify.py"), "--help"],
                       capture_output=True, text=True)
    assert r.returncode == 0 and "anomalies" in r.stdout
