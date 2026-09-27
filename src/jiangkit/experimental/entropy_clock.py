#!/usr/bin/env python3
"""entropy_clock.py - a terminal clock that gets messier as your machine does.

The clock face is a big block-digit HH:MM:SS.  Its tidiness is driven by a
"machine entropy" score built from:
  * disk fullness and disk I/O activity
  * memory (and swap) pressure
  * the process table: how many processes exist, how evenly memory is spread
    across them (Shannon entropy) and how many were born/died since the last tick

The score is smoothed (exponential moving average), so the display degrades
gradually under load and tidies itself back as things settle: characters
get replaced by debris, rows drift sideways, pixels drop out, stray particles
float around, and the colour shifts from calm green to alarm red.

--once prints a single snapshot (with the metric breakdown) for scripting/tests.
"""
from __future__ import annotations

import argparse
import math
import random
import shutil
import sys
import time
from dataclasses import dataclass

import psutil

FONT = {
    "0": ["█████", "█   █", "█   █", "█   █", "█████"],
    "1": ["  █  ", " ██  ", "  █  ", "  █  ", " ███ "],
    "2": ["█████", "    █", "█████", "█    ", "█████"],
    "3": ["█████", "    █", " ████", "    █", "█████"],
    "4": ["█   █", "█   █", "█████", "    █", "    █"],
    "5": ["█████", "█    ", "█████", "    █", "█████"],
    "6": ["█████", "█    ", "█████", "█   █", "█████"],
    "7": ["█████", "    █", "   █ ", "  █  ", "  █  "],
    "8": ["█████", "█   █", "█████", "█   █", "█████"],
    "9": ["█████", "█   █", "█████", "    █", "█████"],
    ":": ["     ", "  █  ", "     ", "  █  ", "     "],
}
DEBRIS = "#%&@*+=~:;.,'`^°"


@dataclass
class Metrics:
    disk: float          # 0..1 fullness of /
    io: float            # 0..1 disk I/O activity
    memory: float        # 0..1 memory (+swap) pressure
    procs: float         # 0..1 process count pressure
    proc_entropy: float  # 0..1 normalised Shannon entropy of RSS across processes
    churn: float         # 0..1 process births/deaths since last sample
    n_procs: int = 0

    def score(self) -> float:
        # Subtract "normal idle machine" baselines so a calm box reads as tidy.
        def above(x: float, base: float) -> float:
            return max(0.0, (x - base) / (1 - base))
        s = (0.20 * above(self.disk, 0.3) + 0.10 * self.io + 0.30 * above(self.memory, 0.35)
             + 0.10 * self.procs + 0.15 * above(self.proc_entropy, 0.5) + 0.15 * self.churn)
        return max(0.0, min(1.0, s))


class Sampler:
    """Collects metrics; keeps state between samples for rates and churn."""

    def __init__(self):
        self.prev_pids: set[int] | None = None
        self.prev_io = None
        self.prev_t = None

    def sample(self) -> Metrics:
        now = time.time()
        disk = psutil.disk_usage("/").percent / 100
        io = 0.0
        try:
            cur = psutil.disk_io_counters()
            if cur and self.prev_io is not None:
                dt = max(1e-3, now - self.prev_t)
                rate = ((cur.read_bytes + cur.write_bytes)
                        - (self.prev_io.read_bytes + self.prev_io.write_bytes)) / dt
                io = min(1.0, math.log10(1 + rate) / 8)  # 100 MB/s ~ 1.0
            self.prev_io = cur
        except Exception:  # some containers have no disk counters  # nosec B110 - best-effort optional step; failure is intentionally non-fatal
            pass
        vm = psutil.virtual_memory()
        sw = psutil.swap_memory()
        memory = min(1.0, vm.percent / 100 + 0.5 * sw.percent / 100 * (sw.total > 0))
        rss, pids = [], set()
        for p in psutil.process_iter(["memory_info"]):
            pids.add(p.pid)
            mi = p.info.get("memory_info")
            if mi:
                rss.append(mi.rss)
        n = len(pids)
        total = sum(rss) or 1
        ent = -sum((r / total) * math.log(r / total) for r in rss if r > 0)
        proc_entropy = ent / math.log(len(rss)) if len(rss) > 1 else 0.0
        churn = 0.0
        if self.prev_pids is not None:
            changed = len(pids ^ self.prev_pids)
            churn = min(1.0, changed / 20)
        self.prev_pids, self.prev_t = pids, now
        return Metrics(disk, io, memory, min(1.0, n / 600), proc_entropy, churn, n)


class Smoother:
    """Exponential moving average so the face degrades and recovers gradually."""

    def __init__(self, alpha: float = 0.25, initial: float | None = None):
        self.alpha = alpha
        self.value = initial

    def update(self, x: float) -> float:
        self.value = x if self.value is None else self.value + self.alpha * (x - self.value)
        return self.value


def clean_face(text: str) -> list[str]:
    rows = ["" for _ in range(5)]
    for ch in text:
        glyph = FONT.get(ch, FONT[":"])
        for i in range(5):
            rows[i] += glyph[i] + " "
    return rows


