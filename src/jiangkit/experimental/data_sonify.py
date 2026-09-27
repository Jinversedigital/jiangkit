#!/usr/bin/env python3
"""data_sonify.py - listen to a CSV file.

Every numeric column becomes a voice with its own register, timbre and
stereo position; the value in each row sets the pitch (optionally snapped to
a pentatonic scale so it stays musical).  Values whose z-score exceeds a
threshold are anomalies: they trigger an audible "alert" (a bright bell ping
plus a short tremolo on that voice) exactly at the moment the row plays.

Outputs a stereo WAV and, if ffmpeg is available, an MP4 video showing the
waveform and the data chart with a moving playhead synchronized to the audio
and red markers on the anomalies.
"""
from __future__ import annotations

import argparse
import csv
import math
import shutil
import subprocess  # nosec B404 - subprocess is required to drive external tools; every call uses an argv list
import sys
import tempfile
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

PENTA = np.array([0, 2, 4, 7, 9])
TIMBRES = ["sine", "triangle", "softsquare", "saw", "organ"]


@dataclass
class Anomaly:
    column: str
    row: int
    value: float
    z: float


# ------------------------------------------------------------------ data
def read_numeric_csv(path: str, min_numeric: float = 0.8):
    """Return (column names, 2-D float array rows x cols) for numeric columns.

    Non-numeric cells in numeric columns become NaN and are filled by
    carrying the previous value forward (then backward)."""
    with open(path, newline="", encoding="utf-8", errors="replace") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        raise SystemExit("error: CSV has no data rows")
    cols = []
    for name in rows[0].keys():
        ok = 0
        for r in rows:
            try:
                float(r[name])
                ok += 1
            except (TypeError, ValueError):
                pass
        if ok / len(rows) >= min_numeric:
            cols.append(name)
    if not cols:
        raise SystemExit("error: no numeric columns found")
    data = np.full((len(rows), len(cols)), np.nan)
    for i, r in enumerate(rows):
        for j, c in enumerate(cols):
            try:
                data[i, j] = float(r[c])
            except (TypeError, ValueError):
                pass
    for j in range(data.shape[1]):  # forward/backward fill
        col = data[:, j]
        mask = np.isnan(col)
        if mask.all():
            col[:] = 0
            continue
        idx = np.where(~mask, np.arange(len(col)), 0)
        np.maximum.accumulate(idx, out=idx)
        col[:] = col[idx]
        first = np.argmax(~np.isnan(col))
        col[:first] = col[first]
    return cols, data


def find_anomalies(cols, data, z_thr: float) -> list[Anomaly]:
    out = []
    mean, std = data.mean(axis=0), data.std(axis=0)
    for j, c in enumerate(cols):
        if std[j] == 0:
            continue
        z = (data[:, j] - mean[j]) / std[j]
        for i in np.where(np.abs(z) >= z_thr)[0]:
            out.append(Anomaly(c, int(i), float(data[i, j]), float(z[i])))
    return sorted(out, key=lambda a: (a.row, a.column))


# ------------------------------------------------------------------ audio
def value_to_freq(norm: np.ndarray, voice: int, quantize: bool) -> np.ndarray:
    base = 45 + 7 * (voice % 5)            # each voice gets its own register
    semis = norm * 24                      # two octaves of range
    if quantize:
        octave = np.floor(semis / 12)
        within = semis - 12 * octave
        snapped = PENTA[np.argmin(np.abs(within[:, None] - PENTA[None, :]), axis=1)]
        semis = 12 * octave + snapped
    return 440.0 * 2 ** ((base + semis - 69) / 12)


def oscillator(kind: str, phase: np.ndarray) -> np.ndarray:
    frac = (phase / (2 * np.pi)) % 1.0
    if kind == "sine":
        return np.sin(phase)
    if kind == "triangle":
        return 2 * np.abs(2 * frac - 1) - 1
    if kind == "softsquare":
        return np.tanh(3 * np.sin(phase)) * 0.7
    if kind == "saw":
        return (2 * frac - 1) * 0.5
    return (np.sin(phase) + 0.4 * np.sin(2 * phase) + 0.2 * np.sin(4 * phase)) / 1.6


