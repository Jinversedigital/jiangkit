"""JiangKit 匠具 — see README.md."""
import os as _os

# Privacy: JiangKit has no telemetry of its own, and opts out of third-party library telemetry.
for _k, _v in (("GRADIO_ANALYTICS_ENABLED", "False"), ("HF_HUB_DISABLE_TELEMETRY", "1"),
               ("DO_NOT_TRACK", "1"), ("ANONYMIZED_TELEMETRY", "False")):
    _os.environ.setdefault(_k, _v)

# Windows: zh-TW consoles default to cp950, which cannot encode Simplified Chinese, arrows or
# emoji, so piped output (panel, redirects, subprocess tools) crashed with UnicodeEncodeError.
import sys as _sys  # noqa: E402

if _sys.platform == "win32":
    _os.environ.setdefault("PYTHONUTF8", "1")  # child tool processes inherit UTF-8 mode
    for _stream in (_sys.stdout, _sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

from ._brand import BRAND, BRAND_ZH, CLI_NAME  # noqa: E402,F401

__version__ = "1.0.0"
