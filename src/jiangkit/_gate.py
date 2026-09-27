"""Gate for running a tool module directly (python -m jiangkit.x.y): Pro tools check the license."""
from __future__ import annotations

import sys


def run_module(module_name: str, main) -> int:
    from .registry import BY_MODULE

    tool = BY_MODULE.get(module_name)
    if tool is not None and tool.tier == "pro":
        from . import license as lic
        from .errors import EXIT_PRO_REQUIRED
        from .i18n import t
        from ._brand import CLI_NAME

        if not lic.status().is_pro:
            print(t("pro_required", tool=tool.id, cli=CLI_NAME), file=sys.stderr)
            return EXIT_PRO_REQUIRED
    rc = main()
    return int(rc or 0) if not isinstance(rc, bool) else int(rc)
