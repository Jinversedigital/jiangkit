"""Privacy helpers for image export: strip EXIF/GPS/XMP/ICC-comment metadata by default."""
from __future__ import annotations

import os

from PIL import Image

KEEP_METADATA_ENV = "JIANGKIT_KEEP_METADATA"


def keep_metadata_default() -> bool:
    return os.environ.get(KEEP_METADATA_ENV) == "1"


def strip_metadata(img: Image.Image) -> Image.Image:
    """Return a copy of *img* with no EXIF/XMP/comments (pixels + ICC profile kept)."""
    fresh = img.copy()
    fresh.info = {k: v for k, v in img.info.items() if k in ("icc_profile", "transparency", "duration", "loop")}
    return fresh


def save_clean(img: Image.Image, path, keep_metadata: bool | None = None, **save_kw) -> None:
    """Save *img* without metadata unless keep_metadata=True (or JIANGKIT_KEEP_METADATA=1)."""
    if keep_metadata is None:
        keep_metadata = keep_metadata_default()
    if keep_metadata:
        exif = img.info.get("exif") or img.getexif()
        if exif and "exif" not in save_kw:
            save_kw["exif"] = exif
        img.save(path, **save_kw)
        return
    save_kw.pop("exif", None)
    save_kw.pop("pnginfo", None)
    icc = img.info.get("icc_profile")
    if icc and "icc_profile" not in save_kw:
        save_kw["icc_profile"] = icc
    # Pillow only writes EXIF when passed explicitly, but re-encode via a fresh image so
    # text chunks / comments / XMP carried in .info are never propagated either.
    fresh = img.copy()
    fresh.info = {}
    fresh.save(path, **save_kw)
