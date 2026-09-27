"""Generate synthetic fixtures shaped like real X GraphQL responses.

Run:  python make_fixtures.py   (writes *.json next to this file)
The output JSON files are committed, so tests don't need to run this.
"""
import json
from pathlib import Path

HERE = Path(__file__).parent


def user(handle, name, new_schema=True):
    r = {"__typename": "User", "rest_id": str(sum(map(ord, handle)) * 7919),
         "legacy": {"followers_count": 10}}
    if new_schema:          # 2025+ layout: names under user.core
        r["core"] = {"screen_name": handle, "name": name}
    else:                   # older layout: names under user.legacy
        r["legacy"].update({"screen_name": handle, "name": name})
    return {"result": r}


def tweet(tid, handle, name, text, created, likes=0, rts=0, replies=0, views="0",
          hashtags=(), photos=(), video=None, reply_to=None, conv=None, new_schema=True,
          note_text=None, retweet_of=None):
    legacy = {
        "id_str": str(tid), "full_text": text, "created_at": created,
        "favorite_count": likes, "retweet_count": rts, "reply_count": replies,
        "quote_count": 1, "bookmark_count": 2, "lang": "zh",
        "conversation_id_str": str(conv or tid),
        "entities": {"hashtags": [{"indices": [0, 1], "text": h} for h in hashtags],
                     "urls": [], "user_mentions": []},
    }
    media = [{"type": "photo", "media_url_https": u, "id_str": "9"} for u in photos]
    if video:
        media.append({"type": "video", "media_url_https": "https://pbs.twimg.com/ext_tw_video_thumb/1/pu/img/thumb.jpg",
                      "video_info": {"variants": [
                          {"content_type": "application/x-mpegURL", "url": "https://video.twimg.com/x.m3u8"},
                          {"content_type": "video/mp4", "bitrate": 832000, "url": video + "?low"},
                          {"content_type": "video/mp4", "bitrate": 2176000, "url": video}]}})
    if media:
        legacy["extended_entities"] = {"media": media}
        legacy["entities"]["media"] = media[:1]
    if reply_to:
        legacy["in_reply_to_status_id_str"] = str(reply_to)
    t = {"__typename": "Tweet", "rest_id": str(tid), "core": {"user_results": user(handle, name, new_schema)},
         "legacy": legacy, "views": {"count": views, "state": "EnabledWithCount"}}
    if note_text:
        t["note_tweet"] = {"is_expandable": True, "note_tweet_results": {"result": {
            "id": "N1", "text": note_text, "entity_set": {"hashtags": [{"text": "長文"}]}}}}
    if retweet_of:
        legacy["retweeted_status_result"] = {"result": retweet_of}
    return t


def entry(t, promoted=False):
    ic = {"itemType": "TimelineTweet", "__typename": "TimelineTweet",
          "tweet_results": {"result": t}, "tweetDisplayType": "Tweet"}
    if promoted:
        ic["promotedMetadata"] = {"advertiser_results": {}}
    tid = t.get("rest_id") or t.get("tweet", {}).get("rest_id")
    return {"entryId": ("promoted-tweet-" if promoted else "tweet-") + tid, "sortIndex": tid,
            "content": {"entryType": "TimelineTimelineItem", "__typename": "TimelineTimelineItem",
                        "itemContent": ic}}


def cursor(kind, value):
    return {"entryId": f"cursor-{kind.lower()}-{value}", "sortIndex": "0",
            "content": {"entryType": "TimelineTimelineCursor", "__typename": "TimelineTimelineCursor",
                        "value": value, "cursorType": kind}}


def dump(name, obj):
    (HERE / name).write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")


# ---------- SearchTimeline page 1 ----------
t1 = tweet(1830000000000000001, "ai_idol_mei", "Mei 美", "#AI美女 新作品上線 ✨ #AIart",
           "Sun Sep 20 10:00:00 +0000 2026", likes=1520, rts=88, replies=12, views="45210",
           hashtags=["AI美女", "AIart"], photos=["https://pbs.twimg.com/media/AAA111.jpg",
                                                 "https://pbs.twimg.com/media/AAA222.png"])
t2_inner = tweet(1830000000000000002, "reels_maker", "Reels 工作室", "Reels 教學影片 #AIart",
                 "Mon Sep 21 08:30:00 +0000 2026", likes=300, rts=20, replies=5, views="9999",
                 hashtags=["AIart"], video="https://video.twimg.com/ext_tw_video/2/pu/vid/1080x1920/abc.mp4",
                 new_schema=False)
t2 = {"__typename": "TweetWithVisibilityResults", "tweet": t2_inner,
      "limitedActionResults": {"limited_actions": []}}
t3 = tweet(1830000000000000003, "long_writer", "長文作者", "截斷的文字…",
           "Tue Sep 22 23:59:59 +0000 2026", likes=7, views="100", note_text="這是一篇很長的長文 #長文 完整內容")