def render(cols, data, anomalies, seconds_per_row: float, sr: int, quantize: bool):
    n_rows, n_cols = data.shape
    spr = max(1, int(seconds_per_row * sr))
    total = n_rows * spr + int(0.8 * sr)
    left = np.zeros(total)
    right = np.zeros(total)
    lo, hi = data.min(axis=0), data.max(axis=0)
    span = np.where(hi - lo == 0, 1, hi - lo)
    t_row = np.arange(spr) / sr
    fade = np.minimum(1, np.minimum(t_row, t_row[::-1]) / 0.005)   # 5 ms de-click
    anomaly_rows = {}
    for a in anomalies:
        anomaly_rows.setdefault((a.row, a.column), a)
    for j, c in enumerate(cols):
        norm = (data[:, j] - lo[j]) / span[j]
        freqs = value_to_freq(norm, j, quantize)
        # per-sample frequency with smooth glide; phase accumulation avoids clicks
        f_samples = np.repeat(freqs, spr)
        glide = max(1, int(0.02 * sr))
        kernel = np.ones(glide) / glide
        f_samples = np.convolve(f_samples, kernel, mode="same")
        phase = 2 * np.pi * np.cumsum(f_samples) / sr
        voice = oscillator(TIMBRES[j % len(TIMBRES)], phase) * (0.5 / n_cols)
        # gentle per-row pulse so rows are audible as steps
        voice *= np.tile(0.6 + 0.4 * fade, n_rows)
        for i in range(n_rows):
            if (i, c) in anomaly_rows:
                seg = slice(i * spr, (i + 1) * spr)
                voice[seg] *= 1 + 0.8 * np.sin(2 * np.pi * 12 * t_row)  # tremolo
        pan = 0.5 if n_cols == 1 else j / (n_cols - 1)
        left[: len(voice)] += voice * math.cos(pan * math.pi / 2)
        right[: len(voice)] += voice * math.sin(pan * math.pi / 2)
    # alert pings: bright inharmonic bell, louder for larger |z|
    ping_len = int(0.6 * sr)
    tp = np.arange(ping_len) / sr
    for a in anomalies:
        j = cols.index(a.column)
        f0 = 1760 if a.z > 0 else 1320          # high for spikes, lower for dips
        bell = (np.sin(2 * np.pi * f0 * tp) + 0.6 * np.sin(2 * np.pi * f0 * 2.76 * tp)) \
            * np.exp(-tp * 7) * min(0.5, 0.12 * abs(a.z))
        s = a.row * spr
        e = min(total, s + ping_len)
        pan = 0.5 if n_cols == 1 else j / (n_cols - 1)
        left[s:e] += bell[: e - s] * math.cos(pan * math.pi / 2)
        right[s:e] += bell[: e - s] * math.sin(pan * math.pi / 2)
    stereo = np.stack([left, right], axis=1)
    peak = np.abs(stereo).max() or 1
    return stereo / peak * 0.9


def write_wav(path: str, stereo: np.ndarray, sr: int) -> None:
    pcm = (stereo * 32767).astype(np.int16)
    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())


