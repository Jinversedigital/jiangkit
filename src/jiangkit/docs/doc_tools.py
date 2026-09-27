#!/usr/bin/env python3
"""doc_tools.py - PDF and document utilities.

Subcommands:
  merge        Merge several PDFs into one
  split        Split a PDF into single pages or fixed-size chunks
  extract      Extract selected pages (e.g. "1-3,5,8-") into a new PDF
  rotate       Rotate pages by 90/180/270 degrees
  compress     Re-save a PDF with stream compression and optional image downscaling
  to-images    Render PDF pages to PNG/JPG
  from-images  Combine images into a PDF
  text         Extract embedded text
  ocr          OCR scanned PDFs / images with tesseract (chi_tra+eng by default)
  convert      Convert Word (.docx) / Markdown / HTML to PDF (pandoc if available)
"""
import argparse
import io
import os
import shutil
import subprocess  # nosec B404 - subprocess is required to drive external tools; every call uses an argv list
import sys
import unicodedata
from pathlib import Path

import pymupdf  # PyMuPDF

CJK_FONT_DIRS = ["/usr/share/fonts/opentype/noto", "/usr/share/fonts/truetype/noto",
                 "/System/Library/Fonts", "C:/Windows/Fonts"]
CJK_FONT_NAMES = ["NotoSansCJK-Regular.ttc", "NotoSansTC-Regular.otf", "msjh.ttc",
                  "PingFang.ttc"]


def parse_ranges(spec, n_pages):
    """Parse a 1-based page range spec like '1-3,5,8-' into 0-based indices."""
    pages = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            start = int(a) if a else 1
            end = int(b) if b else n_pages
        else:
            start = end = int(part)
        if start < 1 or end > n_pages or start > end:
            raise ValueError(f"Invalid page range '{part}' for a {n_pages}-page document")
        pages.extend(range(start - 1, end))
    return pages


def cmd_merge(a):
    out = pymupdf.open()
    for f in a.inputs:
        with pymupdf.open(f) as src:
            out.insert_pdf(src)
    out.save(a.output, garbage=3, deflate=True)
    print(f"Merged {len(a.inputs)} files -> {a.output} ({out.page_count} pages)")


