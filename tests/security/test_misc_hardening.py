"""Smaller hardening checks: PowerShell quoting, webhook schemes, subprocess usage, exit codes."""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from jiangkit.system import sys_tools

from ._util import jiang

SRC = Path(__file__).resolve().parents[2] / "src" / "jiangkit"


@pytest.mark.parametrize("s", ["$(Remove-Item C:\\ -Recurse)", "`whoami`", "a'; calc; '", "${env:PATH}"])
def test_powershell_literal_quoting(s):
    q = sys_tools._ps_quote(s)
    assert q.startswith("'") and q.endswith("'")
    inner = q[1:-1]
    assert "'" not in inner.replace("''", "")  # every quote doubled -> literal string, no expansion


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://x/", "gopher://x/", "javascript:x"])
def test_webhook_rejects_non_http(url):
    with pytest.raises(RuntimeError):
        sys_tools.post_json(url, {})


def test_shell_true_only_where_justified():
    """shell=True may only appear with an inline justification (nosec + reason)."""
    for f in SRC.rglob("*.py"):
        for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"\bshell\s*=\s*True\s*[,)]", line) and not line.lstrip().startswith(("#", "*")):
                assert "nosec" in line or "nosemgrep" in line, f"{f}:{i}"


def test_no_os_system_or_popen_strings():
    for f in SRC.rglob("*.py"):
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in ("system", "popen") and getattr(node.func.value, "id", "") == "os":
                    raise AssertionError(f"os.{node.func.attr} in {f}:{node.lineno}")


def test_exit_codes():
    assert jiang("nope-category").returncode == 2
    assert jiang("version").returncode == 0
    assert jiang("list", "--json").returncode == 0


def test_bilingual_help():
    out = jiang("--help").stdout
    assert any("\u4e00" <= ch <= "\u9fff" for ch in out) and any(c.isascii() and c.isalpha() for c in out)
