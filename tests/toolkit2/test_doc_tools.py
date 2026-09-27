import shutil
import subprocess

import pymupdf
import pytest
from PIL import Image, ImageDraw, ImageFont

from jiangkit.docs import doc_tools

FONT = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"


def make_pdf(path, n=5, prefix="Page"):
    doc = pymupdf.open()
    for i in range(n):
        page = doc.new_page()
        page.insert_text((72, 72), f"{prefix} {i + 1}", fontsize=24)
    doc.save(path)
    return path


def make_image(path, color, size=(400, 300)):
    Image.new("RGB", size, color).save(path)
    return path


def test_parse_ranges():
    assert doc_tools.parse_ranges("1-3,5,8-", 10) == [0, 1, 2, 4, 7, 8, 9]
    with pytest.raises(ValueError):
        doc_tools.parse_ranges("4-12", 10)


def test_merge_split_extract_rotate(cli, tmp_path):
    a = make_pdf(tmp_path / "a.pdf", 3, "A")
    b = make_pdf(tmp_path / "b.pdf", 2, "B")
    cli("doc_tools", "merge", a, b, "-o", "m.pdf")
    m = pymupdf.open(tmp_path / "m.pdf")
    assert m.page_count == 5 and "B 2" in m[4].get_text()

    cli("doc_tools", "split", "m.pdf", "-d", "parts")
    assert len(list((tmp_path / "parts").glob("*.pdf"))) == 5
    cli("doc_tools", "split", "m.pdf", "-d", "chunks", "--every", "2")
    assert len(list((tmp_path / "chunks").glob("*.pdf"))) == 3

    cli("doc_tools", "extract", "m.pdf", "-p", "2,4-5", "-o", "e.pdf")
    e = pymupdf.open(tmp_path / "e.pdf")
    assert [p.get_text().strip() for p in e] == ["A 2", "B 1", "B 2"]

    cli("doc_tools", "rotate", "m.pdf", "-a", "90", "-p", "1", "-o", "r.pdf")
    r = pymupdf.open(tmp_path / "r.pdf")
    assert r[0].rotation == 90 and r[1].rotation == 0


def test_compress_images_roundtrip_and_text(cli, tmp_path):
    imgs = []
    for i, c in enumerate(["red", "green", "blue"]):
        # Noisy image so compression has something to do
        im = Image.effect_noise((1200, 900), 60).convert("RGB")
        im.save(tmp_path / f"i{i}.png")
        imgs.append(tmp_path / f"i{i}.png")
    cli("doc_tools", "from-images", *imgs, "-o", "imgs.pdf")
    assert pymupdf.open(tmp_path / "imgs.pdf").page_count == 3
    cli("doc_tools", "from-images", *imgs, "-o", "a4.pdf", "--page-size", "a4")
    assert round(pymupdf.open(tmp_path / "a4.pdf")[0].rect.width) == 595

    cli("doc_tools", "compress", "a4.pdf", "-o", "small.pdf", "--image-dpi", "72")
    assert (tmp_path / "small.pdf").stat().st_size < (tmp_path / "a4.pdf").stat().st_size

    cli("doc_tools", "to-images", "imgs.pdf", "-d", "out", "--dpi", "50", "-f", "jpg")
    assert len(list((tmp_path / "out").glob("*.jpg"))) == 3

    make_pdf(tmp_path / "t.pdf", 2, "Hello")
    res = cli("doc_tools", "text", "t.pdf")
    assert "Hello 1" in res.stdout and "Hello 2" in res.stdout


@pytest.mark.skipif(not shutil.which("tesseract"), reason="tesseract not installed")
def test_ocr_chinese_and_english(cli, tmp_path):
    ok, msg = doc_tools.tesseract_status("chi_tra+eng")
    if not ok:
        pytest.skip(msg)
    im = Image.new("RGB", (600, 160), "white")
    d = ImageDraw.Draw(im)
    d.text((30, 30), "繁體中文測試", font=ImageFont.truetype(FONT, 26), fill="black")
    d.text((30, 90), "Hello OCR 2026", font=ImageFont.truetype(FONT, 24), fill="black")
    im.save(tmp_path / "scan.png")
    cli("doc_tools", "from-images", "scan.png", "-o", "scan.pdf")
    res = cli("doc_tools", "ocr", "scan.pdf", "-o", "ocr.txt")
    text = (tmp_path / "ocr.txt").read_text()
    assert "Hello" in text and "2026" in text
    assert "中文" in text.replace(" ", "")


def test_ocr_missing_lang_skips_gracefully(cli):
    res = cli("doc_tools", "ocr", "nothing.png", "-l", "zzz_fake", check=False)
    assert res.returncode == 2 and "[skip]" in res.stderr


def test_convert_markdown_and_docx(cli, tmp_path):
    md = tmp_path / "note.md"
    md.write_text("# 標題 Title\n\n繁體中文段落 paragraph.\n\n| a | b |\n|---|---|\n| 1 | 2 |\n",
                  encoding="utf-8")
    cli("doc_tools", "convert", md, "-o", "note.pdf")
    text = cli("doc_tools", "text", "note.pdf").stdout
    assert "繁體中文段落" in text and "Title" in text
    if shutil.which("pandoc"):
        subprocess.run(["pandoc", str(md), "-o", str(tmp_path / "note.docx")], check=True)
        cli("doc_tools", "convert", "note.docx", "-o", "docx.pdf")
        assert "繁體中文段落" in cli("doc_tools", "text", "docx.pdf").stdout
