#!/usr/bin/env python3
"""regex_from_examples.py - synthesize a regular expression from examples.

Give it strings that SHOULD match and strings that should NOT; it searches
for a small, readable regex that fully matches every positive and none of
the negatives, then explains that regex step by step in Traditional Chinese.

Search strategy:
  1. Seeds: every positive is tokenised into character-class runs
     (digits, lower, upper, literals); positives with the same shape are
     merged into one pattern with length ranges, plus generalised variants.
  2. A small genetic algorithm evolves token sequences with mutations
     (generalise/specialise a class, widen/narrow/relax a quantifier,
     insert/delete/duplicate/merge tokens) and one-point crossover.
     Fitness = accuracy on the examples first, then a simplicity cost that
     prefers exact lengths and narrow classes over `.*`-style patterns.
  3. If no single sequence is perfect, a top-level alternation of per-shape
     patterns is tried as a fallback.

The explainer walks Python's own regex parse tree, so `--explain` also works
for regexes you did not generate here.
"""
from __future__ import annotations

import argparse
import random
import re
import sys
from dataclasses import dataclass

try:
    import re._parser as sre_parse          # Python 3.11+
    from re._constants import *  # noqa: F401,F403
    import re._constants as C
except ImportError:  # pragma: no cover
    import sre_parse                       # type: ignore
    import sre_constants as C              # type: ignore

# ------------------------------------------------------------------ token model
CLASSES = {  # name -> (regex, generality cost, test)
    "d": (r"\d", 0.5, str.isdigit),
    "lower": ("[a-z]", 0.5, lambda c: c.isascii() and c.islower()),
    "upper": ("[A-Z]", 0.5, lambda c: c.isascii() and c.isupper()),
    "alpha": ("[A-Za-z]", 1.0, lambda c: c.isascii() and c.isalpha()),
    "alnum": ("[A-Za-z0-9]", 1.5, lambda c: c.isascii() and c.isalnum()),
    "w": (r"\w", 1.5, lambda c: c.isalnum() or c == "_"),
    "s": (r"\s", 0.8, str.isspace),
    "any": (".", 3.0, lambda c: c != "\n"),
}
# generalisation lattice (each class can widen to these)
WIDEN = {"d": ["alnum", "w"], "lower": ["alpha"], "upper": ["alpha"], "alpha": ["alnum"],
         "alnum": ["w"], "w": ["any"], "s": ["any"], "any": []}
NARROW = {v: k for k, vs in WIDEN.items() for v in vs}
MODEL_WEIGHT = 1.0
LOG_ALPHABET = 6.6   # log2(95 printable ASCII characters)
SPECIAL = set(".^$*+?{}[]\\|()")
# how a class maps onto character-set items (for merging tokens into [...])
CLASS_ITEMS = {"d": {"0-9"}, "lower": {"a-z"}, "upper": {"A-Z"}, "alpha": {"a-z", "A-Z"},
               "alnum": {"a-z", "A-Z", "0-9"}}


def set_items(t: "Tok") -> set | None:
    if t.kind == "lit":
        return {t.val}
    if t.kind == "set":
        return set(t.val)
    return CLASS_ITEMS.get(t.val)


def merge_tokens(a: "Tok", b: "Tok", open_ended: bool = False) -> "Tok | None":
    """Fuse two adjacent tokens into one (same atom, or a character set)."""
    hi = None if open_ended or a.hi is None or b.hi is None else a.hi + b.hi
    lo = a.lo + b.lo
    if (a.kind, a.val) == (b.kind, b.val):
        return Tok(a.kind, a.val, lo, hi)
    ia, ib = set_items(a), set_items(b)
    if ia is None or ib is None:
        return None
    return Tok("set", frozenset(ia | ib), lo, hi)


