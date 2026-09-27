#!/usr/bin/env python3
"""image_to_level.py - turn any photo into a playable terminal platformer.

How a picture becomes a level:
  * Horizontal edges (Sobel, vertical-gradient dominant)  -> platforms  '='
  * Dark regions sitting on a surface                     -> spikes     '^'
  * Very dark columns at the bottom                       -> lava pits  '~'
  * Bright local maxima                                   -> coins      'o'
  * A floor/wall frame '#', start 'S' on the left, exit 'E' on the right.

The same deterministic physics engine drives the curses game, the headless
test mode and a BFS solver.  After generation the solver checks that the
exit is reachable; if not, the level is repaired step by step (hazards
cleared, stepping stones added) until it is, so every photo yields a
beatable level.

Controls (curses): a/d or arrows = move, w/space/up = jump, q = quit.
"""
from __future__ import annotations

import argparse
import sys
import time
from collections import deque
from dataclasses import dataclass, field

import numpy as np
from PIL import Image

SOLID = set("#=")
HAZARD = set("^~")
JUMP = 3  # cells of jump height

# action = (dx, jump)
ACTIONS = [(dx, j) for dx in (-1, 0, 1) for j in (False, True)]


# ------------------------------------------------------------------ generation
def load_gray(path: str, width: int, height: int) -> np.ndarray:
    img = Image.open(path).convert("L").resize((width, height), Image.LANCZOS)
    return np.asarray(img, dtype=np.float64)


