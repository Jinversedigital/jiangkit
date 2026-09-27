#!/usr/bin/env python3
"""git_symphony.py - turn a git repository's commit history into music.

Mapping rules (the "score"):
  * Each author becomes an instrument (its own MIDI track/channel, GM program,
    and its own synth timbre in the WAV rendering).
  * Lines added  -> pitch (more lines = higher note, quantised to a pentatonic scale).
  * Lines removed -> velocity (deleting code is played louder / more forcefully).
  * The wall-clock gap between consecutive commits -> a rest (log-scaled, capped).
  * "Big refactors" (added+removed above a threshold, or many files touched)
    trigger a chord instead of a single note.

Outputs a standard MIDI file (via mido) and a WAV file rendered by a tiny
additive/subtractive numpy synthesizer (no soundfont needed).
"""
from __future__ import annotations

import argparse
import math
import subprocess  # nosec B404 - subprocess is required to drive external tools; every call uses an argv list
import sys
import wave
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

try:
    import mido
except ImportError:  # pragma: no cover - mido is in the venv
    mido = None

# C-major pentatonic intervals: always sounds consonant regardless of data.
PENTATONIC = [0, 2, 4, 7, 9]
# (GM program number, synth waveform name) per author slot.
INSTRUMENTS = [
    (0, "piano"),      # Acoustic Grand Piano
    (73, "sine"),      # Flute
    (33, "triangle"),  # Electric bass (finger)
    (80, "square"),    # Lead 1 (square)
    (81, "saw"),       # Lead 2 (sawtooth)
    (11, "bell"),      # Vibraphone
    (48, "pad"),       # String ensemble
    (24, "pluck"),     # Nylon guitar
]
# Base octave per slot so voices do not all collide in one register.
BASE_NOTE = [60, 72, 36, 60, 48, 72, 48, 55]


@dataclass
class Commit:
    sha: str
    author: str
    timestamp: int
    added: int = 0
    removed: int = 0
    files: int = 0


@dataclass
class NoteEvent:
    start: float          # seconds
    duration: float       # seconds
    pitches: list[int]    # MIDI note numbers (more than one = chord)
    velocity: int         # 1..127
    author: str
    sha: str
    is_chord: bool = False


@dataclass
class Score:
    events: list[NoteEvent] = field(default_factory=list)
    authors: list[str] = field(default_factory=list)
    length: float = 0.0


def read_history(repo: str, max_commits: int | None = None) -> list[Commit]:
    """Parse `git log --numstat` into Commit objects, oldest first."""
    cmd = ["git", "-C", repo, "log", "--reverse", "--no-merges", "--numstat",
           "--format=@@@%H\t%an\t%at"]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, check=True,  # nosec B603 - argv list without shell; executable and arguments are fixed or validated
                             errors="replace").stdout
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        raise SystemExit(f"error: cannot read git history of {repo!r}: {exc}")
    commits: list[Commit] = []
    cur: Commit | None = None
    for line in out.splitlines():
        if line.startswith("@@@"):
            sha, author, ts = line[3:].split("\t")
            cur = Commit(sha=sha, author=author, timestamp=int(ts))
            commits.append(cur)
        elif line.strip() and cur is not None:
            parts = line.split("\t")
            if len(parts) >= 3:
                # Binary files show "-" for counts; count them as 1 line.
                a = int(parts[0]) if parts[0].isdigit() else 1
                r = int(parts[1]) if parts[1].isdigit() else 1
                cur.added += a
                cur.removed += r
                cur.files += 1
    if max_commits:
        commits = commits[-max_commits:]
    return commits


def scale_note(base: int, magnitude: float, span_degrees: int = 12) -> int:
    """Map a non-negative magnitude to a pentatonic scale degree above base."""
    degree = min(span_degrees - 1, int(round(math.log2(1 + magnitude) * 1.5)))
    octave, step = divmod(degree, len(PENTATONIC))
    return max(0, min(127, base + 12 * octave + PENTATONIC[step]))


