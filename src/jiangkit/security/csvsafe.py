"""CSV / spreadsheet formula-injection protection (OWASP 'CSV Injection').

Cells beginning with = + - @ TAB or CR can be interpreted as formulas by Excel/LibreOffice/Sheets.
We prefix such text cells with a single quote:
  * "=", TAB, CR (and full-width "＝") at the start -> always escaped
  * "+", "-", "@" (and full-width forms) at the start -> escaped when the cell also contains a
    formula/DDE metacharacter  ( ) | ! ; = $ " -- so harmless data such as "@handle",
    "- bullet" or "-3.5" round-trips unchanged while "@SUM(A1)", "+cmd|'/c calc'!A0",
    "-2+3=1" are neutralised.
"""
from __future__ import annotations

import re
from typing import Any, Iterable, Mapping

_ALWAYS = ("=", "\t", "\r", "\uff1d")
_SOFT = ("+", "-", "@", "\uff0b", "\uff0d", "\uff20")
_META = re.compile(r"[()|!;=$\"\uff08\uff09\uff1d]")
_NUMBER = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$")


def escape_cell(value: Any) -> Any:
    if not isinstance(value, str) or not value:
        return value
    if value.startswith(_ALWAYS):
        return "'" + value
    if value.startswith(_SOFT) and not _NUMBER.match(value) and _META.search(value):
        return "'" + value
    return value


def escape_row(row: Iterable[Any]) -> list[Any]:
    return [escape_cell(v) for v in row]


def escape_dict(row: Mapping[str, Any]) -> dict[str, Any]:
    return {k: escape_cell(v) for k, v in row.items()}


def escape_dataframe(df):
    """Return a copy of a pandas DataFrame with text cells (and headers) escaped."""
    out = df.copy()
    for col in out.columns:
        if out[col].dtype == object or str(out[col].dtype).startswith(("string", "str")):
            out[col] = out[col].map(escape_cell)
    out.columns = [escape_cell(c) if isinstance(c, str) else c for c in out.columns]
    return out


class SafeDictWriter:
    """Drop-in wrapper around csv.DictWriter that escapes every row."""

    def __init__(self, fh, fieldnames, **kw):
        import csv

        self._w = csv.DictWriter(fh, fieldnames=fieldnames, **kw)

    def writeheader(self):
        return self._w.writeheader()

    def writerow(self, row):
        return self._w.writerow(escape_dict(row))

    def writerows(self, rows):
        for r in rows:
            self.writerow(r)


class SafeWriter:
    """Drop-in wrapper around csv.writer that escapes every row."""

    def __init__(self, fh, **kw):
        import csv

        self._w = csv.writer(fh, **kw)

    def writerow(self, row):
        return self._w.writerow(escape_row(row))

    def writerows(self, rows):
        for r in rows:
            self.writerow(r)