@dataclass(frozen=True)
class Tok:
    kind: str            # 'lit' | 'cls' | 'set'
    val: object          # char | class name | frozenset of chars
    lo: int = 1
    hi: int | None = 1   # None = unbounded

    def atom(self) -> str:
        if self.kind == "lit":
            return "\\" + self.val if self.val in SPECIAL else self.val
        if self.kind == "cls":
            return CLASSES[self.val][0]
        # set: items are single chars or ranges like "a-z"
        ranges = sorted(i for i in self.val if len(i) == 3)
        chars = sorted(i for i in self.val if len(i) == 1)
        return "[" + "".join(ranges) + "".join("\\" + c if c in "]\\^-[" else c
                                               for c in chars) + "]"

    def quant(self) -> str:
        lo, hi = self.lo, self.hi
        if lo == hi == 1:
            return ""
        if hi is None:
            return "+" if lo == 1 else "*" if lo == 0 else f"{{{lo},}}"
        if lo == 0 and hi == 1:
            return "?"
        return f"{{{lo}}}" if lo == hi else f"{{{lo},{hi}}}"

    def cost(self) -> float:
        """Model cost in bits (MDL): what it takes to write this token down."""
        import math
        if self.kind == "lit":
            c = LOG_ALPHABET                                  # which character
        elif self.kind == "cls":
            c = 3.0 + 0.5 * CLASSES[self.val][1]              # which class (+ tie-break)
        else:
            c = 2.0 + sum(4.0 if len(i) == 3 else LOG_ALPHABET for i in self.val)
        lo, hi = self.lo, self.hi
        if lo == hi == 1:
            q = 1.0
        elif hi is None:
            q = 2.0 + 2 * math.log2(lo + 1)
        elif lo == hi:
            q = 1.0 + 2 * math.log2(lo + 1)
        else:
            q = 2.0 + 2 * math.log2(lo + 1) + 2 * math.log2(hi + 1)
        return c + q

CLASS_SIZE = {"d": 10, "lower": 26, "upper": 26, "alpha": 52, "alnum": 62, "w": 63, "s": 6,
              "any": 95}
RANGE_TEST = {"a-z": lambda c: "a" <= c <= "z", "A-Z": lambda c: "A" <= c <= "Z",
              "0-9": lambda c: "0" <= c <= "9"}


def atom_size(t: Tok) -> int:
    if t.kind == "lit":
        return 1
    if t.kind == "cls":
        return CLASS_SIZE[t.val]
    return sum(26 if i in ("a-z", "A-Z") else 10 if i == "0-9" else 1 for i in t.val)


def atom_accepts(t: Tok, c: str) -> bool:
    if t.kind == "lit":
        return c == t.val
    if t.kind == "cls":
        return CLASSES[t.val][2](c)
    return any(RANGE_TEST[i](c) if len(i) == 3 else c == i for i in t.val)


def data_bits(toks: list[Tok], s: str) -> float:
    """MDL data cost: bits needed to pick `s` among strings the pattern allows.

    Wide classes cost log2(|class|) bits per character and loose quantifiers
    cost bits for the repetition count, so an over-general regex like
    `[0-9-]{10}` pays more than `\\d{4}-\\d{2}-\\d{2}` for the same data."""
    import math
    from functools import lru_cache
    n_t, L = len(toks), len(s)
    ok = [[atom_accepts(t, c) for c in s] for t in toks]
    per_char = [math.log2(atom_size(t)) for t in toks]

    @lru_cache(maxsize=None)
    def best(ti: int, pos: int) -> float:
        if ti == n_t:
            return 0.0 if pos == L else math.inf
        t = toks[ti]
        top = L - pos if t.hi is None else min(t.hi, L - pos)
        res = math.inf
        run_ok = True
        for k in range(0, top + 1):
            if k > 0:
                run_ok = run_ok and ok[ti][pos + k - 1]
                if not run_ok:
                    break
            if k < t.lo:
                continue
            if t.hi is None:
                count_bits = 2 * math.log2(1 + k - t.lo) + 1
            else:
                count_bits = math.log2(t.hi - t.lo + 1)
            res = min(res, k * per_char[ti] + count_bits + best(ti + 1, pos + k))
        return res

    return best(0, 0)


def render(toks: list[Tok]) -> str:
    return "".join(t.atom() + t.quant() for t in toks)


def char_class(c: str) -> str | None:
    if c.isdigit():
        return "d"
    if c.isascii() and c.islower():
        return "lower"
    if c.isascii() and c.isupper():
        return "upper"
    if c.isspace():
        return "s"
    return None


def tokenize(s: str, letters_literal: bool = False) -> list[Tok]:
    """Run-length tokenise a string by character class.

    With letters_literal=True, letters stay literal (only digits/space become
    classes), which lets fixed words/prefixes such as 'v' in 'v1.2' survive."""
    toks: list[Tok] = []
    i = 0
    while i < len(s):
        c = s[i]
        k = char_class(c)
        if letters_literal and k in ("lower", "upper"):
            k = None
        j = i + 1
        if k:
            while j < len(s) and char_class(s[j]) == k:
                j += 1
            toks.append(Tok("cls", k, j - i, j - i))
        else:
            while j < len(s) and s[j] == c:
                j += 1
            toks.append(Tok("lit", c, j - i, j - i))
        i = j
    return toks