def compose(commits: list[Commit], beat: float = 0.25, big_threshold: int = 300,
            big_files: int = 15, max_rest: float = 2.0) -> Score:
    """Turn commits into a Score of timed note events."""
    score = Score()
    t = 0.0
    prev_ts = None
    for c in commits:
        if c.author not in score.authors:
            score.authors.append(c.author)
        slot = score.authors.index(c.author) % len(INSTRUMENTS)
        # Rest: log of the gap in hours, scaled to beats and capped.
        if prev_ts is not None:
            gap_h = max(0.0, (c.timestamp - prev_ts) / 3600.0)
            t += min(max_rest, beat * math.log2(1 + gap_h))
        prev_ts = c.timestamp
        root = scale_note(BASE_NOTE[slot], c.added)
        velocity = int(max(30, min(127, 40 + 18 * math.log2(1 + c.removed))))
        churn = c.added + c.removed
        is_chord = churn >= big_threshold or c.files >= big_files
        if is_chord:
            # Major-ish triad + octave for the biggest refactors.
            pitches = [root, root + 4, root + 7]
            if churn >= 3 * big_threshold:
                pitches.append(root + 12)
            dur = beat * 4
        else:
            pitches = [root]
            dur = beat * (1 + min(3, c.files // 3))
        pitches = [min(127, p) for p in pitches]
        score.events.append(NoteEvent(t, dur, pitches, velocity, c.author,
                                      c.sha, is_chord))
        t += beat
    score.length = max((e.start + e.duration for e in score.events), default=0.0)
    return score


def write_midi(score: Score, path: str, bpm: int = 120) -> None:
    """Write one track per author; times are converted from seconds to ticks."""
    if mido is None:
        raise SystemExit("error: mido is required for MIDI output")
    tpb = 480
    sec_per_tick = 60.0 / bpm / tpb
    mid = mido.MidiFile(ticks_per_beat=tpb)
    meta = mido.MidiTrack()
    meta.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(bpm)))
    meta.append(mido.MetaMessage("track_name", name="git symphony"))
    mid.tracks.append(meta)
    for idx, author in enumerate(score.authors):
        slot = idx % len(INSTRUMENTS)
        channel = idx % 16
        if channel == 9:  # skip the GM drum channel
            channel = 15
        track = mido.MidiTrack()
        track.append(mido.MetaMessage("track_name", name=author[:60]))
        track.append(mido.Message("program_change", program=INSTRUMENTS[slot][0],
                                  channel=channel, time=0))
        # Build absolute-time on/off list then convert to delta times.
        msgs = []
        for e in score.events:
            if e.author != author:
                continue
            on = int(e.start / sec_per_tick)
            off = int((e.start + e.duration) / sec_per_tick)
            for p in e.pitches:
                msgs.append((on, 1, mido.Message("note_on", note=p, velocity=e.velocity,
                                                 channel=channel)))
                msgs.append((off, 0, mido.Message("note_off", note=p, velocity=0,
                                                  channel=channel)))
        msgs.sort(key=lambda m: (m[0], m[1]))
        last = 0
        for tick, _, msg in msgs:
            track.append(msg.copy(time=tick - last))
            last = tick
        mid.tracks.append(track)
    mid.save(path)


def midi_to_hz(n: int) -> float:
    return 440.0 * 2 ** ((n - 69) / 12)


