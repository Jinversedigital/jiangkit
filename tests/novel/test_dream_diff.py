import json
import subprocess
import sys
from pathlib import Path

import jiangkit.experimental as _pkg  # noqa: E402
ROOT = Path(_pkg.__file__).resolve().parent
from jiangkit.experimental import dream_diff as dd  # noqa: E402

CONTRACT_A = """This Agreement is made between Acme Corp and Beta LLC. The Supplier shall deliver the goods within 30 days of the order.

Payment must be made within 45 days. The Buyer may inspect the goods upon arrival. We are delighted to work together and expect a successful partnership.

Late delivery will result in a penalty of 5% of the order value. The warranty covers all defects for 2 years."""
CONTRACT_B = """This Agreement is made between Acme Corp and Gamma Inc. The Supplier should deliver the goods within 60 days of the order.

Payment must be made within 45 days. The Buyer may inspect the goods upon arrival. We expect the partnership to possibly face some risks.

Late delivery will not result in a penalty. The warranty covers some defects for 1 year. The Buyer shall indemnify the Supplier against all claims."""


def by_tag(changes, tag):
    return [c for c in changes if tag in c.tags]


def test_contract_meaning_shifts():
    ch = dd.semantic_diff(CONTRACT_A, CONTRACT_B)
    nums = " ".join(d for c in by_tag(ch, "numbers") for d in c.details)
    assert "30 days -> 60 days (+100%)" in nums and "2 years -> 1 year (-50%)" in nums
    assert "number removed: 5%" in nums
    ob = by_tag(ch, "obligation")[0]
    assert "weakened" in " ".join(ob.details) and "shall" in ob.old
    assert len(by_tag(ch, "polarity")) == 1
    assert any("indemnify" in c.new for c in by_tag(ch, "claim_added"))
    assert any("delighted" in c.old for c in by_tag(ch, "claim_removed"))
    assert "+Gamma Inc" in " ".join(by_tag(ch, "entities")[0].details)
    assert "all -> some" in " ".join(by_tag(ch, "scope")[0].details)
    # unchanged sentences are not reported as changes
    assert all(c.kind == "same" for c in ch if c.old.startswith("Payment must"))


def test_summary_headline():
    ch = dd.semantic_diff(CONTRACT_A, CONTRACT_B)
    s = dd.summarize(ch, CONTRACT_A, CONTRACT_B)
    assert "numbers changed" in s["headline"] and "claims added" in s["headline"]
    assert s["new"]["certainty"] < s["old"]["certainty"] or "hedges" in s["headline"]


def test_tone_and_certainty_story():
    a = "The village was happy and bright. The harvest was definitely a great success."
    b = "The village was sad and dark. The harvest was perhaps a failure."
    ch = dd.semantic_diff(a, b)
    details = " ".join(d for c in ch for d in c.details)
    assert "colder" in details and "more hedged" in details


def test_negated_negative_is_not_colder():
    assert dd.sentiment("There will be no penalty.") > dd.sentiment("There will be a penalty.")


def test_move_and_rewording():
    a = "Alpha is first. Beta comes next. Gamma closes the list."
    b = "Beta comes next. Gamma closes the list. Alpha is first."
    ch = [c for c in dd.semantic_diff(a, b) if c.kind != "same"]
    assert [c.kind for c in ch] == ["moved"]
    ch = dd.semantic_diff("The team finished the report quickly.", "The team completed the report quickly.")
    assert ch[0].tags == ["rewording"]


def test_generated_numbers_bulk():
    a = " ".join(f"Item {i} costs ${i * 10} per unit." for i in range(1, 11))
    b = " ".join(f"Item {i} costs ${i * 10 + (5 if i % 3 == 0 else 0)} per unit." for i in range(1, 11))
    ch = by_tag(dd.semantic_diff(a, b), "numbers")
    assert len(ch) == 3
    assert all("->" in c.details[0] for c in ch)


def test_chinese_text():
    a = "本公司必須在30天內交貨。我們很感謝您的支持。"
    b = "本公司可以在60天內交貨。我們不感謝您的支持。"
    ch = dd.semantic_diff(a, b)
    tags = {t for c in ch for t in c.tags}
    assert {"numbers", "obligation", "polarity"} <= tags


def test_cli_json_and_html(tmp_path):
    fa, fb, out = tmp_path / "a.txt", tmp_path / "b.txt", tmp_path / "d.html"
    fa.write_text(CONTRACT_A)
    fb.write_text(CONTRACT_B)
    r = subprocess.run([sys.executable, str(ROOT / "dream_diff.py"), str(fa), str(fb), "--json",
                        "--html", str(out)], capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert r.returncode == 0, r.stderr
    data = json.loads(r.stdout)
    assert "numbers" in data["summary"]["groups"]
    h = out.read_text()
    assert "<del>" in h and "<ins>" in h and "Obligation shifted" in h
    r = subprocess.run([sys.executable, str(ROOT / "dream_diff.py"), str(fa), str(fb)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert "== Numbers changed" in r.stdout


def test_help():
    r = subprocess.run([sys.executable, str(ROOT / "dream_diff.py"), "--help"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert r.returncode == 0 and "semantic diff" in r.stdout.lower()
