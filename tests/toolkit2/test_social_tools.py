import csv
import json
from urllib.parse import parse_qs, urlsplit

from PIL import Image

from jiangkit.social import social_tools as st


def test_tags_add_list_pick_rotate(cli, tmp_path):
    store = tmp_path / "tags.json"
    tags = " ".join(f"#tag{i}" for i in range(40))
    cli("social_tools", "tags", "add", "big", tags, "--store", store)
    cli("social_tools", "tags", "add", "brand", "#MeiAI", "ken", "#MeiAI", "--store", store)
    data = json.loads(store.read_text())
    assert len(data["groups"]["big"]) == 40 and data["groups"]["brand"] == ["#MeiAI", "#ken"]
    r = cli("social_tools", "tags", "pick", "big,brand", "--platform", "IG", "--always", "MeiAI",
            "--seed", "1", "--store", store)
    picked = r.stdout.split()
    assert len(picked) == 30 and picked[0] == "#MeiAI" and len(set(picked)) == 30
    r = cli("social_tools", "tags", "pick", "big", "--platform", "X", "--store", store)
    assert len(r.stdout.split()) == 3
    a = cli("social_tools", "tags", "pick", "big", "-n", "5", "--mode", "rotate", "--store", store).stdout.split()
    b = cli("social_tools", "tags", "pick", "big", "-n", "5", "--mode", "rotate", "--store", store).stdout.split()
    assert a == [f"#tag{i}" for i in range(5)] and b == [f"#tag{i}" for i in range(5, 10)]
    r = cli("social_tools", "tags", "list", "--store", store)
    assert "[big] (40)" in r.stdout
    cli("social_tools", "tags", "remove", "brand", "--store", store)
    assert "brand" not in json.loads(store.read_text())["groups"]


def test_caption_check(cli):
    assert st.x_weighted_length("hello") == 5
    assert st.x_weighted_length("你好") == 4
    assert st.x_weighted_length("see https://example.com/very/long/path?x=1") == 4 + 23
    ok = cli("social_tools", "check", "-t", "今天的咖啡 ☕ #mei", "-p", "IG,X,Threads")
    assert ok.stdout.count("[OK ]") == 3
    long_zh = "字" * 150  # 150 CJK = 300 weighted on X
    bad = cli("social_tools", "check", "-t", long_zh, "-p", "X,IG", "--json", check=False)
    res = {r["platform"]: r for r in json.loads(bad.stdout)}
    assert bad.returncode == 1 and not res["X"]["ok"] and res["IG"]["ok"]
    r = cli("social_tools", "check", "-t", "hi #a #b", "-p", "Threads", check=False)
    assert r.returncode == 1 and "2 hashtags > max 1" in r.stdout


def test_calendar_csv_and_md(cli, tmp_path):
    store = tmp_path / "tags.json"
    cli("social_tools", "tags", "add", "ai", "#aiart #aigirl #fluxai #comfyui #aiinfluencer",
        "--store", store)
    cli("social_tools", "calendar", "-o", "cal.csv", "--start", "2026-10-01", "--days", "7",
        "--slots", "12:00,20:00", "--platforms", "IG,X,Threads,Patreon",
        "--themes", "咖啡廳,穿搭,幕後", "--tag-groups", "ai", "--store", store, "--seed", "3",
        "--skip-weekdays", "7")
    with open(tmp_path / "cal.csv", encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 6 * 2 * 4  # 2026-10-04 is a Sunday -> skipped
    assert rows[0]["date"] == "2026-10-01" and rows[0]["weekday"] == "週四"
    assert rows[0]["theme"] == "咖啡廳"
    x_rows = [r for r in rows if r["platform"] == "X"]
    assert all(len(r["hashtags"].split()) <= 3 for r in x_rows)
    assert all(len(r["hashtags"].split()) <= 1 for r in rows if r["platform"] == "Threads")
    cli("social_tools", "calendar", "-o", "cal.md", "--start", "2026-10-01", "--days", "3")
    md = (tmp_path / "cal.md").read_text(encoding="utf-8")
    assert md.startswith("# 內容行事曆") and md.count("\n| 2026-10-0") == 3 * 2 * 3


def test_utm(cli, tmp_path):
    url = st.build_utm("https://example.com/shop?ref=abc&utm_source=old#top", "instagram",
                       "social", "oct_drop", content="reel01")
    parts = urlsplit(url)
    q = parse_qs(parts.query)
    assert q["utm_source"] == ["instagram"] and q["ref"] == ["abc"] and parts.fragment == "top"
    assert q["utm_content"] == ["reel01"]
    r = cli("social_tools", "utm", "example.com/p", "--source", "x", "--campaign", "c1")
    assert r.stdout.strip() == "https://example.com/p?utm_source=x&utm_medium=social&utm_campaign=c1"
    (tmp_path / "links.csv").write_text("url,source,campaign\nhttps://a.com,ig,fall\nhttps://b.com,x,fall\n")
    cli("social_tools", "utm", "--csv", "links.csv", "-o", "out.csv")
    with open(tmp_path / "out.csv", encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    assert rows[1]["utm_url"] == "https://b.com?utm_source=x&utm_medium=social&utm_campaign=fall"


def test_qr(cli, tmp_path):
    cli("social_tools", "qr", "https://patreon.com/example", "-o", "q.png", "--error", "H")
    im = Image.open(tmp_path / "q.png")
    assert im.size[0] == im.size[1] and im.size[0] > 200
    cli("social_tools", "qr", "https://example.com", "-o", "q.svg")
    assert "<svg" in (tmp_path / "q.svg").read_text()