def messify(rows: list[str], mess: float, rng: random.Random, pad: int = 6) -> list[str]:
    """Apply entropy to the clock face. mess=0 returns the clean face (padded)."""
    width = max(len(r) for r in rows) + 2 * pad
    canvas = [[" "] * width for _ in range(len(rows) + 2)]
    jitter = int(round(mess * 5))
    for y, row in enumerate(rows):
        shift = rng.randint(-jitter, jitter) if jitter else 0
        for x, ch in enumerate(row):
            if ch != " ":
                r = rng.random()
                if r < mess * 0.12:
                    ch = " "                          # dropout
                elif r < mess * 0.55:
                    ch = rng.choice(DEBRIS)           # corruption
            xx = x + pad + shift
            if 0 <= xx < width and ch != " ":
                canvas[y + 1][xx] = ch
    # free-floating particles around the face
    n_particles = int(mess ** 1.5 * width * len(canvas) * 0.15)
    for _ in range(n_particles):
        canvas[rng.randrange(len(canvas))][rng.randrange(width)] = rng.choice(DEBRIS)
    # at very high entropy, rows occasionally swap places
    if mess > 0.8 and rng.random() < mess - 0.6:
        a, b = rng.sample(range(1, len(rows) + 1), 2)
        canvas[a], canvas[b] = canvas[b], canvas[a]
    return ["".join(r) for r in canvas]


def color_for(mess: float) -> str:
    if mess < 0.35:
        return "\x1b[32m"   # green
    if mess < 0.6:
        return "\x1b[33m"   # yellow
    if mess < 0.8:
        return "\x1b[38;5;208m"  # orange
    return "\x1b[31m"       # red


def mood(mess: float) -> str:
    for thr, word in ((0.2, "serene"), (0.35, "tidy"), (0.5, "restless"), (0.65, "cluttered"),
                      (0.8, "chaotic")):
        if mess < thr:
            return word
    return "entropic meltdown"


def frame(now: time.struct_time, mess: float, m: Metrics | None, rng: random.Random,
          color: bool, show_seconds: bool = True) -> str:
    text = time.strftime("%H:%M:%S" if show_seconds else "%H:%M", now)
    lines = messify(clean_face(text), mess, rng)
    bar_w = 30
    bar = "#" * int(round(mess * bar_w)) + "-" * (bar_w - int(round(mess * bar_w)))
    info = [f"entropy [{bar}] {mess:.2f}  ({mood(mess)})"]
    if m:
        info.append(f"disk {m.disk:.0%} io {m.io:.2f} | mem {m.memory:.0%} | procs {m.n_procs} "
                    f"(spread {m.proc_entropy:.2f}, churn {m.churn:.2f})")
    body = "\n".join(lines + [""] + info)
    return f"{color_for(mess)}{body}\x1b[0m" if color else body


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Terminal clock that gets visually messier as disk, memory and process "
                    "entropy grow, and tidies back as the machine settles.",
        epilog="Examples: entropy_clock.py   |   entropy_clock.py --once --no-color")
    ap.add_argument("--once", action="store_true", help="print one snapshot and exit")
    ap.add_argument("--mess", type=float, help="force the messiness (0..1) instead of measuring")
    ap.add_argument("--interval", type=float, default=1.0, help="seconds between frames")
    ap.add_argument("--alpha", type=float, default=0.25, help="smoothing factor (0..1)")
    ap.add_argument("--seed", type=int, help="random seed for reproducible frames")
    ap.add_argument("--no-color", action="store_true")
    ap.add_argument("--no-seconds", action="store_true")
    ap.add_argument("--frames", type=int, default=0, help="stop after N frames (0 = forever)")
    args = ap.parse_args(argv)

    rng = random.Random(args.seed)  # nosec B311 - non-cryptographic randomness (timing jitter / visuals), not security relevant
    sampler = Sampler()
    smooth = Smoother(args.alpha)
    color = not args.no_color and sys.stdout.isatty()
    if args.once:
        sampler.sample()
        time.sleep(0.2)          # second sample gives meaningful rates/churn
        m = sampler.sample()
        mess = args.mess if args.mess is not None else m.score()
        print(frame(time.localtime(), mess, m, rng, color and not args.no_color, not args.no_seconds))
        return 0
    out = sys.stdout
    out.write("\x1b[?25l")  # hide cursor
    n = 0
    try:
        while True:
            m = sampler.sample()
            mess = args.mess if args.mess is not None else smooth.update(m.score())
            cols = shutil.get_terminal_size().columns
            text = frame(time.localtime(), mess, m, rng, color, not args.no_seconds)
            clipped = "\n".join(l[:cols] for l in text.split("\n"))
            out.write("\x1b[H\x1b[2J" + clipped + "\n")
            out.flush()
            n += 1
            if args.frames and n >= args.frames:
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass
    finally:
        out.write("\x1b[0m\x1b[?25h\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
