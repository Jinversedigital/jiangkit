"""JSONL + CSV storage with id-dedupe and resume support."""
from __future__ import annotations

from jiangkit.security.csvsafe import SafeDictWriter, SafeWriter  # CSV formula-injection escaping
import csv
import json
from pathlib import Path

from .parser import FIELDS


class TweetStore:
    """Append-only JSONL store. CSV is regenerated from JSONL on close().

    Resume: when the JSONL already exists (and fresh=False) its ids are loaded
    so re-running the same command only appends new posts.
    """

    def __init__(self, out_prefix: str | Path, fresh: bool = False):
        p = Path(out_prefix)
        if p.suffix in (".jsonl", ".csv"):
            p = p.with_suffix("")
        p.parent.mkdir(parents=True, exist_ok=True)
        self.jsonl = p.with_suffix(".jsonl")
        self.csv = p.with_suffix(".csv")
        self.seen: set[str] = set()
        if fresh:
            for f in (self.jsonl, self.csv):
                f.unlink(missing_ok=True)
        elif self.jsonl.exists():
            for rec in load_jsonl(self.jsonl):
                self.seen.add(str(rec.get("id")))
        self.resumed = len(self.seen)
        self.added = 0
        self._fh = open(self.jsonl, "a", encoding="utf-8")

    def add(self, rec: dict) -> bool:
        """Add a record; returns False if it was a duplicate."""
        rid = str(rec.get("id"))
        if not rid or rid in self.seen:
            return False
        self.seen.add(rid)
        self._fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        self._fh.flush()
        self.added += 1
        return True

    def close(self) -> None:
        self._fh.close()
        write_csv(load_jsonl(self.jsonl), self.csv)


def load_jsonl(path: str | Path) -> list[dict]:
    out = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue  # tolerate a truncated last line after a crash
    return out


def write_csv(records: list[dict], path: str | Path) -> None:
    # utf-8-sig so Excel opens Chinese text correctly.
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        w = SafeDictWriter(fh, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        for r in records:
            row = dict(r)
            for k in ("media_urls", "hashtags"):
                if isinstance(row.get(k), list):
                    row[k] = " | ".join(row[k])
            w.writerow(row)
