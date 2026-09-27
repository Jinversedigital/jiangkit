"""Unit tests for the X GraphQL parser and storage (no network, no browser)."""
import csv
import json
from pathlib import Path

import pytest

from jiangkit.social.x_scraper.parser import (in_date_range, match_op, parse_count, parse_dom_item,
                              parse_graphql, parse_x_date, iter_cursors)
from jiangkit.social.x_scraper.storage import TweetStore, load_jsonl
from jiangkit.social.x_scraper.scraper import (build_search_url, media_filename, parse_status_id,
                               upgrade_media_url, build_parser)

FIX = Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def test_match_op():
    assert match_op("https://x.com/i/api/graphql/abc123/SearchTimeline?variables=%7B") == "SearchTimeline"
    assert match_op("https://x.com/i/api/graphql/Zz-9/UserTweets?x=1") == "UserTweets"
    assert match_op("https://x.com/i/api/graphql/Q/TweetDetail") == "TweetDetail"
    assert match_op("https://x.com/i/api/graphql/Q/UserByScreenName") is None
    assert match_op("https://x.com/home") is None


def test_parse_date_and_counts():
    assert parse_x_date("Sun Sep 20 10:00:00 +0000 2026") == "2026-09-20T10:00:00+00:00"
    assert parse_x_date("2026-09-20T10:00:00.000Z") == "2026-09-20T10:00:00+00:00"
    assert parse_count("1,234") == 1234
    assert parse_count("1.2K") == 1200
    assert parse_count("3.4M") == 3400000
    assert parse_count("1.5萬") == 15000
    assert parse_count("") is None


def test_search_page1():
    recs = parse_graphql(load("search_page1.json"), "SearchTimeline")
    ids = [r["id"] for r in recs]
    # promoted tweet 999 must be skipped
    assert ids == ["1830000000000000001", "1830000000000000002", "1830000000000000003"]
    t1, t2, t3 = recs
    assert t1["author_handle"] == "ai_idol_mei" and t1["author_name"] == "Mei 美"
    assert t1["url"] == "https://x.com/ai_idol_mei/status/1830000000000000001"
    assert t1["likes"] == 1520 and t1["reposts"] == 88 and t1["replies"] == 12
    assert t1["views"] == 45210
    assert t1["hashtags"] == ["AI美女", "AIart"]
    assert t1["media_urls"] == ["https://pbs.twimg.com/media/AAA111.jpg",
                                "https://pbs.twimg.com/media/AAA222.png"]
    assert t1["created_at"] == "2026-09-20T10:00:00+00:00"
    # TweetWithVisibilityResults unwrapped + old user schema + best-bitrate mp4
    assert t2["author_handle"] == "reels_maker"
    assert t2["media_urls"] == ["https://video.twimg.com/ext_tw_video/2/pu/vid/1080x1920/abc.mp4"]
    # note tweet → full long text + hashtag from entity_set
    assert t3["text"].startswith("這是一篇很長的長文")
    assert "長文" in t3["hashtags"]


def test_cursors():
    cur = dict(iter_cursors(load("search_page1.json")))
    assert cur["Bottom"] == "BOTTOM1" and cur["Top"] == "TOP1"


def test_user_tweets():
    recs = parse_graphql(load("user_tweets.json"), "UserTweets")
    by_id = {r["id"]: r for r in recs}
    assert set(by_id) == {"1820000000000000010", "1830000000000000011", "1830000000000000012"}
    rt = by_id["1830000000000000012"]
    assert rt["is_retweet"] and rt["retweeted_id"] == "1830000000000000004"
    # retweet inherits media of original; the original is not emitted separately
    assert rt["media_urls"] == ["https://pbs.twimg.com/media/CCC333.jpg"]
    assert "1830000000000000004" not in by_id


def test_tweet_detail():
    recs = parse_graphql(load("tweet_detail.json"), "TweetDetail")
    assert [r["id"][-2:] for r in recs] == ["20", "21", "22", "23"]
    r3 = recs[3]
    assert r3["in_reply_to_id"] == "1830000000000000020"
    assert r3["likes"] == 1200  # "1.2K" string tolerated
    assert r3["conversation_id"] == "1830000000000000020"


def test_date_range():
    rec = {"created_at": "2026-09-22T23:59:59+00:00"}
    assert in_date_range(rec, "2026-09-22", "2026-09-23")
    assert not in_date_range(rec, "2026-09-23", None)
    assert not in_date_range(rec, None, "2026-09-22")
    assert in_date_range({"created_at": None}, "2026-01-01", None)


def test_dom_item():
    r = parse_dom_item({"url": "https://x.com/foo/status/42", "text": "hi #abc #def",
                        "datetime": "2026-09-01T00:00:00.000Z", "name": "Foo", "likes": "1.1K",
                        "reposts": "3", "replies": None, "views": "12,345",
                        "media": ["https://pbs.twimg.com/media/X.jpg"] * 2})
    assert r["id"] == "42" and r["author_handle"] == "foo"
    assert r["likes"] == 1100 and r["views"] == 12345 and r["replies"] is None
    assert r["hashtags"] == ["abc", "def"]
    assert r["media_urls"] == ["https://pbs.twimg.com/media/X.jpg"]
    assert parse_dom_item({"url": "https://x.com/home"}) is None


def test_store_dedupe_resume_csv(tmp_path):
    p1 = parse_graphql(load("search_page1.json"))
    p2 = parse_graphql(load("search_page2.json"))
    s = TweetStore(tmp_path / "out")
    added = sum(s.add(r) for r in p1 + p2)
    s.close()
    assert added == 4  # t1 duplicated on page 2
    # resume: re-adding the same records adds nothing
    s2 = TweetStore(tmp_path / "out.jsonl")
    assert s2.resumed == 4
    assert sum(s2.add(r) for r in p1 + p2) == 0
    s2.close()
    assert len(load_jsonl(tmp_path / "out.jsonl")) == 4
    with open(tmp_path / "out.csv", encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 4
    assert rows[0]["hashtags"] == "AI美女 | AIart"
    # fresh wipes
    s3 = TweetStore(tmp_path / "out", fresh=True)
    assert s3.resumed == 0
    s3.close()


def test_url_helpers():
    u = build_search_url("https://x.com", "#AI美女", "latest", "2026-09-01", "2026-09-10")
    assert "f=live" in u and "since%3A2026-09-01" in u and "until%3A2026-09-10" in u
    assert "f=" not in build_search_url("https://x.com", "cat", "top", None, None)
    assert parse_status_id("https://x.com/a/status/12345?s=20") == "12345"
    assert parse_status_id("999") == "999"
    with pytest.raises(SystemExit):
        parse_status_id("nope")
    assert upgrade_media_url("https://pbs.twimg.com/media/A.jpg") == "https://pbs.twimg.com/media/A.jpg?name=orig"
    assert media_filename("1", 0, "https://video.twimg.com/v/abc.mp4?tag=12") == "1_0.mp4"
    assert media_filename("1", 2, "https://pbs.twimg.com/media/A?format=png&name=small") == "1_2.png"


def test_cli_parses():
    a = build_parser().parse_args(["search", "#cat", "-o", "x", "--mode", "top", "--limit", "5"])
    assert a.query == "#cat" and a.mode == "top" and a.limit == 5
    a = build_parser().parse_args(["login"])
    assert a.cmd == "login"
