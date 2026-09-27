import os
import subprocess
import sys
from pathlib import Path

import pytest

import jiangkit  # noqa: E402

PKG = Path(jiangkit.__file__).resolve().parent
TOOLS = {"doc_tools": "docs", "text_tools": "docs", "data_tools": "data", "file_tools": "files",
         "social_tools": "social", "sys_tools": "system"}


@pytest.fixture
def cli(tmp_path, monkeypatch):
    """Run a toolkit2 CLI as a subprocess; returns CompletedProcess."""
    env = dict(os.environ, TOOLKIT2_HOME=str(tmp_path / "home"),
               TOOLKIT2_HASHTAGS=str(tmp_path / "home" / "hashtags.json"),
               MPLBACKEND="Agg")

    def run(tool, *args, check=True, input=None, extra_env=None):
        e = dict(env, **(extra_env or {}))
        script = str(PKG / TOOLS[tool] / f"{tool}.py")
        argv = [sys.executable, script]
        res = subprocess.run([*argv, *map(str, args)],
                             capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=tmp_path, env=e, input=input)
        if check and res.returncode != 0:
            raise AssertionError(f"{tool} {args} failed ({res.returncode}):\n"
                                 f"{res.stdout}\n{res.stderr}")
        return res
    return run
