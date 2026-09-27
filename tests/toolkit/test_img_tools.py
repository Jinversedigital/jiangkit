"""Tests for img_tools.py using generated sample images."""
import json
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from jiangkit.media import img_tools as T


@pytest.fixture
def samples(tmp_path):
    d = tmp_path / "in"
    d.mkdir()
    # A gradient landscape photo with EXIF (incl. fake GPS-ish tags).
    im = Image.new("RGB", (2000, 1200))
    dr = ImageDraw.Draw(im)
    for x in range(0, 2000, 10):
        dr.rectangle([x, 0, x + 10, 1200], fill=(x * 255 // 2000, 255 - x * 255 // 2000, 128))
    dr.ellipse([700, 300, 1300, 900], fill=(255, 220, 0))
    exif = Image.Exif()
    exif[0x010F] = "FakeCam"      # Make
    exif[0x0110] = "Model X"      # Model
    exif[0x8825] = {2: (25.0, 2.0, 0.0)}  # GPS IFD latitude
    im.save(d / "photo.jpg", quality=95, exif=exif)
    # near-duplicate: resized + slightly recompressed copy
    im.resize((1000, 600)).save(d / "photo_small.jpg", quality=70)
    # Different image (portrait, transparent PNG)
    p = Image.new("RGBA", (600, 900), (0, 0, 0, 0))
    ImageDraw.Draw(p).rectangle([100, 100, 500, 800], fill=(0, 120, 255, 255))
    ImageDraw.Draw(p).line([0, 0, 600, 900], fill=(255, 0, 0, 255), width=40)
    p.save(d / "logo.png")
    # Third, distinct image
    q = Image.new("RGB", (800, 800), "white")
    ImageDraw.Draw(q).polygon([(400, 50), (750, 750), (50, 750)], fill="black")
    q.save(d / "tri.webp")
    return d


def test_resize(samples, tmp_path):
    out = tmp_path / "o"
    assert T.main(["resize", str(samples), "--max-side", "800", "-o", str(out)]) == 0
    assert Image.open(out / "photo.jpg").size == (800, 480)
    assert Image.open(out / "logo.png").size == (533, 800)
    assert T.main(["resize", str(samples / "photo.jpg"), "--width", "500", "-o", str(out), "--suffix", "_w"]) == 0
    assert Image.open(out / "photo_w.jpg").size == (500, 300)


@pytest.mark.parametrize("preset", ["ig-square", "ig-portrait", "reels", "x"])
@pytest.mark.parametrize("mode", ["crop", "pad"])
def test_fit_presets(samples, tmp_path, preset, mode):
    out = tmp_path / "o"
    assert T.main(["fit", str(samples), "-p", preset, "--mode", mode, "-o", str(out)]) == 0
    for f in out.iterdir():
        assert Image.open(f).size == T.PRESETS[preset], f


def test_fit_pad_color_and_custom(samples, tmp_path):
    out = tmp_path / "o"
    assert T.main(["fit", str(samples / "photo.jpg"), "-p", "1080x1920", "--mode", "pad",
                   "--bg", "#000000", "-o", str(out)]) == 0
    im = Image.open(out / "photo_1080x1920.jpg")
    assert im.size == (1080, 1920)
    assert max(im.getpixel((540, 5))) < 20  # black bar on top


def test_watermark_text_and_image(samples, tmp_path):
    out = tmp_path / "o"
    src = samples / "photo.jpg"
    assert T.main(["watermark", str(src), "--text", "@正道 AI", "--position", "bottom-right",
                   "--opacity", "0.8", "-o", str(out)]) == 0
    a, b = Image.open(src).convert("RGB"), Image.open(out / "photo.jpg").convert("RGB")
    W, H = a.size
    diff_corner = sum(abs(x - y) for x, y in zip(a.crop((W - 400, H - 200, W, H)).tobytes(),
                                                 b.crop((W - 400, H - 200, W, H)).tobytes()))
    diff_topleft = sum(abs(x - y) for x, y in zip(a.crop((0, 0, 300, 150)).tobytes(),
                                                  b.crop((0, 0, 300, 150)).tobytes()))
    assert diff_corner > 10 * diff_topleft + 1000  # mark is in the bottom-right only
    assert T.main(["watermark", str(src), "--image", str(samples / "logo.png"), "--position",
                   "top-left", "--opacity", "0.5", "--suffix", "_img", "-o", str(out)]) == 0
    assert Image.open(out / "photo_img.jpg").size == (2000, 1200)
    assert T.main(["watermark", str(src), "--text", "SAMPLE", "--tile", "--suffix", "_tile", "-o", str(out)]) == 0
    assert T.main(["watermark", str(src), "-o", str(out)]) == 2  # neither text nor image


def test_strip_exif(samples, tmp_path):
    out = tmp_path / "o"
    src = samples / "photo.jpg"
    assert len(Image.open(src).getexif()) > 0
    assert T.main(["strip-exif", str(src), "-o", str(out)]) == 0
    clean = Image.open(out / "photo.jpg")
    assert len(clean.getexif()) == 0 and "exif" not in clean.info


def test_convert(samples, tmp_path):
    out = tmp_path / "o"
    assert T.main(["convert", str(samples), "-f", "webp", "-q", "80", "-o", str(out)]) == 0
    names = sorted(p.name for p in out.iterdir())
    assert names == ["logo.webp", "photo.webp", "photo_small.webp", "tri.webp"]
    assert Image.open(out / "logo.webp").format == "WEBP"
    assert T.main(["convert", str(samples / "logo.png"), "-f", "jpg", "-o", str(out)]) == 0
    assert Image.open(out / "logo.jpg").mode == "RGB"  # alpha flattened


def test_dupes_report_and_move(samples, tmp_path):
    rep = tmp_path / "rep.json"
    mv = tmp_path / "dups"
    assert T.main(["dupes", str(samples), "--report", str(rep), "--move-to", str(mv)]) == 0
    data = json.loads(rep.read_text(encoding="utf-8"))
    assert len(data) == 1
    assert data[0]["keep"].endswith("photo.jpg")
    assert data[0]["duplicates"][0].endswith("photo_small.jpg")
    assert (mv / "photo_small.jpg").exists() and not (samples / "photo_small.jpg").exists()
