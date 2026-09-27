import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

import jiangkit.experimental as _pkg  # noqa: E402
ROOT = Path(_pkg.__file__).resolve().parent
from jiangkit.experimental import code_weather as cw  # noqa: E402

COMPLEX = "def tangled(a, b, c):\n" + "".join(
    f"    {'if' if i == 0 else 'elif'} a == {i} and (b or c):\n        # TODO fix case {i}\n        return {i}\n"
    for i in range(18)) + "    return -1\n"
SIMPLE = "def add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return a - b\n"


def commit(repo, msg, when):
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@x", GIT_COMMITTER_NAME="t",
               GIT_COMMITTER_EMAIL="t@x", GIT_AUTHOR_DATE=f"{int(when)} +0000",
               GIT_COMMITTER_DATE=f"{int(when)} +0000")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, env=env)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", msg], check=True, env=env)


@pytest.fixture()
def repo(tmp_path):
    r = tmp_path / "proj"
    for d in ("stormy", "sunny", "legacy", "tests"):
        (r / d).mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(r)], check=True)
    now = time.time()
    old = now - 500 * 86400
    (r / "sunny" / "mathutil.py").write_text(SIMPLE)
    (r / "legacy" / "oldthing.py").write_text(SIMPLE.replace("add", "legacy_add") * 3)
    (r / "tests" / "test_mathutil.py").write_text("from sunny.mathutil import add\n\ndef test_add():\n    assert add(1, 2) == 3\n")
    commit(r, "old", old)
    for i in range(6):   # heavy recent churn on the complex, untested module
        (r / "stormy" / "engine.py").write_text(COMPLEX + f"\nVERSION = {i}\n" + "x = 1\n" * (i * 10))
        commit(r, f"churn {i}", now - (6 - i) * 86400)
    return r


def test_scan_metrics(repo):
    files = {f.path: f for f in cw.scan(repo, days=90)}
    eng = files["stormy/engine.py"]
    assert eng.max_cc >= 15 and eng.todos == 18 and not eng.tested and eng.churn > 50
    assert files["sunny/mathutil.py"].tested
    assert files["tests/test_mathutil.py"].is_test
    assert files["legacy/oldthing.py"].age_days > 400


def test_forecast_conditions(repo):
    mods = {m.name: m for m in cw.forecast(cw.scan(repo, days=90))}
    assert "tests/" not in mods
    assert mods["stormy/"].condition == "Thunderstorms"
    assert mods["sunny/"].condition == "Sunny"
    assert mods["legacy/"].condition == "Fog"
    assert mods["stormy/"].temp_c > mods["sunny/"].temp_c


def test_ascii_map_shape(repo):
    mods = cw.forecast(cw.scan(repo))
    m = cw.ascii_map(mods, width=60)
    body = m.splitlines()[:-1]
    assert all(len(line) == 60 for line in body)
    assert "/\\" in m and "stormy/" in m


def test_cli_and_html(repo, tmp_path):
    out = tmp_path / "r.html"
    r = subprocess.run([sys.executable, str(ROOT / "code_weather.py"), str(repo), "--html", str(out),
                        "--ascii"], capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert r.returncode == 0, r.stderr
    assert "Thunderstorms" in r.stdout and "umbrella" in r.stdout
    assert r.stdout.isascii()
    h = out.read_text(encoding="utf-8")
    assert h.startswith("<!doctype html>") and "stormy/engine.py" in h and "⛈" in h


def test_no_git_directory(tmp_path):
    (tmp_path / "a.js").write_text("function f(x){ if (x) { return 1 } else { return 2 } } // TODO\n")
    mods = cw.forecast(cw.scan(tmp_path))
    assert mods and mods[0].files[0].todos == 1


def test_help():
    r = subprocess.run([sys.executable, str(ROOT / "code_weather.py"), "--help"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert r.returncode == 0 and "weather forecast" in r.stdout
