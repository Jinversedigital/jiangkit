import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

import jiangkit.experimental as _pkg  # noqa: E402
ROOT = Path(_pkg.__file__).resolve().parent
SCRIPT = ROOT / "shell_timemachine" / "shell_timemachine.py"
pass  # module imported from the jiangkit package
from jiangkit.experimental.shell_timemachine import shell_timemachine as tm  # noqa: E402


def run_tm(tm_home, *args, cwd=None, env_extra=None):
    env = dict(os.environ, TM_HOME=str(tm_home), **(env_extra or {}))
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True,
                          text=True, encoding="utf-8", errors="replace", cwd=cwd, env=env)


def wait_rows(db_path, n, timeout=10):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if db_path.exists():
            with sqlite3.connect(db_path) as db:
                try:
                    c = db.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
                except sqlite3.OperationalError:
                    c = 0
            if c >= n:
                return c
        time.sleep(0.1)
    return -1


def test_help():
    r = subprocess.run([sys.executable, str(SCRIPT), "--help"], capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert r.returncode == 0 and "last worked" in r.stdout


def test_wrapper_output_diff(tmp_path):
    home, work = tmp_path / "h", tmp_path / "w"
    work.mkdir()
    (work / "cfg.txt").write_text("mode=fast\n")
    assert run_tm(home, "run", "--", "cat cfg.txt", cwd=work).returncode == 0
    (work / "cfg.txt").write_text("mode=broken\n")
    (work / "new.lock").write_text("x")
    r = run_tm(home, "run", "--", "cat cfg.txt; exit 3", cwd=work)
    assert r.returncode == 3
    # different command text -> no earlier success of *that* command
    assert "no earlier successful run" in run_tm(home, "diff").stdout
    (work / "cfg.txt").write_text("mode=slow\n")
    run_tm(home, "run", "--", "cat cfg.txt", cwd=work)
    (work / "cfg.txt").unlink()
    r = run_tm(home, "run", "-q", "--", "cat cfg.txt", cwd=work)
    assert r.returncode != 0
    out = run_tm(home, "diff").stdout
    assert "-mode=slow" in out
    assert "- cfg.txt" in out and "+ new.lock" not in out.split("[files]")[0]
    log = run_tm(home, "log").stdout
    assert log.count("cat cfg.txt") == 4


def test_rerun_env_and_secret_masking(tmp_path):
    home, work = tmp_path / "h", tmp_path / "w"
    work.mkdir()
    fake_bin = tmp_path / "bin1"
    fake_bin.mkdir()
    tool = fake_bin / "mytool"
    tool.write_text("#!/bin/sh\necho version 1\n")
    tool.chmod(0o755)
    path1 = f"{fake_bin}:{os.environ['PATH']}"
    run_tm(home, "run", "--", "mytool", cwd=work,
           env_extra={"PATH": path1, "API_TOKEN": "s3cret", "MODE": "a"})
    # now a new mytool shadows the old one and env changed
    bin2 = tmp_path / "bin2"
    bin2.mkdir()
    (bin2 / "mytool").write_text("#!/bin/sh\necho version 2\nexit 1\n")
    (bin2 / "mytool").chmod(0o755)
    path2 = f"{bin2}:{path1}"
    env2 = {"PATH": path2, "API_TOKEN": "other", "MODE": "b"}
    run_tm(home, "run", "-q", "--", "mytool", cwd=work, env_extra=env2)
    out = run_tm(home, "diff", "mytool", "--rerun", cwd=work, env_extra=env2).stdout
    assert "[binary]" in out and "bin2" in out
    assert "MODE: 'a' -> 'b'" in out
    assert "s3cret" not in out
    assert "API_TOKEN" in out and "sha256" in out
    assert "+version 2" in out and "PATH: +[" in out
    with sqlite3.connect(home / "history.db") as db:
        envs = [r[0] for r in db.execute("SELECT env_json FROM runs")]
    assert all("s3cret" not in e for e in envs)


def test_record_and_files_file(tmp_path):
    home = tmp_path / "h"
    envf = tmp_path / "env"
    envf.write_bytes(b"A=1\0B=two\0PWD=/x\0")
    ff = tmp_path / "files"
    ff.write_text("a.txt\t10\t100\tregular file\nsub\t4096\t100\tdirectory\n")
    r = run_tm(home, "record", "--cmd", "make", "--cwd", str(tmp_path), "--exit", "0",
               "--env-file", str(envf), "--files-file", str(ff), "--ts", "1000")
    assert r.returncode == 0 and not envf.exists() and not ff.exists()
    with sqlite3.connect(home / "history.db") as db:
        env_json, files_json = db.execute("SELECT env_json, files_json FROM runs").fetchone()
    assert json.loads(env_json) == {"A": "1", "B": "two"}
    assert set(json.loads(files_json)) == {"a.txt", "sub/"}


SESSION = ("cd {proj}\necho v1 > data.txt\ncat data.txt\nexport FOO=1\n\n"
           "cat data.txt\nrm data.txt\nexport FOO=2\ncat data.txt\nexit\n")


def _check_session(home):
    assert wait_rows(home / "history.db", 8) >= 8
    time.sleep(0.3)
    log = run_tm(home, "log").stdout
    assert "[E1 ]" in log and log.count("cat data.txt") == 3
    out = run_tm(home, "diff").stdout
    assert "FOO: '1' -> '2'" in out
    assert "- data.txt" in out


def test_bash_hook_integration(tmp_path):
    home, proj = tmp_path / "h", tmp_path / "proj"
    proj.mkdir()
    rc = tmp_path / "rc.bash"
    rc.write_text(run_tm(home, "hook", "bash").stdout)
    env = dict(os.environ, TM_HOME=str(home), HOME=str(tmp_path))
    subprocess.run(["bash", "--rcfile", str(rc), "-i"], input=SESSION.format(proj=proj),
                   text=True, encoding="utf-8", errors="replace", capture_output=True, env=env, timeout=30)
    _check_session(home)


def _find_zsh():
    return shutil.which("zsh") or (str(Path(__file__).resolve().parent / "_tools" / "zsh")
                                   if (Path(__file__).resolve().parent / "_tools" / "zsh").exists() else None)


@pytest.mark.skipif(_find_zsh() is None, reason="zsh not available")
def test_zsh_hook_integration(tmp_path):
    home, proj = tmp_path / "h", tmp_path / "proj"
    proj.mkdir()
    rc = tmp_path / "rc.zsh"
    rc.write_text(run_tm(home, "hook", "zsh").stdout)
    env = dict(os.environ, TM_HOME=str(home), HOME=str(tmp_path))
    subprocess.run([_find_zsh(), "-f", "-i"], input=f"source {rc}\n" + SESSION.format(proj=proj),
                   text=True, encoding="utf-8", errors="replace", capture_output=True, env=env, timeout=30)
    _check_session(home)