def merge_shapes(tok_lists: list[list[Tok]]) -> list[Tok] | None:
    """Merge tokenisations with identical atoms into ranges; None if shapes differ."""
    shape = [(t.kind, t.val) for t in tok_lists[0]]
    if any([(t.kind, t.val) for t in tl] != shape for tl in tok_lists):
        return None
    out = []
    for i, (k, v) in enumerate(shape):
        los = [tl[i].lo for tl in tok_lists]
        out.append(Tok(k, v, min(los), max(los)))
    return out


# ------------------------------------------------------------------ search
class Synth:
    def __init__(self, pos: list[str], neg: list[str], seed: int = 0):
        self.pos, self.neg = pos, neg
        self.rng = random.Random(seed)  # nosec B311 - non-cryptographic randomness (timing jitter / visuals), not security relevant
        self.cache: dict[str, tuple] = {}
        self.alphabet = sorted(set("".join(pos)))

    def fitness(self, toks: list[Tok]) -> tuple[float, float, str]:
        pat = render(toks)
        if pat in self.cache:
            return self.cache[pat]
        try:
            rx = re.compile(pat)
            tp = sum(1 for s in self.pos if rx.fullmatch(s))
            tn = sum(1 for s in self.neg if not rx.fullmatch(s))
            acc = (tp + tn) / (len(self.pos) + len(self.neg))
        except (re.error, RecursionError, OverflowError):
            acc = -1.0
        if acc > 0:
            bits = sum(data_bits(toks, p) for p in self.pos if rx.fullmatch(p))
        else:
            bits = 0.0
        # MDL: model cost (pattern size) + data cost (how loosely it fits the positives)
        res = (acc, -(MODEL_WEIGHT * sum(t.cost() for t in toks) + bits), pat)
        self.cache[pat] = res
        return res

    def seeds(self) -> list[list[Tok]]:
        seeds: list[list[Tok]] = []
        for lit_letters in (False, True):
            tls = [tokenize(p, lit_letters) for p in self.pos]
            seeds.extend(tls)
            groups: dict[tuple, list] = {}
            for tl in tls:
                groups.setdefault(tuple((t.kind, t.val) for t in tl), []).append(tl)
            for g in groups.values():
                m = merge_shapes(g)
                if m:
                    seeds.append(m)
                    seeds.append([Tok(t.kind, t.val, t.lo, None) if t.kind == "cls" else t
                                  for t in m])
        # equal-length positives: one character set per column
        if len({len(p) for p in self.pos}) == 1:
            col = []
            for i in range(len(self.pos[0])):
                chars = {p[i] for p in self.pos}
                col.append(Tok("lit", chars.pop()) if len(chars) == 1
                           else Tok("set", frozenset(chars)))
            seeds.append(col)
        # a very general catch-all seed so the GA can also narrow down
        seeds.append([Tok("cls", "any", 1, None)])
        return seeds

    def mutate(self, toks: list[Tok]) -> list[Tok]:
        toks = list(toks)
        r = self.rng
        if not toks:
            return [Tok("cls", "any", 1, None)]
        i = r.randrange(len(toks))
        t = toks[i]
        op = r.randrange(10)
        if op == 0 and t.kind == "cls" and WIDEN[t.val]:
            toks[i] = Tok("cls", r.choice(WIDEN[t.val]), t.lo, t.hi)
        elif op == 1 and t.kind == "cls" and t.val in NARROW:
            toks[i] = Tok("cls", NARROW[t.val], t.lo, t.hi)
        elif op == 2 and t.kind == "lit":
            k = char_class(t.val)
            if k:
                toks[i] = Tok("cls", k, t.lo, t.hi)
            else:  # punctuation: become a small set with another observed char
                other = r.choice(self.alphabet)
                toks[i] = Tok("set", frozenset({t.val, other}), t.lo, t.hi)
        elif op == 3:  # widen quantifier
            hi = None if (t.hi is None or r.random() < 0.4) else t.hi + 1
            toks[i] = Tok(t.kind, t.val, t.lo, hi)
        elif op == 4:  # narrow / relax lower bound
            lo = max(0, t.lo - 1) if r.random() < 0.5 else t.lo + 1
            hi = t.hi if t.hi is None or t.hi >= lo else lo
            toks[i] = Tok(t.kind, t.val, lo, hi)
        elif op == 5 and t.hi is None:  # bound an open quantifier
            toks[i] = Tok(t.kind, t.val, t.lo, t.lo + r.randrange(0, 4))
        elif op == 6 and len(toks) > 1:
            del toks[i]
        elif op == 7:
            toks.insert(i, t)
        elif op == 8 and i + 1 < len(toks):
            m = merge_tokens(t, toks[i + 1], open_ended=r.random() < 0.5)
            if m:
                toks[i:i + 2] = [m]
        else:  # insert an optional random literal from the positives
            toks.insert(i, Tok("lit", r.choice(self.alphabet), 0 if r.random() < .5 else 1, 1))
        return toks

    def neighbours(self, toks: list[Tok]):
        """Deterministic simplifications tried by the final hill-climb."""
        for i, t in enumerate(toks):
            if len(toks) > 1:
                yield toks[:i] + toks[i + 1:]
            for lo, hi in ((t.lo, None), (1, None), (0, None), (0, t.hi)):
                if (lo, hi) != (t.lo, t.hi) and (hi is None or hi >= lo):
                    yield toks[:i] + [Tok(t.kind, t.val, lo, hi)] + toks[i + 1:]
            if t.kind == "cls":
                for w in WIDEN[t.val]:
                    yield toks[:i] + [Tok("cls", w, t.lo, t.hi)] + toks[i + 1:]
            if t.kind == "lit" and char_class(t.val):
                yield toks[:i] + [Tok("cls", char_class(t.val), t.lo, t.hi)] + toks[i + 1:]
            if t.kind == "cls" and t.lo == t.hi == 1:   # specialise to a fixed character
                for c in self.alphabet:
                    if atom_accepts(t, c):
                        yield toks[:i] + [Tok("lit", c)] + toks[i + 1:]
            if t.kind == "set":
                items = set(t.val)
                for it in items:                        # drop one item
                    if len(items) > 1:
                        yield toks[:i] + [Tok("set", frozenset(items - {it}), t.lo, t.hi)] + toks[i + 1:]
                for rng_name, test in RANGE_TEST.items():  # collapse chars into a range
                    inside = {it for it in items if len(it) == 1 and test(it)}
                    if len(inside) >= 2:
                        yield toks[:i] + [Tok("set", frozenset((items - inside) | {rng_name}),
                                              t.lo, t.hi)] + toks[i + 1:]
            if i + 1 < len(toks):
                for oe in (False, True):
                    m = merge_tokens(t, toks[i + 1], oe)
                    if m:
                        yield toks[:i] + [m] + toks[i + 2:]

    def polish(self, toks: list[Tok]) -> list[Tok]:
        best, bf = toks, self.fitness(toks)
        improved = True
        while improved:
            improved = False
            for nb in self.neighbours(best):
                f = self.fitness(nb)
                if f[0] >= bf[0] and f[1] > bf[1]:
                    best, bf, improved = nb, f, True
                    break
        return best

    def crossover(self, a: list[Tok], b: list[Tok]) -> list[Tok]:
        i = self.rng.randrange(len(a) + 1)
        j = self.rng.randrange(len(b) + 1)
        child = a[:i] + b[j:]
        return child or a

    def run(self, population: int = 150, generations: int = 120) -> tuple[str, float]:
        pop = self.seeds()
        while len(pop) < population:
            pop.append(self.mutate(self.rng.choice(pop)))
        best = max(pop, key=self.fitness)
        stale = 0
        for _ in range(generations):
            ranked = sorted(pop, key=self.fitness, reverse=True)
            elite = ranked[: max(4, population // 10)]
            nxt = list(elite)
            while len(nxt) < population:
                # tournament selection
                p1 = max(self.rng.sample(ranked, 3), key=self.fitness)
                if self.rng.random() < 0.3:
                    p2 = max(self.rng.sample(ranked, 3), key=self.fitness)
                    child = self.crossover(p1, p2)
                else:
                    child = p1
                for _ in range(1 + int(self.rng.random() < 0.3)):
                    child = self.mutate(child)
                if len(child) <= 24:
                    nxt.append(child)
            pop = nxt
            cand = max(pop, key=self.fitness)
            if self.fitness(cand) > self.fitness(best):
                best, stale = cand, 0
            else:
                stale += 1
            if stale > 40 and self.fitness(best)[0] == 1.0:
                break
        best = self.polish(best)
        acc, _, pat = self.fitness(best)
        if acc < 1.0:
            alt = self.alternation_fallback()
            if alt and self.score_pattern(alt) > acc:
                return alt, self.score_pattern(alt)
        return pat, acc

    def score_pattern(self, pat: str) -> float:
        rx = re.compile(pat)
        tp = sum(1 for s in self.pos if rx.fullmatch(s))
        tn = sum(1 for s in self.neg if not rx.fullmatch(s))
        return (tp + tn) / (len(self.pos) + len(self.neg))

    def alternation_fallback(self) -> str | None:
        groups: dict[tuple, list] = {}
        for p in self.pos:
            tl = tokenize(p)
            groups.setdefault(tuple((t.kind, t.val) for t in tl), []).append(tl)
        parts = [render(merge_shapes(g)) for g in groups.values()]
        if len(parts) < 2:
            return None
        return "(?:" + "|".join(parts) + ")"


def synthesize(pos, neg, seed=0, population=150, generations=120) -> tuple[str, float]:
    if not pos:
        raise ValueError("need at least one positive example")
    return Synth(pos, neg, seed).run(population, generations)


# ------------------------------------------------------------------ explanation (zh-TW)
NAMED_RANGES = {("a", "z"): "小寫英文字母", ("A", "Z"): "大寫英文字母", ("0", "9"): "數字（0–9）"}
CATEGORY_ZH = {
    "CATEGORY_DIGIT": "數字（0–9）", "CATEGORY_NOT_DIGIT": "非數字字元",
    "CATEGORY_WORD": "英數字或底線", "CATEGORY_NOT_WORD": "非英數字元",
    "CATEGORY_SPACE": "空白字元", "CATEGORY_NOT_SPACE": "非空白字元",
}
AT_ZH = {"AT_BEGINNING": "字串開頭", "AT_BEGINNING_STRING": "字串開頭", "AT_END": "字串結尾",
         "AT_END_STRING": "字串結尾", "AT_BOUNDARY": "單字邊界", "AT_NON_BOUNDARY": "非單字邊界"}


def _lit(code: int) -> str:
    c = chr(code)
    names = {" ": "空格", "-": "連字號「-」", ".": "句點「.」", "@": "小老鼠「@」",
             "_": "底線「_」", "/": "斜線「/」", ":": "冒號「:」", "\t": "Tab"}
    return names.get(c, f"字元「{c}」")


def _in_items(items) -> str:
    parts, negate = [], False
    for op, av in items:
        name = str(op)
        if name == "NEGATE":
            negate = True
        elif name == "LITERAL":
            parts.append(_lit(av))
        elif name == "RANGE":
            a, b = chr(av[0]), chr(av[1])
            parts.append(NAMED_RANGES.get((a, b), f"「{a}」到「{b}」之間的字元"))
        elif name == "CATEGORY":
            parts.append(CATEGORY_ZH.get(str(av), str(av)))
    for combo, word in (({"小寫英文字母", "大寫英文字母", "數字（0–9）"}, "英文字母或數字"),
                        ({"小寫英文字母", "大寫英文字母"}, "英文字母")):
        if combo <= set(parts):
            parts = [word] + [p for p in parts if p not in combo]
            break
    desc = "或".join(parts) if len(parts) <= 2 else "、".join(parts[:-1]) + "或" + parts[-1]
    return f"除了{desc}以外的任一字元" if negate else (desc if len(parts) == 1 else f"{desc}其中之一")


def _repeat_zh(lo: int, hi: int) -> str:
    inf = hi == C.MAXREPEAT
    if lo == hi:
        return f"剛好 {lo} 個"
    if lo == 0 and hi == 1:
        return "可有可無的（0 或 1 個）"
    if inf:
        return "零個或多個" if lo == 0 else ("一個或多個" if lo == 1 else f"至少 {lo} 個")
    return f"{lo} 到 {hi} 個"


def _atom_zh(op, av) -> str:
    name = str(op)
    if name == "LITERAL":
        return _lit(av)
    if name == "NOT_LITERAL":
        return f"不是「{chr(av)}」的字元"
    if name == "ANY":
        return "任意字元"
    if name == "IN":
        return _in_items(av)
    if name == "CATEGORY":
        return CATEGORY_ZH.get(str(av), str(av))
    return ""


def explain_tree(tree, depth: int = 0) -> list[str]:
    lines = []
    ind = "   " * depth
    items = list(tree)
    i = 0
    while i < len(items):
        op, av = items[i]
        name = str(op)
        if name == "LITERAL":  # collapse runs of literals into a word
            j = i
            while j < len(items) and str(items[j][0]) == "LITERAL":
                j += 1
            if j - i > 1:
                word = "".join(chr(items[k][1]) for k in range(i, j))
                lines.append(f"{ind}接著是固定文字「{word}」")
                i = j
                continue
        if name in ("MAX_REPEAT", "MIN_REPEAT", "POSSESSIVE_REPEAT"):
            lo, hi, sub = av
            sub = list(sub)
            lazy = "（盡量少）" if name == "MIN_REPEAT" else ""
            if len(sub) == 1 and str(sub[0][0]) != "SUBPATTERN":
                lines.append(f"{ind}{_repeat_zh(lo, hi)}{_atom_zh(*sub[0])}{lazy}")
            else:
                lines.append(f"{ind}以下這組內容重複{_repeat_zh(lo, hi).replace('個', '次')}{lazy}：")
                lines.extend(explain_tree(sub, depth + 1))
        elif name == "SUBPATTERN":
            group, _, _, sub = av
            label = f"（第 {group} 組，會被擷取）" if group else ""
            lines.append(f"{ind}一個群組{label}，內容為：")
            lines.extend(explain_tree(sub, depth + 1))
        elif name == "BRANCH":
            _, alts = av
            lines.append(f"{ind}以下 {len(alts)} 種寫法其中一種：")
            for k, alt in enumerate(alts, 1):
                lines.append(f"{ind}   選項 {k}：")
                lines.extend(explain_tree(alt, depth + 2))
        elif name == "AT":
            lines.append(f"{ind}位置：{AT_ZH.get(str(av), str(av))}")
        else:
            lines.append(f"{ind}一個{_atom_zh(op, av) or name}")
        i += 1
    return lines


def explain(pattern: str, fullmatch: bool = True) -> str:
    tree = sre_parse.parse(pattern)
    steps = explain_tree(tree)
    head = ("這個正規表示式要求「整個字串」從頭到尾依序符合：" if fullmatch
            else "這個正規表示式會在字串中尋找依序符合下列規則的片段：")
    body = []
    n = 0
    for s in steps:
        if s.startswith("   "):
            body.append("     " + s)
        else:
            n += 1
            body.append(f"  {n}. {s}")
    return head + "\n" + "\n".join(body)


# ------------------------------------------------------------------ CLI
def read_lines(path: str | None) -> list[str]:
    if not path:
        return []
    with open(path, encoding="utf-8") as f:
        return [l.rstrip("\n") for l in f if l.rstrip("\n")]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Synthesize a regex from positive/negative examples (genetic search) and "
                    "explain it in Traditional Chinese.",
        epilog="Example: regex_from_examples.py -p 2026-09-27 -p 1999-01-05 -n 2026/09/27 -n 26-9-27")
    ap.add_argument("-p", "--pos", action="append", default=[], help="string that must match")
    ap.add_argument("-n", "--neg", action="append", default=[], help="string that must NOT match")
    ap.add_argument("--pos-file", help="file with one positive example per line")
    ap.add_argument("--neg-file", help="file with one negative example per line")
    ap.add_argument("--explain", metavar="REGEX", help="only explain this regex in zh-TW")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--population", type=int, default=150)
    ap.add_argument("--generations", type=int, default=120)
    args = ap.parse_args(argv)

    if args.explain:
        print(explain(args.explain, fullmatch=False))
        return 0
    pos = args.pos + read_lines(args.pos_file)
    neg = args.neg + read_lines(args.neg_file)
    if not pos:
        ap.error("give at least one positive example (-p)")
    pat, acc = synthesize(pos, neg, args.seed, args.population, args.generations)
    rx = re.compile(pat)
    print(f"regex (full match): {pat}")
    print(f"anchored form     : ^{pat}$")
    print(f"accuracy          : {acc * 100:.1f}% on {len(pos)} positive / {len(neg)} negative examples")
    bad_p = [s for s in pos if not rx.fullmatch(s)]
    bad_n = [s for s in neg if rx.fullmatch(s)]
    if bad_p:
        print(f"  misses positives : {bad_p}")
    if bad_n:
        print(f"  matches negatives: {bad_n}")
    print()
    print(explain(pat))
    return 0 if acc == 1.0 else 1


if __name__ == "__main__":
    sys.exit(main())