# ------------------------------------------------------------------ video
def render_background(png: str, cols, data, anomalies, stereo, duration: float,
                      seconds_per_row: float, size=(1280, 720)):
    """Draw waveform + chart; return pixel x-extent (left, right) of the time axis."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    dpi = 100
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(size[0] / dpi, size[1] / dpi), dpi=dpi,
                                   sharex=True, gridspec_kw={"height_ratios": [1, 2]})
    fig.patch.set_facecolor("#111")
    for ax in (ax1, ax2):
        ax.set_facecolor("#1b1b1b")
        ax.tick_params(colors="#bbb")
        for sp in ax.spines.values():
            sp.set_color("#444")
    mono = stereo.mean(axis=1)
    bins = 2000
    chunk = max(1, len(mono) // bins)
    env = np.abs(mono[: chunk * (len(mono) // chunk)]).reshape(-1, chunk).max(axis=1)
    tx = np.arange(len(env)) * chunk / (len(mono) / duration)
    ax1.fill_between(tx, -env, env, color="#4fc3f7", lw=0)
    ax1.set_ylabel("audio", color="#bbb")
    lo, hi = data.min(axis=0), data.max(axis=0)
    span = np.where(hi - lo == 0, 1, hi - lo)
    t = (np.arange(len(data)) + 0.5) * seconds_per_row
    for j, c in enumerate(cols):
        ax2.plot(t, (data[:, j] - lo[j]) / span[j] + j * 1.1, lw=1.4, label=c)
    for a in anomalies:
        j = cols.index(a.column)
        ax2.plot((a.row + 0.5) * seconds_per_row, (a.value - lo[j]) / span[j] + j * 1.1,
                 "x", color="red", ms=11, mew=3)
    ax2.set_xlim(0, duration)
    ax2.set_xlabel("seconds", color="#bbb")
    ax2.set_yticks([])
    ax2.legend(loc="upper left", fontsize=8, facecolor="#222", labelcolor="#ddd")
    fig.suptitle(f"data sonification - {len(cols)} voices, {len(anomalies)} anomalies",
                 color="#eee")
    fig.tight_layout()
    fig.canvas.draw()
    x0 = ax2.transData.transform((0, 0))[0]
    x1 = ax2.transData.transform((duration, 0))[0]
    fig.savefig(png, dpi=dpi, facecolor=fig.get_facecolor())
    plt.close(fig)
    return x0, x1


def make_video(out: str, png: str, wav: str, duration: float, x0: float, x1: float,
               size=(1280, 720), fps: int = 25) -> None:
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg not found")
    from jiangkit.security.proc import media_path

    out, png, wav = media_path(out), media_path(png, must_exist=True), media_path(wav, must_exist=True)
    w, h, fps = int(size[0]), int(size[1]), int(fps)
    x0, x1, duration = float(x0), float(x1), float(duration)
    # a 3-px red playhead slides from x0 to x1 over the audio duration
    filt = (f"[0:v]scale={w}:{h},format=yuv420p[bg];"
            f"color=c=red:s=3x{h}:r={fps}[bar];"
            f"[bg][bar]overlay=x='{x0:.1f}+({x1 - x0:.1f})*t/{duration:.3f}':y=0:shortest=1[v]")
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-framerate", str(fps),
           "-i", png, "-i", wav, "-filter_complex", filt, "-map", "[v]", "-map", "1:a",
           "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-t", f"{duration:.3f}", out]
    r = subprocess.run(cmd, capture_output=True, text=True)  # nosec B603 - argv list, sanitised paths
    if r.returncode != 0:  # fall back to the built-in mpeg4 encoder
        cmd[cmd.index("libx264")] = "mpeg4"
        i = cmd.index("-preset")
        del cmd[i:i + 2]
        r = subprocess.run(cmd, capture_output=True, text=True)  # nosec B603 - argv list without shell; executable and arguments are fixed or validated
        if r.returncode != 0:
            raise RuntimeError(r.stderr)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Listen to a CSV: numeric columns become voices, z-score anomalies "
                    "become audible alerts; outputs WAV + synced chart video.",
        epilog="Example: data_sonify.py sales.csv --wav sales.wav --video sales.mp4 --z 2.5")
    ap.add_argument("csv")
    ap.add_argument("--wav", default="sonified.wav")
    ap.add_argument("--video", default="sonified.mp4", help="MP4 path ('' to skip)")
    ap.add_argument("--duration", type=float, default=15.0,
                    help="target total length in seconds (ignored if --seconds-per-row)")
    ap.add_argument("--seconds-per-row", type=float)
    ap.add_argument("--z", type=float, default=3.0, help="z-score threshold for anomalies")
    ap.add_argument("--no-quantize", action="store_true", help="continuous pitch, no scale")
    ap.add_argument("--columns", help="comma-separated subset of columns to play")
    ap.add_argument("--sample-rate", type=int, default=22050)
    args = ap.parse_args(argv)

    cols, data = read_numeric_csv(args.csv)
    if args.columns:
        want = [c.strip() for c in args.columns.split(",")]
        missing = [c for c in want if c not in cols]
        if missing:
            raise SystemExit(f"error: not numeric/unknown columns: {missing}")
        data = data[:, [cols.index(c) for c in want]]
        cols = want
    spr = args.seconds_per_row or max(0.03, min(1.0, args.duration / len(data)))
    anomalies = find_anomalies(cols, data, args.z)
    stereo = render(cols, data, anomalies, spr, args.sample_rate, not args.no_quantize)
    write_wav(args.wav, stereo, args.sample_rate)
    duration = len(stereo) / args.sample_rate
    print(f"{len(data)} rows x {len(cols)} voices ({', '.join(cols)}), "
          f"{spr * 1000:.0f} ms/row, {duration:.1f}s audio -> {args.wav}")
    for a in anomalies:
        print(f"  ALERT row {a.row:<5} {a.column:<16} value={a.value:<12.4g} z={a.z:+.2f}"
              f"  @ {a.row * spr:.2f}s")
    if args.video:
        with tempfile.TemporaryDirectory() as td:
            png = str(Path(td) / "bg.png")
            x0, x1 = render_background(png, cols, data, anomalies, stereo, duration, spr)
            make_video(args.video, png, args.wav, duration, x0, x1)
        print(f"video -> {args.video}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
