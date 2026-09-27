"""JiangKit 匠具 — see README.md."""
import os as _os

# Privacy: JiangKit has no telemetry of its own, and opts out of third-party library telemetry.
for _k, _v in (("GRADIO_ANALYTICS_ENABLED", "False"), ("HF_HUB_DISABLE_TELEMETRY", "1"),
               ("DO_NOT_TRACK", "1"), ("ANONYMIZED_TELEMETRY", "False")):
    _os.environ.setdefault(_k, _v)

from ._brand import BRAND, BRAND_ZH, CLI_NAME  # noqa: E402,F401

__version__ = "1.0.0"
