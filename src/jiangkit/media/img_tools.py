#!/usr/bin/env python3
"""Batch image utilities for social-media content (IG / Reels / X).

Subcommands: resize, fit (crop/pad to preset), watermark, strip-exif,
convert, dupes (perceptual-hash duplicate finder).
Every subcommand accepts files and/or directories (recursive with -r).
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

PRESETS = {
    "ig-square": (1080, 1080),
    "ig-portrait": (1080, 1350),
    "ig-landscape": (1080, 566),
    "reels": (1080, 1920),
    "story": (1080, 1920),
    "x": (1600, 900),
    "yt-thumb": (1280, 720),
}
IMG_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".gif"}
FORMAT_OF_EXT = {".jpg": "JPEG", ".jpeg": "JPEG", ".png": "PNG", ".webp": "WEBP",
                 ".bmp": "BMP", ".tif": "TIFF", ".tiff": "TIFF", ".gif": "GIF"}
POSITIONS = ["top-left", "top", "top-right", "left", "center", "right",
             "bottom-left", "bottom", "bottom-right"]
# CJK-capable fonts tried in order for text watermarks.
FONT_CANDIDATES = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSerifCJK-Bold.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]


# ------------------------------------------------------------------ helpers
def collect(inputs: list[str], recursive: bool) -> list[Path]:
    files: list[Path] = []
    for s in inputs:
        p = Path(s)
        if p.is_dir():
            it = p.rglob("*") if recursive else p.glob("*")
            files += sorted(f for f in it if f.is_file() and f.suffix.lower() in IMG_EXT)
        elif p.is_file():
            files.append(p)
        else:
            print(f"找不到: {s}", file=sys.stderr)
    return files


def out_path(src: Path, out_dir: str | None, suffix: str = "", ext: str | None = None,
             in_place: bool = False) -> Path:
    if in_place:
        return src if not ext else src.with_suffix(ext)
    d = Path(out_dir) if out_dir else src.parent / "out"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{src.stem}{suffix}{ext or src.suffix}"


def open_img(p: Path) -> Image.Image:
    im = Image.open(p)
    return ImageOps.exif_transpose(im)  # respect phone rotation before editing


def save(im: Image.Image, dst: Path, quality: int = 92, keep_exif: bytes | None = None) -> None:
    fmt = FORMAT_OF_EXT.get(dst.suffix.lower(), "PNG")
    if fmt == "JPEG" and im.mode not in ("RGB", "L"):
        bg = Image.new("RGB", im.size, (255, 255, 255))
        if im.mode in ("RGBA", "LA", "P"):
            im = im.convert("RGBA")
            bg.paste(im, mask=im.split()[-1])
        else:
            bg.paste(im.convert("RGB"))
        im = bg
    kw = {}
    if fmt in ("JPEG", "WEBP"):
        kw["quality"] = quality
    if fmt == "JPEG":
        kw.update(optimize=True, progressive=True)
    if fmt == "PNG":
        kw["optimize"] = True
    if keep_exif:
        kw["exif"] = keep_exif
    else:
        # Privacy default: never carry EXIF/GPS/XMP/comments from the source into exports.
        icc = im.info.get("icc_profile")
        im = im.copy()
        im.info = {k: v for k, v in im.info.items() if k in ("transparency",)}
        if icc:
            kw["icc_profile"] = icc
    im.save(dst, fmt, **kw)


def parse_size(s: str) -> tuple[int, int]:
    if s in PRESETS:
        return PRESETS[s]
    w, h = s.lower().split("x")
    return int(w), int(h)


def parse_color(s: str) -> tuple:
    from PIL import ImageColor
    return ImageColor.getrgb(s)


# --------------------------------------------------------------- operations
def op_resize(im: Image.Image, width=None, height=None, max_side=None, scale=None) -> Image.Image:
    w, h = im.size
    if scale:
        nw, nh = int(w * scale), int(h * scale)
    elif max_side:
        r = max_side / max(w, h)
        if r >= 1:
            return im
        nw, nh = int(w * r), int(h * r)
    elif width and height:
        nw, nh = width, height
    elif width:
        nw, nh = width, round(h * width / w)
    elif height:
        nw, nh = round(w * height / h), height
    else:
        return im
    return im.resize((max(1, nw), max(1, nh)), Image.LANCZOS)


def op_fit(im: Image.Image, size: tuple[int, int], mode: str = "crop", anchor: str = "center",
           bg: str = "blur") -> Image.Image:
    """Crop (cover) or pad (contain) to exactly `size`. Pad bg: blur or a color."""
    tw, th = size
    if mode == "crop":
        cx = {"left": 0.0, "top-left": 0.0, "bottom-left": 0.0,
              "right": 1.0, "top-right": 1.0, "bottom-right": 1.0}.get(anchor, 0.5)
        cy = {"top": 0.0, "top-left": 0.0, "top-right": 0.0,
              "bottom": 1.0, "bottom-left": 1.0, "bottom-right": 1.0}.get(anchor, 0.5)
        return ImageOps.fit(im, size, Image.LANCZOS, centering=(cx, cy))
    # pad
    fg = ImageOps.contain(im, size, Image.LANCZOS)
    if bg == "blur":
        canvas = ImageOps.fit(im.convert("RGB"), size, Image.LANCZOS).filter(ImageFilter.GaussianBlur(40))
    else:
        canvas = Image.new("RGB", size, parse_color(bg))
    if fg.mode == "RGBA":
        canvas.paste(fg, ((tw - fg.width) // 2, (th - fg.height) // 2), fg)
    else:
        canvas.paste(fg.convert("RGB"), ((tw - fg.width) // 2, (th - fg.height) // 2))
    return canvas


def _pos(pos: str, W: int, H: int, w: int, h: int, margin: int) -> tuple[int, int]:
    x = {"left": margin, "center": (W - w) // 2, "right": W - w - margin}
    y = {"top": margin, "center": (H - h) // 2, "bottom": H - h - margin}
    parts = pos.split("-")
    vy = parts[0] if parts[0] in ("top", "bottom") else "center"
    hx = parts[-1] if parts[-1] in ("left", "right") else "center"
    return x[hx], y[vy]


def load_font(size: int, path: str | None = None):
    for f in ([path] if path else []) + FONT_CANDIDATES:
        if f and Path(f).exists():
            try:
                return ImageFont.truetype(f, size)
            except OSError:
                continue
    try:  # ask fontconfig for any CJK-capable font
        import subprocess  # nosec B404 - subprocess is required to drive external tools; every call uses an argv list
        f = subprocess.run(["fc-match", "-f", "%{file}", "sans:lang=zh-tw"], capture_output=True,  # nosec B603 B607 - argv list without shell; executable and arguments are fixed or validated; external tool is looked up on PATH by design; argv list, no shell
                           text=True, timeout=5).stdout.strip()
        if f:
            return ImageFont.truetype(f, size)
    except Exception:  # nosec B110 - best-effort optional step; failure is intentionally non-fatal
        pass
    return ImageFont.load_default(size=size)


def op_watermark(im: Image.Image, text: str | None = None, image: str | None = None,
                 position: str = "bottom-right", opacity: float = 0.5, scale: float = 0.2,
                 margin: float = 0.03, color: str = "white", font: str | None = None,
                 tile: bool = False) -> Image.Image:
    base = im.convert("RGBA")
    W, H = base.size
    m = int(min(W, H) * margin)
    if image:
        mark = Image.open(image).convert("RGBA")
        mw = max(1, int(W * scale))
        mark = mark.resize((mw, max(1, round(mark.height * mw / mark.width))), Image.LANCZOS)
    else:
        fnt = load_font(max(10, int(min(W, H) * scale * 0.3)), font)
        dummy = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
        l, t, r, b = dummy.textbbox((0, 0), text, font=fnt, stroke_width=2)
        mark = Image.new("RGBA", (r - l + 4, b - t + 4), (0, 0, 0, 0))
        d = ImageDraw.Draw(mark)
        d.text((2 - l, 2 - t), text, font=fnt, fill=parse_color(color) + (255,),
               stroke_width=2, stroke_fill=(0, 0, 0, 160))
    alpha = mark.split()[-1].point(lambda a: int(a * opacity))
    mark.putalpha(alpha)
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    if tile:
        # Staggered grid of marks; each row is shifted by half a step.
        step_x, step_y = mark.width * 2, mark.height * 3
        for row, yy in enumerate(range(m, H - mark.height, step_y)):
            for xx in range(m + (row % 2) * mark.width, W - mark.width, step_x):
                layer.alpha_composite(mark, (xx, yy))
    else:
        layer.alpha_composite(mark, _pos(position, W, H, mark.width, mark.height, m))
    out = Image.alpha_composite(base, layer)
    return out if im.mode == "RGBA" else out.convert("RGB")


def strip_exif(im: Image.Image) -> Image.Image:
    """Rebuild image from pixels only (drops EXIF/GPS/XMP/ICC text chunks)."""
    clean = Image.frombytes(im.mode, im.size, im.tobytes())
    if im.mode == "P" and im.getpalette():
        clean.putpalette(im.getpalette())
    return clean


# ---------------------------------------------------------------- commands
def _batch(args, fn, suffix="", ext=None) -> int:
    files = collect(args.inputs, args.recursive)
    if not files:
        print("沒有可處理的圖片", file=sys.stderr)
        return 1
    n = 0
    for f in files:
        try:
            im = open_img(f)
            res = fn(im)
            dst = out_path(f, args.out_dir, suffix, ext, getattr(args, "in_place", False))
            save(res, dst, getattr(args, "quality", 92))
            print(f"{f} → {dst} {res.size[0]}x{res.size[1]}")
            n += 1
        except Exception as e:
            print(f"失敗 {f}: {e}", file=sys.stderr)
    print(f"完成 {n}/{len(files)}")
    return 0 if n == len(files) else 1


def cmd_resize(a):
    return _batch(a, lambda im: op_resize(im, a.width, a.height, a.max_side, a.scale), a.suffix)


def cmd_fit(a):
    size = parse_size(a.preset)
    return _batch(a, lambda im: op_fit(im, size, a.mode, a.anchor, a.bg), a.suffix or f"_{a.preset}")


def cmd_watermark(a):
    if not a.text and not a.image:
        print("需要 --text 或 --image", file=sys.stderr)
        return 2
    return _batch(a, lambda im: op_watermark(im, a.text, a.image, a.position, a.opacity,
                                             a.scale, a.margin, a.color, a.font, a.tile), a.suffix)


def cmd_strip_exif(a):
    return _batch(a, strip_exif, a.suffix)


def cmd_convert(a):
    ext = "." + a.format.lower().replace("jpeg", "jpg")
    return _batch(a, lambda im: im, a.suffix, ext)


def cmd_dupes(a) -> int:
    import imagehash
    import json
    files = collect(a.inputs, a.recursive)
    hfn = {"phash": imagehash.phash, "dhash": imagehash.dhash,
           "ahash": imagehash.average_hash, "whash": imagehash.whash}[a.algo]
    hashes = []
    for f in files:
        try:
            with Image.open(f) as im:
                hashes.append((f, hfn(im)))
        except Exception as e:
            print(f"略過 {f}: {e}", file=sys.stderr)
    # Group by union-find over pairwise Hamming distance <= threshold.
    parent = list(range(len(hashes)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for i in range(len(hashes)):
        for j in range(i + 1, len(hashes)):
            if hashes[i][1] - hashes[j][1] <= a.threshold:
                parent[find(j)] = find(i)
    groups: dict[int, list[Path]] = {}
    for i, (f, _) in enumerate(hashes):
        groups.setdefault(find(i), []).append(f)
    dup_groups = [g for g in groups.values() if len(g) > 1]
    report = []
    for g in dup_groups:
        # keep the largest-resolution / largest-file image, others are duplicates
        g.sort(key=lambda p: (Image.open(p).size[0] * Image.open(p).size[1], p.stat().st_size), reverse=True)
        report.append({"keep": str(g[0]), "duplicates": [str(p) for p in g[1:]]})
    print(f"掃描 {len(hashes)} 張，發現 {len(dup_groups)} 組重複：")
    for r in report:
        print(f"  保留 {r['keep']}")
        for d in r["duplicates"]:
            print(f"    重複 {d}")
    if a.report:
        Path(a.report).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"報告 → {a.report}")
    if a.move_to:
        dest = Path(a.move_to)
        dest.mkdir(parents=True, exist_ok=True)
        for r in report:
            for d in r["duplicates"]:
                target = dest / Path(d).name
                k = 1
                while target.exists():
                    target = dest / f"{Path(d).stem}_{k}{Path(d).suffix}"
                    k += 1
                shutil.move(d, target)
        print(f"已將重複檔移到 {dest}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="img_tools", description="批次圖片工具（IG / Reels / X 尺寸、浮水印、去 EXIF、轉檔、找重複）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p, quality=True):
        p.add_argument("inputs", nargs="+", help="圖片檔或資料夾")
        p.add_argument("-r", "--recursive", action="store_true", help="遞迴子資料夾")
        p.add_argument("-o", "--out-dir", help="輸出資料夾 (預設: 原資料夾/out)")
        p.add_argument("--suffix", default="", help="輸出檔名後綴")
        p.add_argument("--in-place", action="store_true", help="直接覆蓋原檔 (小心)")
        if quality:
            p.add_argument("-q", "--quality", type=int, default=92, help="JPEG/WEBP 品質 1-100")

    p = sub.add_parser("resize", help="批次縮放")
    common(p)
    p.add_argument("--width", type=int)
    p.add_argument("--height", type=int)
    p.add_argument("--max-side", type=int, help="最長邊不超過 N（不放大）")
    p.add_argument("--scale", type=float, help="倍率，如 0.5")
    p.set_defaults(func=cmd_resize)

    p = sub.add_parser("fit", help="裁切/補邊到預設尺寸: " + ", ".join(f"{k}={w}x{h}" for k, (w, h) in PRESETS.items()))
    common(p)
    p.add_argument("-p", "--preset", required=True, help="預設名稱或 WxH，如 ig-portrait / 1080x1350")
    p.add_argument("--mode", choices=["crop", "pad"], default="crop", help="crop=裁切填滿, pad=完整保留+補邊")
    p.add_argument("--anchor", choices=POSITIONS, default="center", help="crop 時保留哪個區域")
    p.add_argument("--bg", default="blur", help="pad 背景: blur 或顏色 (#000000 / white)")
    p.set_defaults(func=cmd_fit)

    p = sub.add_parser("watermark", help="文字或圖片浮水印")
    common(p)
    p.add_argument("--text", help="浮水印文字（支援中文）")
    p.add_argument("--image", help="浮水印圖片 (建議透明 PNG)")
    p.add_argument("--position", choices=POSITIONS, default="bottom-right")
    p.add_argument("--opacity", type=float, default=0.5, help="0-1")
    p.add_argument("--scale", type=float, default=0.2, help="浮水印相對大小 (圖片=寬度比例)")
    p.add_argument("--margin", type=float, default=0.03, help="邊距 (短邊比例)")
    p.add_argument("--color", default="white", help="文字顏色")
    p.add_argument("--font", help="字型檔路徑 (.ttf/.ttc)")
    p.add_argument("--tile", action="store_true", help="平鋪整張圖 (防盜用)")
    p.set_defaults(func=cmd_watermark)

    p = sub.add_parser("strip-exif", help="移除 EXIF / GPS 等中繼資料")
    common(p)
    p.set_defaults(func=cmd_strip_exif)

    p = sub.add_parser("convert", help="轉換格式 / 品質")
    common(p)
    p.add_argument("-f", "--format", required=True, choices=["jpg", "jpeg", "png", "webp"])
    p.set_defaults(func=cmd_convert)

    p = sub.add_parser("dupes", help="感知雜湊找重複/近似圖片")
    p.add_argument("inputs", nargs="+")
    p.add_argument("-r", "--recursive", action="store_true")
    p.add_argument("--algo", choices=["phash", "dhash", "ahash", "whash"], default="phash")
    p.add_argument("-t", "--threshold", type=int, default=8, help="漢明距離門檻 (0=完全相同, 越大越寬鬆)")
    p.add_argument("--report", help="輸出 JSON 報告路徑")
    p.add_argument("--move-to", help="把重複檔移到此資料夾 (保留解析度最高者)")
    p.set_defaults(func=cmd_dupes)
    return ap


def main(argv=None) -> int:
    a = build_parser().parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
