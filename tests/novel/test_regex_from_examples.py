import random
import re
import subprocess
import sys
from pathlib import Path

import pytest

import jiangkit.experimental as _pkg  # noqa: E402
ROOT = Path(_pkg.__file__).resolve().parent
from jiangkit.experimental import regex_from_examples as rx  # noqa: E402

CASES = [
    (["2026-09-27", "1999-01-05", "2001-12-31"], ["2026/09/27", "26-9-27", "2026-9-27"]),
    (["A-1234", "B-99", "Z-5"], ["a-12", "A1234", "AB-12", "A-"]),
    (["#ff00aa", "#0A0B0C", "#123456"], ["ff00aa", "#12345", "#GGGGGG"]),
    (["v1.2.3", "v10.0.1", "v0.9.12"], ["1.2.3", "v1.2", "v1..3"]),
    (["INV-2026-001", "INV-2025-120"], ["inv-2026-001", "INV-26-001", "INV2026001"]),
]


@pytest.mark.parametrize("pos,neg", CASES)
def test_synthesis_is_consistent(pos, neg):
    pat, acc = rx.synthesize(pos, neg, seed=1)
    r = re.compile(pat)
    assert acc == 1.0
    assert all(r.fullmatch(p) for p in pos)
    assert not any(r.fullmatch(n) for n in neg)


def test_prefers_structure_over_memorising():
    pat, _ = rx.synthesize(*CASES[0], seed=0)
    assert pat == r"\d{4}-\d{2}-\d{2}"
    pat, _ = rx.synthesize(*CASES[4], seed=0)
    assert pat.startswith("INV-")


def test_generalises_to_unseen_examples():
    # generated phone-like codes; train on a few, check on held-out ones
    rng = random.Random(5)
    gen = lambda: f"{rng.randint(100, 999)}-{rng.randint(1000, 9999)}"
    train = [gen() for _ in range(4)]
    held = [gen() for _ in range(30)]
    neg = ["1234567", "12-3456", "abc-defg", "123-45678", "123 4567"]
    pat, acc = rx.synthesize(train, neg, seed=2)
    assert acc == 1.0
    assert all(re.fullmatch(pat, h) for h in held)


def test_tokenize_and_render():
    toks = rx.tokenize("ab12-X")
    assert rx.render(toks) == "[a-z]{2}\\d{2}-[A-Z]"
    assert rx.render(rx.tokenize("v1", letters_literal=True)) == "v\\d"


def test_mdl_data_cost_orders_patterns():
    tight = rx.tokenize("2026-09-27")
    loose = [rx.Tok("set", frozenset({"0-9", "-"}), 10, 10)]
    assert rx.data_bits(tight, "2026-09-27") < rx.data_bits(loose, "2026-09-27")
    assert rx.data_bits(tight, "2026x09-27") == float("inf")


def test_explain_traditional_chinese():
    out = rx.explain(r"\d{4}-\d{2}-\d{2}")
    assert "剛好 4 個數字" in out and "連字號" in out and "整個字串" in out
    out = rx.explain(r"^(foo|ba[rz])+\s*[A-Za-z]{2,}?$", fullmatch=False)
    assert "選項 1" in out and "固定文字「foo」" in out and "英文字母" in out
    assert "字串開頭" in out and "盡量少" in out


def test_cli(tmp_path):
    pf = tmp_path / "pos.txt"
    pf.write_text("A-1\nB-22\n")
    r = subprocess.run([sys.executable, str(ROOT / "regex_from_examples.py"), "--pos-file", str(pf),
                        "-n", "a-1", "-n", "AB-1"], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "accuracy          : 100.0%" in r.stdout and "依序符合" in r.stdout
    r = subprocess.run([sys.executable, str(ROOT / "regex_from_examples.py"), "--explain", r"a+b?"],
                       capture_output=True, text=True)
    assert "一個或多個" in r.stdout and "可有可無" in r.stdout


def test_help():
    r = subprocess.run([sys.executable, str(ROOT / "regex_from_examples.py"), "--help"],
                       capture_output=True, text=True)
    assert r.returncode == 0 and "Traditional Chinese" in r.stdout