def sobel(g: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    p = np.pad(g, 1, mode="edge")
    gx = (p[:-2, 2:] + 2 * p[1:-1, 2:] + p[2:, 2:]) - (p[:-2, :-2] + 2 * p[1:-1, :-2] + p[2:, :-2])
    gy = (p[2:, :-2] + 2 * p[2:, 1:-1] + p[2:, 2:]) - (p[:-2, :-2] + 2 * p[:-2, 1:-1] + p[:-2, 2:])
    return gx, gy


def build_level(gray: np.ndarray, edge_pct: float = 85, dark_pct: float = 12,
                bright_pct: float = 95, max_coins: int = 25) -> list[list[str]]:
    h, w = gray.shape
    grid = [[" "] * w for _ in range(h)]
    gx, gy = sobel(gray)
    mag = np.hypot(gx, gy)
    thr = max(np.percentile(mag, edge_pct), 1e-6)
    horiz = (mag >= thr) & (np.abs(gy) >= np.abs(gx))
    # platforms: horizontal edges, thinned (no platform directly under another)
    for y in range(2, h - 3):
        for x in range(1, w - 1):
            if horiz[y, x] and grid[y - 1][x] != "=":
                grid[y][x] = "="
    # drop isolated single platform cells - they are unreadable noise
    for y in range(h):
        for x in range(1, w - 1):
            if grid[y][x] == "=" and grid[y][x - 1] != "=" and grid[y][x + 1] != "=":
                grid[y][x] = " "
    # frame
    for x in range(w):
        grid[h - 1][x] = "#"
        grid[0][x] = "#"
    for y in range(h):
        grid[y][0] = grid[y][w - 1] = "#"
    dark_thr = min(np.percentile(gray, dark_pct), 90)
    # lava: columns whose bottom band is very dark
    bottom = gray[int(h * 0.8):, :].mean(axis=0)
    for x in range(4, w - 4):
        if bottom[x] <= dark_thr:
            grid[h - 1][x] = "~"
    # spikes: dark cells resting on a solid surface
    for y in range(1, h - 1):
        for x in range(1, w - 1):
            if grid[y][x] == " " and gray[y, x] <= dark_thr and grid[y + 1][x] in SOLID:
                grid[y][x] = "^"
    # coins: bright local maxima in free space
    bright_thr = np.percentile(gray, bright_pct)
    cands = []
    for y in range(1, h - 1):
        for x in range(1, w - 1):
            v = gray[y, x]
            if grid[y][x] == " " and v >= bright_thr and v >= gray[y - 1:y + 2, x - 1:x + 2].max():
                cands.append((v, y, x))
    for _, y, x in sorted(cands, reverse=True)[:max_coins]:
        grid[y][x] = "o"
    # start and exit pads (cleared surroundings)
    for (sx, ch) in ((2, "S"), (w - 3, "E")):
        for yy in range(h - 5, h - 1):
            for xx in (sx - 1, sx, sx + 1):
                grid[yy][xx] = " "
        for xx in (sx - 1, sx, sx + 1):
            grid[h - 1][xx] = "#"
        grid[h - 2][sx] = ch
    return grid


# ------------------------------------------------------------------ engine
@dataclass
class State:
    x: int
    y: int
    vy: int = 0
    coins: int = 0
    dead: bool = False
    won: bool = False
    taken: set = field(default_factory=set)


class Game:
    def __init__(self, grid: list[list[str]]):
        self.grid = [row[:] for row in grid]
        self.h, self.w = len(grid), len(grid[0])
        self.start = self.exit = None
        for y, row in enumerate(grid):
            for x, c in enumerate(row):
                if c == "S":
                    self.start = (x, y)
                elif c == "E":
                    self.exit = (x, y)
        if not self.start or not self.exit:
            raise ValueError("level needs S and E")
        self.coins_total = sum(r.count("o") for r in grid)

    def cell(self, x: int, y: int) -> str:
        if 0 <= x < self.w and 0 <= y < self.h:
            return self.grid[y][x]
        return "#"

    def solid(self, x, y) -> bool:
        return self.cell(x, y) in SOLID

    def new_state(self) -> State:
        return State(*self.start)

    def physics(self, x, y, vy, dx, jump):
        """Pure physics step: returns new (x, y, vy). Shared by game and solver."""
        on_ground = self.solid(x, y + 1)
        if jump and on_ground:
            vy = -JUMP
        if dx and not self.solid(x + dx, y):
            x += dx
        if vy < 0:
            if self.solid(x, y - 1):
                vy = 0
            else:
                y -= 1
                vy += 1
        elif not self.solid(x, y + 1):
            y += 1
            vy = 0
        else:
            vy = 0
        return x, y, vy

    def step(self, s: State, dx: int, jump: bool) -> State:
        if s.dead or s.won:
            return s
        s.x, s.y, s.vy = self.physics(s.x, s.y, s.vy, dx, jump)
        c = self.cell(s.x, s.y)
        if c in HAZARD:
            s.dead = True
        elif c == "o" and (s.x, s.y) not in s.taken:
            s.taken.add((s.x, s.y))
            s.coins += 1
        elif (s.x, s.y) == self.exit:
            s.won = True
        return s

    def solve(self, max_states: int = 400_000):
        """BFS over (x, y, vy). Returns (action list, set of reached cells)."""
        start = (*self.start, 0)
        prev = {start: None}
        q = deque([start])
        reached = set()
        while q and len(prev) < max_states:
            st = q.popleft()
            reached.add(st[:2])
            if st[:2] == self.exit:
                path = []
                while prev[st] is not None:
                    st, a = prev[st]
                    path.append(a)
                return path[::-1], reached
            for a in ACTIONS:
                nx, ny, nvy = self.physics(*st, *a)
                if self.cell(nx, ny) in HAZARD:
                    continue
                ns = (nx, ny, nvy)
                if ns not in prev:
                    prev[ns] = (st, a)
                    q.append(ns)
        return None, reached

    def render(self, s: State) -> list[str]:
        rows = []
        for y, row in enumerate(self.grid):
            line = ["." if (x, y) in s.taken else c for x, c in enumerate(row)]
            line = [" " if c == "." else c for c in line]
            if y == s.y:
                line[s.x] = "X" if s.dead else "@"
            rows.append("".join(line))
        return rows


def make_playable(grid: list[list[str]], max_fixes: int = 200) -> tuple[list[list[str]], int]:
    """Repair the level until BFS can reach the exit. Returns (grid, fixes)."""
    fixes = 0
    h, w = len(grid), len(grid[0])
    while fixes < max_fixes:
        g = Game(grid)
        path, reached = g.solve()
        if path is not None:
            return grid, fixes
        fixes += 1
        # frontier: the right-most column we could reach, lowest cell there
        fx = max(x for x, _ in reached)
        fy = max(y for x, y in reached if x == fx)
        changed = False
        # 1) remove hazards / low obstacles in the next few columns near the frontier
        for x in range(fx + 1, min(w - 1, fx + 4)):
            for y in range(max(1, fy - JUMP), h - 1):
                if grid[y][x] in HAZARD or (grid[y][x] == "=" and y >= fy - 1):
                    grid[y][x] = " "
                    changed = True
            if grid[h - 1][x] == "~":
                grid[h - 1][x] = "#"
                changed = True
        if not changed:
            # 2) add a stepping stone below/ahead of the frontier
            y = min(h - 2, fy + 1)
            x = min(w - 2, fx + 1)
            if grid[y][x] == " ":
                grid[y][x] = "="
            else:
                # 3) last resort: carve a floor corridor to the exit
                for xx in range(1, w - 1):
                    grid[h - 1][xx] = "#"
                    for yy in range(h - 1 - JUMP - 1, h - 1):
                        if grid[yy][xx] not in "SE":
                            grid[yy][xx] = " "
    return grid, fixes


# ------------------------------------------------------------------ front ends
def headless(game: Game, verbose: bool = True) -> dict:
    path, _ = game.solve()
    result = {"solvable": path is not None, "steps": len(path) if path else 0,
              "coins_total": game.coins_total, "coins": 0, "won": False}
    if path:
        s = game.new_state()
        for dx, j in path:
            game.step(s, dx, j)
        result.update(coins=s.coins, won=s.won, dead=s.dead)
        if verbose:
            print("\n".join(game.render(s)))
    return result


def play_curses(game: Game) -> None:  # pragma: no cover - interactive
    import curses

    def loop(scr):
        curses.curs_set(0)
        scr.nodelay(True)
        s = game.new_state()
        while True:
            dx, jump = 0, False
            while True:
                k = scr.getch()
                if k == -1:
                    break
                if k in (ord("q"), 27):
                    return
                if k in (ord("a"), curses.KEY_LEFT):
                    dx = -1
                elif k in (ord("d"), curses.KEY_RIGHT):
                    dx = 1
                elif k in (ord("w"), ord(" "), curses.KEY_UP):
                    jump = True
                elif k == ord("r"):
                    s = game.new_state()
            game.step(s, dx, jump)
            scr.erase()
            mh, mw = scr.getmaxyx()
            for y, line in enumerate(game.render(s)[: mh - 2]):
                scr.addstr(y, 0, line[: mw - 1])
            status = f"coins {s.coins}/{game.coins_total}  a/d move  w jump  r restart  q quit"
            if s.dead:
                status = "You hit a hazard!  r = restart, q = quit"
            if s.won:
                status = f"You escaped with {s.coins} coins!  q = quit"
            scr.addstr(min(game.h, mh - 2), 0, status[: mw - 1])
            scr.refresh()
            time.sleep(0.08)

    curses.wrapper(loop)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Turn a photo into a playable terminal platformer level.",
        epilog="Example: image_to_level.py photo.jpg   |   image_to_level.py photo.jpg --headless")
    ap.add_argument("image", help="input image (any format Pillow reads)")
    ap.add_argument("--width", type=int, default=78)
    ap.add_argument("--height", type=int, default=22)
    ap.add_argument("--headless", action="store_true",
                    help="no curses: print level, auto-solve with the engine, report stats")
    ap.add_argument("--save", help="write the level as a text file")
    ap.add_argument("--edge-pct", type=float, default=85, help="edge percentile for platforms")
    args = ap.parse_args(argv)

    gray = load_gray(args.image, args.width, args.height)
    grid = build_level(gray, edge_pct=args.edge_pct)
    grid, fixes = make_playable(grid)
    game = Game(grid)
    counts = {c: sum(r.count(c) for r in grid) for c in "=^~o"}
    if args.save:
        with open(args.save, "w") as f:
            f.write("\n".join("".join(r) for r in grid) + "\n")
    if args.headless:
        print("\n".join("".join(r) for r in grid))
        print(f"\nplatforms={counts['=']} spikes={counts['^']} lava={counts['~']} "
              f"coins={counts['o']} repairs={fixes}")
        res = headless(game, verbose=False)
        state = "SOLVED" if res["won"] else "UNSOLVED"
        print(f"{state}: exit reached in {res['steps']} ticks by the solver, "
              f"coins collected on the way {res['coins']}/{res['coins_total']}")
        return 0 if res["won"] else 1
    play_curses(game)
    return 0


if __name__ == "__main__":
    sys.exit(main())
