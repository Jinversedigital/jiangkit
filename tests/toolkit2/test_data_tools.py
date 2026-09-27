import json

import pandas as pd
import pytest
from PIL import Image

from jiangkit.data import data_tools

ROWS = [
    {"date": "2026-09-01", "platform": "IG", "post": "咖啡廳 Reel", "likes": 120, "views": 3000},
    {"date": "2026-09-02", "platform": "X", "post": "teaser", "likes": 40, "views": 900},
    {"date": "2026-09-03", "platform": "IG", "post": "穿搭 carousel", "likes": 300, "views": 5200},
    {"date": "2026-09-03", "platform": "IG", "post": "穿搭 carousel", "likes": 300, "views": 5200},
    {"date": "2026-09-04", "platform": "Threads", "post": "日常", "likes": 75, "views": 1500},
]


@pytest.fixture
def csv_file(tmp_path):
    p = tmp_path / "posts.csv"
    pd.DataFrame(ROWS).to_csv(p, index=False)
    return p


def test_convert_roundtrip(cli, tmp_path, csv_file):
    for ext in ["xlsx", "json", "jsonl", "tsv"]:
        cli("data_tools", "convert", csv_file, "-o", f"posts.{ext}")
        back = data_tools.read_any(tmp_path / f"posts.{ext}")
        assert len(back) == 5 and list(back.columns) == list(ROWS[0])
        assert "咖啡廳 Reel" in back["post"].tolist()
    lines = (tmp_path / "posts.jsonl").read_text(encoding="utf-8").splitlines()
    assert json.loads(lines[0])["post"] == "咖啡廳 Reel"  # UTF-8, not \\u escapes
    cli("data_tools", "convert", "posts.xlsx", "-o", "back.csv")
    assert data_tools.read_any(tmp_path / "back.csv")["likes"].sum() == 835


def test_dedupe_filter_sort_select(cli, tmp_path, csv_file):
    cli("data_tools", "dedupe", csv_file, "-o", "d.csv")
    assert len(pd.read_csv(tmp_path / "d.csv")) == 4
    cli("data_tools", "filter", "d.csv", "-w", "platform==IG", "-w", "likes>=200", "-o", "f.csv")
    f = pd.read_csv(tmp_path / "f.csv")
    assert f["post"].tolist() == ["穿搭 carousel"]
    cli("data_tools", "filter", "d.csv", "-w", "post~=咖啡|日常", "-o", "f2.csv")
    assert len(pd.read_csv(tmp_path / "f2.csv")) == 2
    cli("data_tools", "sort", "d.csv", "--by", "platform", "likes:desc", "-o", "s.csv")
    s = pd.read_csv(tmp_path / "s.csv")
    assert s["likes"].tolist() == [300, 120, 75, 40]
    cli("data_tools", "select", "s.csv", "--cols", "post", "likes", "--rename", "likes=讚", "-o", "sel.csv")
    assert list(pd.read_csv(tmp_path / "sel.csv").columns) == ["post", "讚"]
    bad = cli("data_tools", "filter", "d.csv", "-w", "nope==1", "-o", "x.csv", check=False)
    assert bad.returncode != 0 and "Unknown column" in bad.stderr


def test_merge_stack_and_join(cli, tmp_path, csv_file):
    pd.DataFrame(ROWS[:2]).to_json(tmp_path / "more.json", orient="records", force_ascii=False)
    cli("data_tools", "merge", csv_file, "more.json", "-o", "all.csv", "--source-col", "src")
    allr = pd.read_csv(tmp_path / "all.csv")
    assert len(allr) == 7 and set(allr["src"]) == {"posts.csv", "more.json"}
    pd.DataFrame([{"platform": "IG", "handle": "@mei"}, {"platform": "X", "handle": "@mei_x"}]) \
        .to_csv(tmp_path / "handles.csv", index=False)
    cli("data_tools", "merge", csv_file, "handles.csv", "--on", "platform", "--how", "left", "-o", "j.csv")
    j = pd.read_csv(tmp_path / "j.csv")
    assert j.loc[j.platform == "X", "handle"].iloc[0] == "@mei_x"


def test_stats(cli, csv_file):
    res = cli("data_tools", "stats", csv_file)
    assert "Rows: 5" in res.stdout and "likes" in res.stdout and "IG: 3" in res.stdout


def test_chart(cli, tmp_path, csv_file):
    cli("data_tools", "chart", csv_file, "-o", "bar.png", "--x", "platform", "--y", "likes",
        "--agg", "sum", "--title", "各平台按讚數")
    cli("data_tools", "chart", csv_file, "-o", "line.png", "--x", "date", "--y", "likes", "views",
        "--kind", "line", "--rotate", "30")
    for n in ["bar.png", "line.png"]:
        im = Image.open(tmp_path / n)
        assert im.size[0] > 500 and im.format == "PNG"
