import random
import subprocess
import sys
import time
from pathlib import Path

import jiangkit.experimental as _pkg  # noqa: E402
ROOT = Path(_pkg.__file__).resolve().parent
from jiangkit.experimental import entropy_clock as ec  # noqa: E402


def diff_chars(a, b):
    return sum(x != y for la, lb in zip(a, b) for x, y in zip(la, lb))


def test_clean_face_at_zero():
    rows = ec.clean_face("12:34:56")
    out = ec.messify(rows, 0.0, random.Random(0))
    # padded clean face: the glyph rows appear unchanged in the middle
    assert [r.strip() for r in out[1:6]] == [r.strip() for r in rows]
    assert out[0].strip() == "" and out[-1].strip() == ""


def test_mess_is_monotonic():
    rows = ec.clean_face("08:15:42")
    clean = ec.messify(rows, 0.0, random.Random(0))
    scores = []
    for m in (0.1, 0.4, 0.7, 1.0):
        scores.append(sum(diff_chars(clean, ec.messify(rows, m, random.Random(s)))
                          for s in range(20)))
    assert scores == sorted(scores) and scores[-1] > 5 * scores[0]


def test_metrics_ranges():
    s = ec.Sampler()
    s.sample()
    m = s.sample()
    for v in (m.disk, m.io, m.memory, m.procs, m.proc_entropy, m.churn, m.score()):
        assert 0.0 <= v <= 1.0
    assert m.n_procs > 0


def test_score_weights():
    calm = ec.Metrics(0.2, 0, 0.2, 0.1, 0.3, 0)
    busy = ec.Metrics(0.95, 0.9, 0.95, 0.9, 0.95, 1.0)
    assert calm.score() < 0.1 < 0.8 < busy.score()


def test_smoother_degrades_and_tidies_back():
    sm = ec.Smoother(alpha=0.3, initial=0.1)
    ups = [sm.update(0.9) for _ in range(10)]
    assert ups == sorted(ups) and ups[0] < 0.5 < ups[-1]
    downs = [sm.update(0.1) for _ in range(15)]
    assert downs == sorted(downs, reverse=True) and downs[-1] < 0.15


def test_process_churn_detected():
    s = ec.Sampler()
    s.sample()
    procs = [subprocess.Popen(["sleep", "5"]) for _ in range(12)]
    try:
        m = s.sample()
        assert m.churn >= 0.5
    finally:
        for p in procs:
            p.kill()
            p.wait()


def test_cli_once():
    r = subprocess.run([sys.executable, str(ROOT / "entropy_clock.py"), "--once", "--seed", "3",
                        "--no-color"], capture_output=True, text=True)
    assert r.returncode == 0
    assert "entropy [" in r.stdout and "procs" in r.stdout and "\x1b[" not in r.stdout
    r2 = subprocess.run([sys.executable, str(ROOT / "entropy_clock.py"), "--once", "--mess", "1",
                         "--seed", "3"], capture_output=True, text=True)
    assert "meltdown" in r2.stdout


def test_loop_frames():
    r = subprocess.run([sys.executable, str(ROOT / "entropy_clock.py"), "--frames", "2",
                        "--interval", "0.05", "--no-color"], capture_output=True, text=True, timeout=20)
    assert r.returncode == 0 and r.stdout.count("entropy [") == 2


def test_help():
    r = subprocess.run([sys.executable, str(ROOT / "entropy_clock.py"), "--help"],
                       capture_output=True, text=True)
    assert r.returncode == 0 and "--once" in r.stdout