def cmd_split(a):
    src = pymupdf.open(a.input)
    outdir = Path(a.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    stem = Path(a.input).stem
    n = src.page_count
    step = max(1, a.every)
    files = []
    for start in range(0, n, step):
        end = min(start + step, n) - 1
        part = pymupdf.open()
        part.insert_pdf(src, from_page=start, to_page=end)
        name = outdir / (f"{stem}_p{start + 1:03d}.pdf" if step == 1
                         else f"{stem}_p{start + 1:03d}-{end + 1:03d}.pdf")
        part.save(name)
        files.append(name)
    print(f"Split into {len(files)} files in {outdir}")


def cmd_extract(a):
    src = pymupdf.open(a.input)
    pages = parse_ranges(a.pages, src.page_count)
    src.select(pages)
    src.save(a.output, garbage=3, deflate=True)
    print(f"Extracted {len(pages)} pages -> {a.output}")


def cmd_rotate(a):
    doc = pymupdf.open(a.input)
    pages = parse_ranges(a.pages, doc.page_count) if a.pages else range(doc.page_count)
    for i in pages:
        p = doc[i]
        p.set_rotation((p.rotation + a.angle) % 360)
    doc.save(a.output)
    print(f"Rotated {len(list(pages))} pages by {a.angle} -> {a.output}")


def cmd_compress(a):
    doc = pymupdf.open(a.input)
    if a.image_dpi:
        # Downscale and re-encode embedded images to JPEG at the given DPI
        try:
            doc.rewrite_images(dpi_threshold=a.image_dpi + 10, dpi_target=a.image_dpi,
                               quality=a.quality)
        except AttributeError:
            print("Warning: this PyMuPDF version lacks rewrite_images; skipping image downscale")
    doc.save(a.output, garbage=4, deflate=True, deflate_images=True, deflate_fonts=True,
             clean=True)
    before, after = os.path.getsize(a.input), os.path.getsize(a.output)
    print(f"{a.input}: {before:,} B -> {a.output}: {after:,} B ({after / before:.0%})")


def cmd_to_images(a):
    doc = pymupdf.open(a.input)
    outdir = Path(a.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    pages = parse_ranges(a.pages, doc.page_count) if a.pages else range(doc.page_count)
    count = 0
    for i in pages:
        pix = doc[i].get_pixmap(dpi=a.dpi)
        name = outdir / f"{Path(a.input).stem}_p{i + 1:03d}.{a.format}"
        if a.format == "png":
            pix.save(name)
        else:
            pix.pil_save(name, quality=90)
        count += 1
    print(f"Rendered {count} pages at {a.dpi} DPI -> {outdir}")


def cmd_from_images(a):
    from PIL import Image
    imgs = []
    for f in a.inputs:
        im = Image.open(f)
        if im.mode in ("RGBA", "LA", "P"):
            bg = Image.new("RGB", im.size, "white")
            bg.paste(im.convert("RGBA"), mask=im.convert("RGBA").split()[-1])
            im = bg
        else:
            im = im.convert("RGB")
        imgs.append(im)
    if not imgs:
        sys.exit("No images given")
    if a.page_size:
        # Fit each image onto a fixed page size (A4 portrait at 150 DPI etc.)
        w_pt, h_pt = pymupdf.paper_size(a.page_size)
        doc = pymupdf.open()
        for im in imgs:
            page = doc.new_page(width=w_pt, height=h_pt)
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=92)
            m = a.margin
            page.insert_image(pymupdf.Rect(m, m, w_pt - m, h_pt - m), stream=buf.getvalue(),
                              keep_proportion=True)
        doc.save(a.output, deflate=True)
    else:
        imgs[0].save(a.output, "PDF", save_all=True, append_images=imgs[1:], resolution=150)
    print(f"Combined {len(imgs)} images -> {a.output}")


def cmd_text(a):
    doc = pymupdf.open(a.input)
    pages = parse_ranges(a.pages, doc.page_count) if a.pages else range(doc.page_count)
    # NFC maps CJK compatibility ideographs (e.g. U+F918) that some fonts emit back to the
    # standard characters, so extracted Chinese text is searchable.
    chunks = [unicodedata.normalize("NFC", doc[i].get_text()) for i in pages]
    text = "\n\f".join(chunks)
    if a.output:
        Path(a.output).write_text(text, encoding="utf-8")
        print(f"Wrote {len(text)} chars -> {a.output}")
    else:
        print(text)


def tesseract_status(lang):
    """Return (ok, message) describing whether tesseract + languages are usable."""
    if not shutil.which("tesseract"):
        return False, ("tesseract not installed. Install: sudo apt install tesseract-ocr "
                       "tesseract-ocr-chi-tra  (macOS: brew install tesseract tesseract-lang)")
    try:
        out = subprocess.run(["tesseract", "--list-langs"], capture_output=True, text=True).stdout  # nosec B603 B607 - argv list without shell; executable and arguments are fixed or validated; external tool is looked up on PATH by design; argv list, no shell
    except OSError as e:
        return False, str(e)
    have = set(out.split())
    missing = [l for l in lang.split("+") if l not in have]
    if missing:
        return False, f"tesseract language data missing: {', '.join(missing)}"
    return True, "ok"


def cmd_ocr(a):
    ok, msg = tesseract_status(a.lang)
    if not ok:
        print(f"[skip] OCR unavailable: {msg}", file=sys.stderr)
        sys.exit(2)
    import pytesseract
    from PIL import Image
    texts = []
    src = Path(a.input)
    if src.suffix.lower() == ".pdf":
        doc = pymupdf.open(src)
        for i, page in enumerate(doc):
            pix = page.get_pixmap(dpi=a.dpi)
            im = Image.open(io.BytesIO(pix.tobytes("png")))
            texts.append(pytesseract.image_to_string(im, lang=a.lang))
            print(f"OCR page {i + 1}/{doc.page_count}", file=sys.stderr)
    else:
        texts.append(pytesseract.image_to_string(Image.open(src), lang=a.lang))
    text = "\n\f".join(texts)
    if a.output:
        Path(a.output).write_text(text, encoding="utf-8")
        print(f"Wrote OCR text -> {a.output}")
    else:
        print(text)


def find_cjk_font():
    for d in CJK_FONT_DIRS:
        for n in CJK_FONT_NAMES:
            if Path(d, n).exists():
                return d, n
    return None, None


def html_to_pdf(html, output, paper="a4"):
    """Render HTML to PDF using PyMuPDF Story (no LaTeX needed; supports CJK fonts)."""
    font_dir, font_name = find_cjk_font()
    css = "body{font-size:11pt;line-height:1.5} pre,code{font-size:9pt} " \
          "table{border-collapse:collapse} td,th{border:1px solid #888;padding:3px}"
    archive = None
    if font_dir:
        css = (f"@font-face{{font-family:cjk;src:url({font_name});}} "
               "*{font-family:cjk;} " + css)
        archive = pymupdf.Archive(font_dir)
    story = pymupdf.Story(html=html, user_css=css, archive=archive)
    writer = pymupdf.DocumentWriter(str(output))
    mediabox = pymupdf.paper_rect(paper)
    where = mediabox + (50, 50, -50, -50)
    more = 1
    while more:
        dev = writer.begin_page(mediabox)
        more, _ = story.place(where)
        story.draw(dev)
        writer.end_page()
    writer.close()


def cmd_convert(a):
    src = Path(a.input)
    ext = src.suffix.lower()
    pandoc = shutil.which("pandoc")
    if ext in (".html", ".htm"):
        html = src.read_text(encoding="utf-8")
    elif pandoc:
        # pandoc -> standalone HTML fragment; then render with PyMuPDF (no LaTeX required)
        # absolute path: a file named "-o..." can never be parsed as a pandoc option
        res = subprocess.run([pandoc, str(Path(src).resolve()), "-t", "html5"],  # nosec B603
                             capture_output=True, text=True, timeout=300)
        if res.returncode != 0:
            sys.exit(f"pandoc failed: {res.stderr}")
        html = res.stdout
    elif ext in (".md", ".markdown", ".txt"):
        try:
            import markdown
        except ImportError:
            sys.exit("[skip] Neither pandoc nor the 'markdown' package is available")
        html = markdown.markdown(src.read_text(encoding="utf-8"),
                                 extensions=["tables", "fenced_code"])
    else:
        print(f"[skip] Converting {ext} requires pandoc (https://pandoc.org/installing.html)",
              file=sys.stderr)
        sys.exit(2)
    html_to_pdf(html, a.output, a.paper)
    print(f"Converted {src} -> {a.output}")


def build_parser():
    p = argparse.ArgumentParser(description="PDF / document toolkit")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("merge", help="Merge PDFs")
    s.add_argument("inputs", nargs="+")
    s.add_argument("-o", "--output", required=True)
    s.set_defaults(func=cmd_merge)

    s = sub.add_parser("split", help="Split PDF into pages/chunks")
    s.add_argument("input")
    s.add_argument("-d", "--outdir", default="split_out")
    s.add_argument("--every", type=int, default=1, help="Pages per output file (default 1)")
    s.set_defaults(func=cmd_split)

    s = sub.add_parser("extract", help="Extract pages, e.g. --pages 1-3,5")
    s.add_argument("input")
    s.add_argument("-p", "--pages", required=True)
    s.add_argument("-o", "--output", required=True)
    s.set_defaults(func=cmd_extract)

    s = sub.add_parser("rotate", help="Rotate pages")
    s.add_argument("input")
    s.add_argument("-a", "--angle", type=int, choices=[90, 180, 270], default=90)
    s.add_argument("-p", "--pages", help="Page ranges (default: all)")
    s.add_argument("-o", "--output", required=True)
    s.set_defaults(func=cmd_rotate)

    s = sub.add_parser("compress", help="Compress/optimize PDF")
    s.add_argument("input")
    s.add_argument("-o", "--output", required=True)
    s.add_argument("--image-dpi", type=int, default=0,
                   help="Downscale embedded images to this DPI (e.g. 120). 0 = keep")
    s.add_argument("--quality", type=int, default=75, help="JPEG quality for downscaled images")
    s.set_defaults(func=cmd_compress)

    s = sub.add_parser("to-images", help="Render PDF pages to images")
    s.add_argument("input")
    s.add_argument("-d", "--outdir", default="pdf_images")
    s.add_argument("--dpi", type=int, default=150)
    s.add_argument("-f", "--format", choices=["png", "jpg"], default="png")
    s.add_argument("-p", "--pages")
    s.set_defaults(func=cmd_to_images)

    s = sub.add_parser("from-images", help="Combine images into a PDF")
    s.add_argument("inputs", nargs="+")
    s.add_argument("-o", "--output", required=True)
    s.add_argument("--page-size", help="Fit onto fixed paper size, e.g. a4, letter")
    s.add_argument("--margin", type=float, default=20, help="Margin in points (with --page-size)")
    s.set_defaults(func=cmd_from_images)

    s = sub.add_parser("text", help="Extract embedded text")
    s.add_argument("input")
    s.add_argument("-p", "--pages")
    s.add_argument("-o", "--output")
    s.set_defaults(func=cmd_text)

    s = sub.add_parser("ocr", help="OCR a scanned PDF or image (tesseract)")
    s.add_argument("input")
    s.add_argument("-l", "--lang", default="chi_tra+eng")
    s.add_argument("--dpi", type=int, default=300)
    s.add_argument("-o", "--output")
    s.set_defaults(func=cmd_ocr)

    s = sub.add_parser("convert", help="DOCX/Markdown/HTML -> PDF")
    s.add_argument("input")
    s.add_argument("-o", "--output", required=True)
    s.add_argument("--paper", default="a4")
    s.set_defaults(func=cmd_convert)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except (ValueError, FileNotFoundError, RuntimeError) as e:
        sys.exit(f"Error: {e}")


if __name__ == "__main__":
    main()