def synth_voice(kind: str, freq: float, dur: float, sr: int) -> np.ndarray:
    """Render a single note with a simple waveform and ADSR-ish envelope."""
    n = max(1, int(dur * sr))
    t = np.arange(n) / sr
    ph = 2 * np.pi * freq * t
    if kind == "sine":
        w = np.sin(ph)
    elif kind == "square":
        w = np.sign(np.sin(ph)) * 0.5
    elif kind == "saw":
        w = 2 * ((freq * t) % 1.0) - 1
        w *= 0.5
    elif kind == "triangle":
        w = 2 * np.abs(2 * ((freq * t) % 1.0) - 1) - 1
    elif kind == "bell":
        w = np.sin(ph) + 0.5 * np.sin(ph * 2.76) + 0.25 * np.sin(ph * 5.4)
        w /= 1.75
    elif kind == "pad":
        w = (np.sin(ph) + np.sin(ph * 1.003) + 0.5 * np.sin(ph * 2)) / 2.5
    elif kind == "pluck":
        w = np.sin(ph) * np.exp(-t * 6) + 0.3 * np.sin(ph * 3) * np.exp(-t * 12)
    else:  # piano-ish: harmonics with fast decay on the upper partials
        w = (np.sin(ph) + 0.5 * np.sin(2 * ph) * np.exp(-t * 3)
             + 0.25 * np.sin(3 * ph) * np.exp(-t * 5)) / 1.75
    # Envelope: 10 ms attack, exponential decay, 30 ms release.
    env = np.exp(-t * (1.5 if kind in ("pad", "sine") else 3.0))
    a = min(n, int(0.01 * sr))
    r = min(n, int(0.03 * sr))
    if a:
        env[:a] *= np.linspace(0, 1, a)
    if r:
        env[-r:] *= np.linspace(1, 0, r)
    return w * env


def render_wav(score: Score, path: str, sr: int = 22050, tail: float = 1.0) -> int:
    """Mix all note events into a mono 16-bit WAV. Returns number of samples."""
    total = int((score.length + tail) * sr) + 1
    buf = np.zeros(total, dtype=np.float64)
    for e in score.events:
        slot = score.authors.index(e.author) % len(INSTRUMENTS)
        kind = INSTRUMENTS[slot][1]
        amp = (e.velocity / 127.0) * 0.3
        start = int(e.start * sr)
        for p in e.pitches:
            v = synth_voice(kind, midi_to_hz(p), e.duration + 0.3, sr) * amp
            end = min(total, start + len(v))
            buf[start:end] += v[: end - start]
    peak = np.max(np.abs(buf)) or 1.0
    buf = buf / peak * 0.9
    pcm = (buf * 32767).astype(np.int16)
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm.tobytes())
    return total


def summary(score: Score, commits: list[Commit]) -> str:
    lines = [f"commits: {len(commits)}  authors: {len(score.authors)}  "
             f"duration: {score.length:.1f}s  chords: {sum(e.is_chord for e in score.events)}"]
    for i, a in enumerate(score.authors):
        prog, kind = INSTRUMENTS[i % len(INSTRUMENTS)]
        n = sum(1 for e in score.events if e.author == a)
        lines.append(f"  {a:<24} -> {kind:<8} (GM {prog:>3})  {n} notes")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Turn a git repository's commit history into music (MIDI + WAV).",
        epilog="Example: git_symphony.py --repo . --midi song.mid --wav song.wav")
    ap.add_argument("--repo", default=".", help="path to git repository (default: .)")
    ap.add_argument("--midi", default="symphony.mid", help="output MIDI path ('' to skip)")
    ap.add_argument("--wav", default="symphony.wav", help="output WAV path ('' to skip)")
    ap.add_argument("--max-commits", type=int, default=None,
                    help="only use the most recent N commits")
    ap.add_argument("--beat", type=float, default=0.25, help="seconds per commit step")
    ap.add_argument("--bpm", type=int, default=120, help="MIDI tempo")
    ap.add_argument("--big-threshold", type=int, default=300,
                    help="added+removed lines that make a commit a chord (refactor)")
    ap.add_argument("--sample-rate", type=int, default=22050)
    args = ap.parse_args(argv)

    commits = read_history(args.repo, args.max_commits)
    if not commits:
        print("no commits found", file=sys.stderr)
        return 1
    score = compose(commits, beat=args.beat, big_threshold=args.big_threshold)
    if args.midi:
        write_midi(score, args.midi, bpm=args.bpm)
    if args.wav:
        render_wav(score, args.wav, sr=args.sample_rate)
    print(summary(score, commits))
    for p in (args.midi, args.wav):
        if p:
            print(f"wrote {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
