"""Parse X (Twitter) GraphQL JSON responses into flat tweet records.

The parser is deliberately *shape-tolerant*: instead of hard-coding the exact
path for each endpoint (SearchTimeline, UserTweets, TweetDetail ...), it walks
the whole JSON tree and picks up every ``tweet_results`` node.  X changes its
response layout often, so this survives most minor schema changes.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Iterator

# Endpoints whose responses we care about.
GRAPHQL_OPS = ("SearchTimeline", "UserTweets", "UserTweetsAndReplies",
               "UserMedia", "TweetDetail", "HomeTimeline", "HomeLatestTimeline")
GRAPHQL_URL_RE = re.compile(r"/i/api/graphql/[^/]+/(" + "|".join(GRAPHQL_OPS) + r")\b")

FIELDS = ["id", "url", "author_handle", "author_name", "text", "created_at",
          "likes", "reposts", "replies", "quotes", "views", "bookmarks",
          "media_urls", "hashtags", "lang", "is_retweet", "retweeted_id",
          "quoted_id", "in_reply_to_id", "conversation_id", "source_op"]

X_DATE_FMT = "%a %b %d %H:%M:%S %z %Y"   # e.g. "Wed Oct 10 20:19:24 +0000 2018"


def match_op(url: str) -> str | None:
    """Return GraphQL operation name if the URL is one we intercept."""
    m = GRAPHQL_URL_RE.search(url)
    return m.group(1) if m else None


def parse_x_date(value: str | None) -> str | None:
    """Convert X's legacy date string to ISO-8601 UTC. Pass ISO through."""
    if not value:
        return None
    try:
        return datetime.strptime(value, X_DATE_FMT).astimezone(timezone.utc).isoformat()
    except ValueError:
        pass
    try:  # already ISO (DOM fallback gives <time datetime="...Z">)
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc).isoformat()
    except ValueError:
        return value


def _to_int(v: Any) -> int | None:
    if v is None or v == "":
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return parse_count(str(v))


def parse_count(s: str | None) -> int | None:
    """Parse human counts like '1,234', '1.2K', '3.4M', '1.2萬' → int."""
    if s is None:
        return None
    s = s.strip().replace(",", "")
    if not s:
        return None
    m = re.match(r"^([\d.]+)\s*([KkMmBb萬万億亿]?)", s)
    if not m:
        return None
    num = float(m.group(1))
    mult = {"k": 1e3, "m": 1e6, "b": 1e9, "萬": 1e4, "万": 1e4, "億": 1e8, "亿": 1e8}
    return int(round(num * mult.get(m.group(2).lower(), 1)))


def _unwrap(result: dict | None) -> dict | None:
    """Unwrap TweetWithVisibilityResults / tombstones to the real Tweet dict."""
    if not isinstance(result, dict):
        return None
    t = result.get("__typename")
    if t == "TweetWithVisibilityResults" or ("tweet" in result and "legacy" not in result):
        return _unwrap(result.get("tweet"))
    if t in ("TweetTombstone", "TweetUnavailable"):
        return None
    if "legacy" not in result:
        return None
    return result


def _user(tweet: dict) -> tuple[str | None, str | None]:
    u = (((tweet.get("core") or {}).get("user_results") or {}).get("result") or {})
    if u.get("__typename") == "UserUnavailable":
        return None, None
    # Newer schema puts screen_name/name under user.core; older under user.legacy.
    core = u.get("core") or {}
    legacy = u.get("legacy") or {}
    handle = core.get("screen_name") or legacy.get("screen_name")
    name = core.get("name") or legacy.get("name")
    return handle, name


def _best_video(variants: list[dict]) -> str | None:
    mp4 = [v for v in variants or [] if v.get("content_type") == "video/mp4" and v.get("url")]
    if mp4:
        return max(mp4, key=lambda v: v.get("bitrate") or 0)["url"]
    return (variants[0].get("url") if variants else None)


def _media(legacy: dict) -> list[str]:
    media = ((legacy.get("extended_entities") or {}).get("media")
             or (legacy.get("entities") or {}).get("media") or [])
    urls: list[str] = []
    for m in media:
        if m.get("type") in ("video", "animated_gif"):
            u = _best_video((m.get("video_info") or {}).get("variants") or [])
        else:
            u = m.get("media_url_https") or m.get("media_url")
        if u and u not in urls:
            urls.append(u)
    return urls