ad = tweet(1830000000000000999, "brand", "Ad Brand", "Buy now", "Tue Sep 22 00:00:00 +0000 2026")
search1 = {"data": {"search_by_raw_query": {"search_timeline": {"timeline": {"instructions": [
    {"type": "TimelineClearCache"},
    {"type": "TimelineAddEntries", "entries": [entry(t1), entry(t2), entry(ad, promoted=True), entry(t3),
                                               cursor("Top", "TOP1"), cursor("Bottom", "BOTTOM1")]}]}}}}}
dump("search_page1.json", search1)

# ---------- SearchTimeline page 2 (contains a duplicate of t1) ----------
t4 = tweet(1830000000000000004, "comic_studio", "短劇漫畫社", "第3話 更新 #短劇",
           "Wed Sep 23 12:00:00 +0000 2026", likes=2400, rts=310, replies=77, views="120000",
           hashtags=["短劇"], photos=["https://pbs.twimg.com/media/CCC333.jpg"])
search2 = {"data": {"search_by_raw_query": {"search_timeline": {"timeline": {"instructions": [
    {"type": "TimelineAddEntries", "entries": [entry(t4), entry(t1), cursor("Bottom", "BOTTOM2")]},
    {"type": "TimelineReplaceEntry", "entry_id_to_replace": "cursor-top", "entry": cursor("Top", "TOP2")}]}}}}}
dump("search_page2.json", search2)

# ---------- UserTweets (pinned + normal + retweet + tombstone) ----------
pinned = tweet(1820000000000000010, "ai_idol_mei", "Mei 美", "置頂：作品集連結",
               "Fri Aug 01 00:00:00 +0000 2026", likes=5000, views="300000")
own = tweet(1830000000000000011, "ai_idol_mei", "Mei 美", "今天的穿搭 #OOTD",
            "Thu Sep 24 09:00:00 +0000 2026", likes=900, rts=40, replies=30, views="21000",
            hashtags=["OOTD"], photos=["https://pbs.twimg.com/media/DDD444.jpg"])
rt = tweet(1830000000000000012, "ai_idol_mei", "Mei 美", "RT @comic_studio: 第3話 更新 #短劇",
           "Thu Sep 24 10:00:00 +0000 2026", rts=310, retweet_of=t4)
user_tweets = {"data": {"user": {"result": {"__typename": "User", "timeline": {"timeline": {"instructions": [
    {"type": "TimelinePinEntry", "entry": entry(pinned)},
    {"type": "TimelineAddEntries", "entries": [
        entry(own), entry(rt),
        {"entryId": "tweet-404", "content": {"entryType": "TimelineTimelineItem", "itemContent": {
            "itemType": "TimelineTweet", "tweet_results": {"result": {"__typename": "TweetTombstone",
                                                                      "tombstone": {"text": {"text": "deleted"}}}}}}},
        {"entryId": "who-to-follow-1", "content": {"entryType": "TimelineTimelineModule", "items": [
            {"entryId": "who-to-follow-1-user-1", "item": {"itemContent": {"itemType": "TimelineUser",
                                                                          "user_results": user("someone", "S")}}}]}},
        cursor("Bottom", "UBOTTOM")]}]}}}}}}
dump("user_tweets.json", user_tweets)

# ---------- TweetDetail (focal + conversation threads) ----------
focal = tweet(1830000000000000020, "ai_idol_mei", "Mei 美", "大家喜歡哪一套？",
              "Fri Sep 25 01:00:00 +0000 2026", likes=100, replies=3, views="5000")
r1 = tweet(1830000000000000021, "fan_a", "粉絲A", "第二套！", "Fri Sep 25 01:05:00 +0000 2026",
           likes=10, reply_to=1830000000000000020, conv=1830000000000000020)
r2 = tweet(1830000000000000022, "ai_idol_mei", "Mei 美", "謝謝～", "Fri Sep 25 01:10:00 +0000 2026",
           likes=3, reply_to=1830000000000000021, conv=1830000000000000020)
r3 = tweet(1830000000000000023, "fan_b", "粉絲B", "都好看 #AI美女", "Fri Sep 25 02:00:00 +0000 2026",
           likes="1.2K", hashtags=["AI美女"], reply_to=1830000000000000020, conv=1830000000000000020)


def conv_module(eid, tweets):
    return {"entryId": eid, "content": {"entryType": "TimelineTimelineModule", "__typename": "TimelineTimelineModule",
            "items": [{"entryId": f"{eid}-tweet-{t['rest_id']}", "item": {"itemContent": {
                "itemType": "TimelineTweet", "tweet_results": {"result": t}}}} for t in tweets],
            "displayType": "VerticalConversation"}}


detail = {"data": {"threaded_conversation_with_injections_v2": {"instructions": [
    {"type": "TimelineAddEntries", "entries": [
        entry(focal),
        conv_module("conversationthread-21", [r1, r2]),
        conv_module("conversationthread-23", [r3]),
        {"entryId": "cursor-bottom-x", "content": {"entryType": "TimelineTimelineItem", "itemContent": {
            "itemType": "TimelineTimelineCursor", "__typename": "TimelineTimelineCursor",
            "value": "DETAILCURSOR", "cursorType": "Bottom"}}}]},
    {"type": "TimelineTerminateTimeline", "direction": "Top"}]}}}
dump("tweet_detail.json", detail)
print("fixtures written")
