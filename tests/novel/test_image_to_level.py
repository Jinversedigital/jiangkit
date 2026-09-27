import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

import jiangkit.experimental as _pkg  # noqa: E402
ROOT = Path(_pkg.__file__).resolve().parent
from jiangkit.experimental import image_to_level as il  # noqa: E402


def make_photo(path, seed=0):
    rng = np.random.default_rng(seed)
    h, w = 200, 320
    y, x = np.mgrid[0:h, 0:w]
    img = 120 + 60 * np.sin(x / 25) * np.cos(y / 30) + rng.normal(0, 5, (h, w))
    img[60:64, 40:140] = 250
    img[120:124, 150:280] = 240
    img[150:200, 100:160] = 5          # dark block -> hazards / lava
    for cx, cy in [(50, 40), (200, 90), (280, 30)]:
        img[(x - cx) ** 2 + (y - cy) ** 2 < 30] = 255  # bright spots -> coins
    Image.fromarray(img.clip(0, 255).astype("uint8")).save(path)
    return path


def test_generation_features(tmp_path):
    gray = il.load_gray(str(make_photo(tmp_path / "p.png")), 78, 22)
    grid = il.build_level(gray)
    flat = "".join("".join(r) for r in grid)
    assert flat.count("=") > 10
    assert flat.count("o") >= 3
    assert flat.count("^") + flat.count("~") >= 1
    assert flat.count("S") == 1 and flat.count("E") == 1
    assert len(grid) == 22 and all(len(r) == 78 for r in grid)


@pytest.mark.parametrize("seed", range(4))
def test_always_playable(tmp_path, seed):
    gray = il.load_gray(str(make_photo(tmp_path / "p.png", seed)), 60, 20)
    grid, _ = il.make_playable(il.build_level(gray))
    res = il.headless(il.Game(grid), verbose=False)
    assert res["solvable"] and res["won"]


@pytest.mark.parametrize("value", [0, 255])
def test_degenerate_images_playable(tmp_path, value):
    p = tmp_path / "flat.png"
    Image.new("L", (100, 60), value).save(p)
    grid, _ = il.make_playable(il.build_level(il.load_gray(str(p), 40, 15)))
    assert il.headless(il.Game(grid), verbose=False)["won"]


def _mini(rows):
    return [list(r) for r in rows]


def test_engine_physics():
    g = il.Game(_mini([
        "##########",
        "#        #",
        "#        #",
        "#   o    #",
        "#S  ^   E#",
        "##########",
    ]))
    s = g.new_state()
    g.step(s, 1, False)
    assert (s.x, s.y) == (2, 4)
    g.step(s, 0, True)                 # jump: rises one cell per tick
    assert s.y == 3
    g.step(s, 0, False)
    g.step(s, 0, False)
    assert s.y == 1                    # peak = JUMP cells above ground
    for _ in range(5):
        g.step(s, 0, False)
    assert s.y == 4                    # gravity brings us back
    g.step(s, 1, False)                # walk into spike column
    g.step(s, 1, False)
    assert s.dead


def test_coin_and_win_via_solver():
    g = il.Game(_mini([
        "##########",
        "#        #",
        "#    o   #",
        "#   ===  #",
        "#S   ^  E#",
        "##########",
    ]))
    path, _ = g.solve()
    assert path
    s = g.new_state()
    for a in path:
        g.step(s, *a)
    assert s.won and not s.dead


def test_cli_headless_and_save(tmp_path):
    photo = make_photo(tmp_path / "p.png")
    out = tmp_path / "level.txt"
    r = subprocess.run([sys.executable, str(ROOT / "image_to_level.py"), str(photo),
                        "--headless", "--save", str(out)], capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "SOLVED" in r.stdout
    assert out.read_text().count("\n") == 22


def test_help():
    r = subprocess.run([sys.executable, str(ROOT / "image_to_level.py"), "--help"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert r.returncode == 0 and "platformer" in r.stdout


def test_curses_smoke_in_pty(tmp_path):
    """Start the real curses game in a pseudo-terminal, press keys, quit."""
    import os
    import pty
    import select
    import time
    photo = make_photo(tmp_path / "p.png")
    pid, fd = pty.fork()
    if pid == 0:  # child
        os.environ["TERM"] = "xterm"
        os.environ["LINES"], os.environ["COLUMNS"] = "30", "100"
        os.execv(sys.executable, [sys.executable, str(ROOT / "image_to_level.py"), str(photo)])
    screen = b""
    def pump(t):
        nonlocal screen
        end = time.time() + t
        while time.time() < end:
            r, _, _ = select.select([fd], [], [], 0.05)
            if r:
                try:
                    screen += os.read(fd, 65536)
                except OSError:
                    return
    pump(1.5)
    for k in (b"d", b"d", b"w", b"d"):
        os.write(fd, k)
        pump(0.2)
    os.write(fd, b"q")
    pump(1.0)
    _, status = os.waitpid(pid, 0)
    assert os.WEXITSTATUS(status) == 0
    assert b"coins" in screen and b"@" in screen
