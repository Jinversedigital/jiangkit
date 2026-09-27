#!/usr/bin/env python3
"""dream_diff.py - a *semantic* diff between two versions of a text.

A normal diff tells you which characters moved.  dream_diff tells you what
the change MEANS, grouping edits into meaning-level shifts:

  numbers changed      "30 days" -> "60 days" (+100%)
  claims added/removed whole new or dropped statements
  polarity reversed    a negation appeared or vanished ("will" -> "will not")
  obligation shifted   must/shall <-> should <-> may (contracts!)
  certainty shifted    hedges vs. boosters (possibly / definitely)
  tone shifted         lexicon sentiment and formality (warmer, colder, more casual)
  entities changed     names/places/organisations swapped
  scope changed        all / some / only / none ...
  moved                same sentence, different place
  rewording only       different words, same meaning signals

Everything is lightweight, local NLP (regexes, lexicons, difflib alignment):
no model downloads, no paid API.  Works best on English; Chinese text gets
sentence splitting, numbers, negation/modality and word-level highlighting.
Outputs a grouped text report (or JSON) and a side-by-side HTML view.
"""
from __future__ import annotations

import argparse
import difflib
import html
import json
import re
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path

# ------------------------------------------------------------------ lexicons
POSITIVE = set("""good great excellent happy glad pleased delighted love wonderful amazing
benefit benefits beneficial success successful win wins improve improved improvement strong
proud hope hopeful bright joy thrilled grateful thanks thank welcome safe secure reliable
fair generous positive progress opportunity opportunities growth gain gains celebrate warm
kind brilliant best better easy clear confident trust trusted support supportive exciting
excited calm peaceful healthy robust effective efficient""".split()) | {"好", "優秀", "成功", "感謝", "希望", "喜歡", "進步", "安全"}
NEGATIVE = set("""bad poor terrible awful sad angry upset hate fail failed failure loss losses
lose risk risks risky danger dangerous threat threats weak worse worst problem problems
crisis concern concerns concerned worry worried fear afraid harm harmful damage damages
penalty penalties breach default decline declined difficult hard unfortunately regret
negative dark cold cruel unfair unsafe liable liability blame disappointing disappointed
delay delayed cut cuts layoffs terminate termination dispute""".split()) | {"失敗", "危險", "風險", "問題", "損失", "擔心", "遺憾", "違約"}
HEDGES = set("""may might could possibly perhaps probably likely unlikely approximately
about around roughly seems appear appears suggest suggests estimated estimate potentially
generally typically usually somewhat""".split()) | {"可能", "也許", "大約", "估計"}
BOOSTERS = set("""definitely certainly clearly always never guaranteed undoubtedly surely
absolutely completely fully must proven unquestionably""".split()) | {"一定", "絕對", "肯定", "保證"}
NEGATIONS = {"not", "no", "never", "none", "nobody", "nothing", "neither", "nor", "without",
             "cannot", "n't", "不", "沒", "沒有", "無", "未", "非", "別", "勿"}
MODALS = {  # obligation strength
    "must": 3, "shall": 3, "required": 3, "require": 3, "requires": 3, "obligated": 3,
    "will": 2.2, "should": 2, "ought": 2, "expected": 2, "may": 1, "might": 1, "can": 1,
    "could": 1, "optional": 0.5, "必須": 3, "應": 2, "應該": 2, "得": 2, "可": 1, "可以": 1, "得以": 1,
}
SCOPE = {"all", "every", "each", "always", "any", "some", "several", "few", "only", "none",
         "most", "many", "entire", "whole", "全部", "所有", "每", "部分", "僅", "只"}
CASUAL = {"gonna", "wanna", "kinda", "hey", "cool", "awesome", "stuff", "yeah", "ok", "okay",
          "super", "totally", "guys", "lol"}
STOP = set("""a an the and or but if of to in on at by for with from as is are was were be
been being this that these those it its we you they he she i our your their his her them us
will would shall should may might can could do does did has have had not no so than then
there here which who whom what when where why how all any each also into over under""".split())