def parse_tweet(result: dict, source_op: str | None = None) -> dict | None:
    """Turn one ``tweet_results.result`` node into a flat record (or None)."""
    tw = _unwrap(result)
    if not tw:
        return None
    legacy = tw.get("legacy") or {}
    tid = tw.get("rest_id") or legacy.get("id_str")
    if not tid:
        return None
    handle, name = _user(tw)
    # Long posts ("note tweets") keep full text outside legacy.full_text.
    note = (((tw.get("note_tweet") or {}).get("note_tweet_results") or {}).get("result") or {})
    text = note.get("text") or legacy.get("full_text") or legacy.get("text") or ""
    rt = _unwrap(((legacy.get("retweeted_status_result") or {}).get("result")))
    hashtags = [h.get("text") for h in (legacy.get("entities") or {}).get("hashtags", []) if h.get("text")]
    if note.get("entity_set"):
        for h in note["entity_set"].get("hashtags", []):
            if h.get("text") and h["text"] not in hashtags:
                hashtags.append(h["text"])
    media = _media(legacy)
    if rt and not media:
        media = _media(rt.get("legacy") or {})
    views = (tw.get("views") or {}).get("count")
    return {
        "id": str(tid),
        "url": f"https://x.com/{handle or 'i'}/status/{tid}",
        "author_handle": handle,
        "author_name": name,
        "text": text,
        "created_at": parse_x_date(legacy.get("created_at")),
        "likes": _to_int(legacy.get("favorite_count")),
        "reposts": _to_int(legacy.get("retweet_count")),
        "replies": _to_int(legacy.get("reply_count")),
        "quotes": _to_int(legacy.get("quote_count")),
        "views": _to_int(views),
        "bookmarks": _to_int(legacy.get("bookmark_count")),
        "media_urls": media,
        "hashtags": hashtags,
        "lang": legacy.get("lang"),
        "is_retweet": bool(rt),
        "retweeted_id": (rt or {}).get("rest_id"),
        "quoted_id": legacy.get("quoted_status_id_str"),
        "in_reply_to_id": legacy.get("in_reply_to_status_id_str"),
        "conversation_id": legacy.get("conversation_id_str"),
        "source_op": source_op,
    }


def iter_tweet_results(node: Any) -> Iterator[dict]:
    """Yield every ``tweet_results.result`` dict found in a JSON tree.

    We do not descend into a tweet once found, so quoted/retweeted tweets
    embedded inside a tweet are not emitted as separate rows.
    """
    stack = [node]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            tr = cur.get("tweet_results")
            # Skip ads: promoted items carry "promotedMetadata" next to tweet_results.
            if (isinstance(tr, dict) and isinstance(tr.get("result"), dict)
                    and "promotedMetadata" not in cur):
                yield tr["result"]
            for k, v in cur.items():
                if k != "tweet_results" and isinstance(v, (dict, list)):
                    stack.append(v)
        elif isinstance(cur, list):
            stack.extend(reversed(cur))


def iter_cursors(node: Any) -> Iterator[tuple[str, str]]:
    """Yield (cursorType, value) pairs, e.g. ('Bottom', 'DAAC...')."""
    stack = [node]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            if cur.get("__typename") == "TimelineTimelineCursor" or cur.get("cursorType"):
                if cur.get("value"):
                    yield cur.get("cursorType", ""), cur["value"]
            stack.extend(v for v in cur.values() if isinstance(v, (dict, list)))
        elif isinstance(cur, list):
            stack.extend(cur)


def parse_graphql(payload: dict, source_op: str | None = None) -> list[dict]:
    """Parse a whole GraphQL response body into a list of unique records."""
    out, seen = [], set()
    for res in iter_tweet_results(payload):
        rec = parse_tweet(res, source_op)
        if rec and rec["id"] not in seen:
            seen.add(rec["id"])
            out.append(rec)
    return out


def parse_dom_item(item: dict) -> dict | None:
    """Normalise one raw DOM-extracted <article> dict (DOM fallback path).

    Expected keys (all optional except url): url, text, datetime, name,
    replies, reposts, likes, views, media (list), hashtags (list).
    """
    url = item.get("url") or ""
    m = re.search(r"/([^/]+)/status/(\d+)", url)
    if not m:
        return None
    handle, tid = m.group(1), m.group(2)
    text = item.get("text") or ""
    tags = item.get("hashtags") or re.findall(r"#(\w+)", text)
    name = item.get("name")
    if name:  # innerText may glue "Name @handle · 2h" together; keep display name only
        name = name.split(f"@{handle}")[0].strip() or name
    return {
        "id": tid,
        "url": f"https://x.com/{handle}/status/{tid}",
        "author_handle": handle,
        "author_name": name,
        "text": text,
        "created_at": parse_x_date(item.get("datetime")),
        "likes": parse_count(item.get("likes")),
        "reposts": parse_count(item.get("reposts")),
        "replies": parse_count(item.get("replies")),
        "quotes": None,
        "views": parse_count(item.get("views")),
        "bookmarks": None,
        "media_urls": list(dict.fromkeys(item.get("media") or [])),
        "hashtags": list(dict.fromkeys(tags)),
        "lang": item.get("lang"),
        "is_retweet": bool(item.get("is_retweet")),
        "retweeted_id": None,
        "quoted_id": None,
        "in_reply_to_id": None,
        "conversation_id": None,
        "source_op": "DOM",
    }


def in_date_range(rec: dict, since: str | None, until: str | None) -> bool:
    """since inclusive, until exclusive (YYYY-MM-DD, UTC) — same as X search."""
    ca = rec.get("created_at")
    if not ca or not (since or until):
        return True
    day = ca[:10]
    if since and day < since:
        return False
    if until and day >= until:
        return False
    return True
