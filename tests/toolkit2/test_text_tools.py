from jiangkit.docs import text_tools

SRT = """1
00:00:01,000 --> 00:00:03,500
你好，<i>世界</i>

2
00:00:04,000 --> 00:00:06,000
第二句
第二行

3
00:01:00,250 --> 00:01:02,000
結尾
"""


def test_zh_conversion(cli, tmp_path):
    r = cli("text_tools", "zh", "-t", "这个软件的鼠标和内存信息")
    assert r.stdout.strip() == "這個軟體的滑鼠和記憶體資訊"
    r = cli("text_tools", "zh", "-c", "tw2sp", "-t", "這個軟體的滑鼠")
    assert r.stdout.strip() == "这个软件的鼠标"
    f = tmp_path / "a.txt"
    f.write_text("视频和网络", encoding="utf-8")
    cli("text_tools", "zh", f, "-o", "b.txt")
    assert (tmp_path / "b.txt").read_text(encoding="utf-8") == "影片和網路"
    r = cli("text_tools", "zh", input="头发")
    assert r.stdout == "頭髮"


def test_parse_and_render_roundtrip():
    cues = text_tools.parse_subs(SRT)
    assert len(cues) == 3 and cues[1].text == "第二句\n第二行" and cues[2].start == 60.25
    assert text_tools.parse_subs(text_tools.render_vtt(cues))[2].end == 62.0
    assert text_tools.fmt_time(3661.5) == "01:01:01,500"


def test_sub_shift(cli, tmp_path):
    (tmp_path / "a.srt").write_text(SRT, encoding="utf-8")
    cli("text_tools", "sub-shift", "a.srt", "-s", "1.5", "-o", "b.srt")
    cues = text_tools.load_subs(tmp_path / "b.srt")
    assert cues[0].start == 2.5 and cues[2].end == 63.5
    cli("text_tools", "sub-shift", "a.srt", "-s", "-2", "-o", "c.srt")
    cues = text_tools.load_subs(tmp_path / "c.srt")
    assert cues[0].start == 0 and cues[0].end == 1.5  # clamped at 0
    cli("text_tools", "sub-shift", "a.srt", "-s", "10", "--after", "00:00:30,000", "-o", "d.srt")
    cues = text_tools.load_subs(tmp_path / "d.srt")
    assert cues[0].start == 1.0 and cues[2].start == 70.25


def test_sub_conv_merge_text(cli, tmp_path):
    (tmp_path / "a.srt").write_text(SRT, encoding="utf-8")
    cli("text_tools", "sub-conv", "a.srt", "-o", "a.vtt")
    vtt = (tmp_path / "a.vtt").read_text(encoding="utf-8")
    assert vtt.startswith("WEBVTT") and "00:00:01.000 --> 00:00:03.500" in vtt
    cli("text_tools", "sub-conv", "a.vtt", "-o", "back.srt")
    assert len(text_tools.load_subs(tmp_path / "back.srt")) == 3

    cli("text_tools", "sub-merge", "a.srt", "a.vtt", "-o", "app.srt", "--gap", "1")
    app = text_tools.load_subs(tmp_path / "app.srt")
    assert len(app) == 6 and app[3].start == 62.0 + 1 + 1.0

    en = "1\n00:00:01,100 --> 00:00:03,400\nHello world\n"
    (tmp_path / "en.srt").write_text(en, encoding="utf-8")
    cli("text_tools", "sub-merge", "a.srt", "en.srt", "-m", "bilingual", "-o", "bi.srt")
    bi = text_tools.load_subs(tmp_path / "bi.srt")
    assert bi[0].text.endswith("Hello world") and len(bi) == 3
    cli("text_tools", "sub-merge", "a.srt", "en.srt", "-m", "combine", "-o", "comb.srt")
    assert len(text_tools.load_subs(tmp_path / "comb.srt")) == 4

    r = cli("text_tools", "sub-text", "a.srt")
    assert r.stdout.splitlines() == ["你好，世界", "第二句 第二行", "結尾"]


def test_count(cli):
    c = text_tools.count_text("Mei 今天去咖啡廳 drinking latte.\n")
    assert c["cjk_chars"] == 6 and c["latin_words"] == 3 and c["mixed_word_count"] == 9
    r = cli("text_tools", "count", "-t", "你好 world")
    assert "3" in r.stdout


def test_replace_dry_run_and_apply(cli, tmp_path):
    d = tmp_path / "site"
    d.mkdir()
    (d / "index.html").write_text("<title>Old Brand</title> Old Brand", encoding="utf-8")
    (d / "about.html").write_text("About Old Brand", encoding="utf-8")
    (d / "img.png").write_bytes(b"\x89PNG\x00\xff\xfe Old Brand")
    r = cli("text_tools", "replace", d, "-f", "Old Brand", "-r", "新品牌", "-g", "*.html")
    assert "[dry-run]" in r.stdout and "3 occurrence(s) in 2 file(s)" in r.stdout
    assert "Old Brand" in (d / "index.html").read_text(encoding="utf-8")
    cli("text_tools", "replace", d, "-f", r"<title>(.*?)</title>", "-r", r"<title>\1 | 2026</title>",
        "--regex", "-g", "*.html", "--apply", "--backup")
    assert "<title>Old Brand | 2026</title>" in (d / "index.html").read_text(encoding="utf-8")
    assert (d / "index.html.bak").exists()
    cli("text_tools", "replace", d, "-f", "Old Brand", "-r", "新品牌", "-g", "*.html", "--apply")
    assert "Old Brand" not in (d / "about.html").read_text(encoding="utf-8")
    assert b"Old Brand" in (d / "img.png").read_bytes()