NUM_RE = re.compile(
    r"(?P<cur>[$€£¥])?\s?(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"\s?(?P<unit>%|percent|per cent|million|billion|thousand|k\b|m\b|bn\b|days?|weeks?|months?|"
    r"years?|hours?|minutes?|seconds?|km|kg|miles?|people|employees|users|customers|times|"
    r"USD|EUR|GBP|TWD|NTD|元|萬|億|天|年|個月|小時|人|%)?", re.I)
WORD_RE = re.compile(r"[\u4e00-\u9fff]|[A-Za-z]+(?:'[a-z]+)?|\d[\d,.]*|[^\sA-Za-z\d\u4e00-\u9fff]")
SENT_RE = re.compile(r"(?<=[.!?。！？])[\"')\]」』]?\s+|(?<=[。！？])|\n\s*\n")


# ------------------------------------------------------------------ analysis
def split_sentences(text: str) -> list[str]:
    parts = [p.strip() for p in SENT_RE.split(text)]
    return [p for p in parts if p and re.search(r"\w", p)]


def words(s: str) -> list[str]:
    return [w.lower() for w in WORD_RE.findall(s)]


def content(s: str) -> set[str]:
    return {w for w in words(s) if w not in STOP and re.match(r"\w", w) and len(w) > 1 or
            re.match(r"[\u4e00-\u9fff]", w)}


def has_any(s: str, lex: set[str]) -> list[str]:
    ws = words(s)
    found = [w for w in ws if w in lex]
    low = s.lower()
    found += [w for w in lex if re.match(r"[\u4e00-\u9fff]", w) and w in low and w not in found]
    if "n't" in lex:
        found += ["n't"] * low.count("n't")
    return found


def numbers(s: str) -> list[dict]:
    out = []
    for m in NUM_RE.finditer(s):
        raw = m.group(0).strip()
        try:
            val = float(m.group("num").replace(",", ""))
        except ValueError:
            continue
        unit = (m.group("unit") or "").lower().rstrip("s") if m.group("unit") else ""
        out.append({"raw": raw, "value": val, "unit": (m.group("cur") or "") + unit})
    return out


def sentiment(s: str) -> float:
    """Lexicon sentiment with simple negation handling: a polar word within
    five tokens after a negation flips sign ("no penalty" is not negative)."""
    ws = words(s)
    score = 0.0
    last_neg = -99
    for i, w in enumerate(ws):
        if w in NEGATIONS or w.endswith("n't"):
            last_neg = i
            continue
        pol = 1 if w in POSITIVE else -1 if w in NEGATIVE else 0
        if pol and i - last_neg <= 5:
            pol = -pol * 0.5          # negated polarity is weaker than the opposite word
        score += pol
    low = s.lower()
    score += sum(1 for w in POSITIVE if re.match(r"[\u4e00-\u9fff]", w) and w in low)
    score -= sum(1 for w in NEGATIVE if re.match(r"[\u4e00-\u9fff]", w) and w in low)
    return score / max(1, len(ws)) * 10


def certainty(s: str) -> int:
    return len(has_any(s, BOOSTERS)) - len(has_any(s, HEDGES))


def obligation(s: str) -> float:
    found = [MODALS[w] for w in has_any(s, set(MODALS))]
    return max(found) if found else 0.0


def formality(s: str) -> float:
    ws = words(s)
    casual = len([w for w in ws if w in CASUAL]) + s.count("!") + len(re.findall(r"\w'(?:s|re|ll|ve|d|t)\b", s))
    return -casual / max(1, len(ws)) * 10


def entities(s: str) -> set[str]:
    toks = re.findall(r"\b[A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)*", s)
    first = re.match(r"\s*([A-Z][a-zA-Z]+)", s)
    ents = set(toks)
    if first and first.group(1) in ents and first.group(1).lower() in STOP | MODALS.keys() | SCOPE:
        ents.discard(first.group(1))
    return {e for e in ents if e.lower() not in STOP and e.lower() not in MODALS}


def similarity(a: str, b: str) -> float:
    ca, cb = content(a), content(b)
    jac = len(ca & cb) / max(1, len(ca | cb))
    return 0.5 * jac + 0.5 * difflib.SequenceMatcher(None, words(a), words(b)).ratio()


@dataclass
class Change:
    kind: str                       # 'modified' | 'added' | 'removed' | 'moved' | 'same'
    old: str = ""
    new: str = ""
    tags: list[str] = field(default_factory=list)
    details: list[str] = field(default_factory=list)


def compare_pair(old: str, new: str) -> Change:
    ch = Change("modified", old, new)
    # numbers: compare as multisets, pair by unit
    no, nn = numbers(old), numbers(new)
    ro, rn = sorted(x["raw"] for x in no), sorted(x["raw"] for x in nn)
    if ro != rn:
        ch.tags.append("numbers")
        used = set()
        for a in no:
            if a["raw"] in rn:
                continue
            match = next((j for j, b in enumerate(nn) if j not in used and b["unit"] == a["unit"]
                          and b["raw"] not in ro), None)
            if match is None:
                ch.details.append(f"number removed: {a['raw']}")
            else:
                used.add(match)
                b = nn[match]
                pct = f" ({(b['value'] - a['value']) / a['value'] * 100:+.0f}%)" if a["value"] else ""
                ch.details.append(f"number changed: {a['raw']} -> {b['raw']}{pct}")
        for j, b in enumerate(nn):
            if j not in used and b["raw"] not in ro:
                ch.details.append(f"number added: {b['raw']}")
    # polarity
    neg_o, neg_n = len(has_any(old, NEGATIONS)), len(has_any(new, NEGATIONS))
    if neg_o % 2 != neg_n % 2:
        ch.tags.append("polarity")
        ch.details.append("negation " + ("added: statement now denied" if neg_n > neg_o
                                         else "removed: statement now affirmed"))
    # obligation
    ob_o, ob_n = obligation(old), obligation(new)
    if ob_o != ob_n and (ob_o or ob_n):
        ch.tags.append("obligation")
        mo = ",".join(sorted(set(has_any(old, set(MODALS))))) or "none"
        mn = ",".join(sorted(set(has_any(new, set(MODALS))))) or "none"
        ch.details.append(f"obligation {'strengthened' if ob_n > ob_o else 'weakened'}: {mo} -> {mn}")
    # certainty
    ce_o, ce_n = certainty(old), certainty(new)
    if ce_o != ce_n:
        ch.tags.append("certainty")
        ch.details.append("more certain / assertive" if ce_n > ce_o else "more hedged / cautious")
    # tone
    se_o, se_n = sentiment(old), sentiment(new)
    if abs(se_n - se_o) >= 0.4:
        ch.tags.append("tone")
        ch.details.append(f"tone {'warmer / more positive' if se_n > se_o else 'colder / more negative'}"
                          f" ({se_o:+.1f} -> {se_n:+.1f})")
    fo_o, fo_n = formality(old), formality(new)
    if abs(fo_n - fo_o) >= 0.5:
        if "tone" not in ch.tags:
            ch.tags.append("tone")
        ch.details.append("more casual" if fo_n < fo_o else "more formal")
    # entities
    eo, en = entities(old), entities(new)
    if eo != en:
        added, removed = en - eo, eo - en
        if added or removed:
            ch.tags.append("entities")
            ch.details.append("entities: " + ", ".join([f"-{e}" for e in sorted(removed)] +
                                                       [f"+{e}" for e in sorted(added)]))
    # scope
    so, sn = sorted(has_any(old, SCOPE)), sorted(has_any(new, SCOPE))
    if so != sn:
        ch.tags.append("scope")
        ch.details.append(f"scope words: {' '.join(so) or '-'} -> {' '.join(sn) or '-'}")
    # content words that appeared/disappeared (claims inside a modified sentence)
    signal_words = SCOPE | set(MODALS) | NEGATIONS | HEDGES | BOOSTERS
    co, cn = content(old) - signal_words, content(new) - signal_words
    gained = cn - co - {w for x in nn for w in words(x["raw"])}
    lost = co - cn - {w for x in no for w in words(x["raw"])}
    if not ch.tags:
        ch.tags.append("rewording")
        ch.details.append(f"similarity {similarity(old, new):.2f}")
    if gained or lost:
        ch.details.append("words: " + " ".join([f"-{w}" for w in sorted(lost)][:8] +
                                               [f"+{w}" for w in sorted(gained)][:8]))
    return ch


def describe_claim(s: str, sign: str) -> Change:
    ch = Change("added" if sign == "+" else "removed", new=s if sign == "+" else "",
                old=s if sign == "-" else "")
    ch.tags.append("claim_added" if sign == "+" else "claim_removed")
    extras = []
    if numbers(s):
        extras.append("quantitative: " + ", ".join(x["raw"] for x in numbers(s)))
    if obligation(s) >= 2.5:
        extras.append("imposes an obligation")
    se = sentiment(s)
    if abs(se) >= 0.4:
        extras.append("positive" if se > 0 else "negative")
    if s.rstrip().endswith("?"):
        extras.append("question, not an assertion")
    ch.details.extend(extras)
    return ch


def semantic_diff(old_text: str, new_text: str, pair_threshold: float = 0.3) -> list[Change]:
    a, b = split_sentences(old_text), split_sentences(new_text)
    norm = lambda s: " ".join(words(s))
    sm = difflib.SequenceMatcher(None, [norm(x) for x in a], [norm(x) for x in b], autojunk=False)
    changes: list[Change] = []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            changes.extend(Change("same", a[i], b[j1 + k]) for k, i in enumerate(range(i1, i2)))
            continue
        olds, news = list(range(i1, i2)), list(range(j1, j2))
        # greedy best-similarity pairing inside the changed block
        pairs = sorted(((similarity(a[i], b[j]), i, j) for i in olds for j in news), reverse=True)
        used_i, used_j, matched = set(), set(), {}
        for sim, i, j in pairs:
            if sim < pair_threshold or i in used_i or j in used_j:
                continue
            used_i.add(i)
            used_j.add(j)
            matched[j] = i
        # emit in new-document order, removals where they were
        pending_old = [i for i in olds if i not in used_i]
        for j in news:
            while pending_old and (j in matched and pending_old[0] < matched[j]):
                changes.append(describe_claim(a[pending_old.pop(0)], "-"))
            if j in matched:
                changes.append(compare_pair(a[matched[j]], b[j]))
            else:
                changes.append(describe_claim(b[j], "+"))
        for i in pending_old:
            changes.append(describe_claim(a[i], "-"))
    # detect moves: a removed sentence re-appearing as an added one elsewhere
    removed = [c for c in changes if c.kind == "removed"]
    for c in changes:
        if c.kind != "added":
            continue
        for r in removed:
            if r.kind == "removed" and similarity(r.old, c.new) > 0.85:
                c.kind, c.old, c.tags, c.details = "moved", r.old, ["moved"], ["sentence relocated"]
                r.kind = "_consumed"
                break
    return [c for c in changes if c.kind != "_consumed"]


CATEGORY_TITLES = {
    "numbers": "Numbers changed", "claim_added": "Claims added", "claim_removed": "Claims removed",
    "polarity": "Polarity reversed", "obligation": "Obligation shifted",
    "certainty": "Certainty shifted", "tone": "Tone shifted", "entities": "Entities changed",
    "scope": "Scope changed", "moved": "Moved", "rewording": "Rewording only",
}


def doc_profile(text: str) -> dict:
    sents = split_sentences(text)
    return {"sentences": len(sents), "words": len(words(text)),
            "sentiment": round(sum(sentiment(s) for s in sents) / max(1, len(sents)), 2),
            "certainty": sum(certainty(s) for s in sents),
            "obligations": sum(1 for s in sents if obligation(s) >= 2.5)}


def summarize(changes: list[Change], old_text: str, new_text: str) -> dict:
    groups: dict[str, list[int]] = {k: [] for k in CATEGORY_TITLES}
    for idx, c in enumerate(changes):
        for t in c.tags:
            groups[t].append(idx)
    po, pn = doc_profile(old_text), doc_profile(new_text)
    headline = []
    if pn["sentiment"] - po["sentiment"] >= 0.2:
        headline.append("the new version reads more positive")
    elif po["sentiment"] - pn["sentiment"] >= 0.2:
        headline.append("the new version reads more negative")
    if pn["certainty"] > po["certainty"]:
        headline.append("it sounds more certain")
    elif pn["certainty"] < po["certainty"]:
        headline.append("it hedges more")
    if pn["obligations"] != po["obligations"]:
        headline.append(f"hard obligations {po['obligations']} -> {pn['obligations']}")
    for k in ("numbers", "claim_added", "claim_removed", "polarity"):
        if groups[k]:
            headline.append(f"{len(groups[k])} x {CATEGORY_TITLES[k].lower()}")
    return {"old": po, "new": pn, "groups": {k: v for k, v in groups.items() if v},
            "headline": "; ".join(headline) or "no meaning-level changes detected"}


def text_report(changes: list[Change], summary: dict) -> str:
    out = [f"SEMANTIC DIFF: {summary['headline']}", ""]
    for k, idxs in summary["groups"].items():
        out.append(f"== {CATEGORY_TITLES[k]} ({len(idxs)})")
        for i in idxs:
            c = changes[i]
            if c.old:
                out.append(f"   - {c.old[:140]}")
            if c.new:
                out.append(f"   + {c.new[:140]}")
            for d in c.details:
                out.append(f"       * {d}")
        out.append("")
    return "\n".join(out)


# ------------------------------------------------------------------ HTML
def word_diff_html(a: str, b: str) -> tuple[str, str]:
    ta, tb = re.findall(r"\s+|[\u4e00-\u9fff]|\w+|[^\w\s]", a), re.findall(r"\s+|[\u4e00-\u9fff]|\w+|[^\w\s]", b)
    sm = difflib.SequenceMatcher(None, ta, tb, autojunk=False)
    oa, ob = [], []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        sa, sb = html.escape("".join(ta[i1:i2])), html.escape("".join(tb[j1:j2]))
        if op == "equal":
            oa.append(sa)
            ob.append(sb)
        else:
            if sa:
                oa.append(f"<del>{sa}</del>")
            if sb:
                ob.append(f"<ins>{sb}</ins>")
    return "".join(oa), "".join(ob)


TAG_COLORS = {"numbers": "#ef6c00", "claim_added": "#2e7d32", "claim_removed": "#c62828",
              "polarity": "#6a1b9a", "obligation": "#1565c0", "certainty": "#00838f",
              "tone": "#ad1457", "entities": "#4e342e", "scope": "#827717", "moved": "#546e7a",
              "rewording": "#9e9e9e"}


def html_report(changes: list[Change], summary: dict, name_a: str, name_b: str) -> str:
    chips = "".join(f'<a class="chip" style="background:{TAG_COLORS[k]}" href="#{k}">'
                    f'{CATEGORY_TITLES[k]} &times; {len(v)}</a>' for k, v in summary["groups"].items())
    rows = []
    first_of = {}
    for idx, c in enumerate(changes):
        for t in c.tags:
            first_of.setdefault(t, idx)
    for idx, c in enumerate(changes):
        anchors = "".join(f'<span id="{t}"></span>' for t, i in first_of.items() if i == idx)
        if c.kind == "same":
            l, r = html.escape(c.old), html.escape(c.new)
            cls = "same"
        elif c.kind in ("modified", "moved"):
            l, r = word_diff_html(c.old, c.new)
            cls = c.kind
        elif c.kind == "added":
            l, r = "", f"<ins>{html.escape(c.new)}</ins>"
            cls = "added"
        else:
            l, r = f"<del>{html.escape(c.old)}</del>", ""
            cls = "removed"
        badges = "".join(f'<span class="badge" style="background:{TAG_COLORS[t]}">'
                         f'{CATEGORY_TITLES[t]}</span>' for t in c.tags)
        det = "".join(f"<li>{html.escape(d)}</li>" for d in c.details)
        rows.append(f'<tr class="{cls}"><td>{anchors}{l}</td><td>{r}</td>'
                    f'<td>{badges}<ul>{det}</ul></td></tr>')
    po, pn = summary["old"], summary["new"]
    prof = "".join(f"<tr><td>{html.escape(str(k))}</td><td>{html.escape(str(po[k]))}</td>"
                   f"<td>{html.escape(str(pn[k]))}</td></tr>" for k in po)
    return f"""<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data: file: 'self'; style-src 'unsafe-inline'"><title>dream diff</title><style>
body{{font-family:Georgia,'Noto Serif TC',serif;margin:24px;background:#faf8f3;color:#222}}
h1{{font-family:system-ui}}.chip{{color:#fff;padding:4px 10px;border-radius:14px;margin:3px;display:inline-block;text-decoration:none;font:13px system-ui}}
table.sbs{{border-collapse:collapse;width:100%;table-layout:fixed}}.sbs td{{vertical-align:top;padding:8px;border-bottom:1px solid #e3ded3}}
.sbs td:nth-child(3){{width:24%;font:12px system-ui}}tr.same td{{color:#888}}tr.added td:nth-child(2){{background:#e8f5e9}}
tr.removed td:nth-child(1){{background:#ffebee}}tr.modified td{{background:#fffdf0}}del{{background:#ffcdd2;text-decoration:line-through}}ins{{background:#c8e6c9;text-decoration:none}}
.badge{{color:#fff;border-radius:4px;padding:1px 6px;margin-right:3px;display:inline-block;margin-bottom:2px}}ul{{margin:4px 0;padding-left:16px}}
.prof td{{padding:2px 10px;font:13px system-ui}}</style></head><body>
<h1>Semantic diff</h1><p><b>{html.escape(summary['headline'])}</b></p><div>{chips}</div>
<table class="prof"><tr><th></th><th>{html.escape(name_a)}</th><th>{html.escape(name_b)}</th></tr>{prof}</table>
<table class="sbs"><tr><th>{html.escape(name_a)}</th><th>{html.escape(name_b)}</th><th>meaning</th></tr>{''.join(rows)}</table>
</body></html>"""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Semantic diff of two text versions: groups edits into meaning-level shifts "
                    "(numbers, claims, tone, obligation, polarity...) with a side-by-side HTML view.",
        epilog="Example: dream_diff.py contract_v1.txt contract_v2.txt --html diff.html")
    ap.add_argument("old")
    ap.add_argument("new")
    ap.add_argument("--html", help="write side-by-side HTML report")
    ap.add_argument("--json", action="store_true", help="print JSON instead of text")
    ap.add_argument("--threshold", type=float, default=0.3,
                    help="similarity needed to treat two sentences as versions of each other")
    args = ap.parse_args(argv)
    ta = Path(args.old).read_text(encoding="utf-8", errors="replace")
    tb = Path(args.new).read_text(encoding="utf-8", errors="replace")
    changes = semantic_diff(ta, tb, args.threshold)
    summary = summarize(changes, ta, tb)
    if args.json:
        print(json.dumps({"summary": summary, "changes": [asdict(c) for c in changes
                                                          if c.kind != "same"]},
                         ensure_ascii=False, indent=2))
    else:
        print(text_report(changes, summary))
    if args.html:
        Path(args.html).write_text(html_report(changes, summary, Path(args.old).name,
                                               Path(args.new).name), encoding="utf-8")
        if not args.json:
            print(f"HTML -> {args.html}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
