"""Fetch the transcript of the most recent captioned video from a YouTube channel feed.

Uses yt-dlp to pull YouTube auto-captions, which are free and need no API key. YouTube
generates auto-captions a few hours to a day or so after upload, so the very newest video
often has none yet; this walks back to the newest one that does. An optional title
substring (`match`) restricts which videos count (e.g. "nbf" for Nothing But Facts).
"""

import glob
import json
import os
import tempfile
import time

import feedparser


def _matches(title, match):
    """A title matches if `match` is falsy (any video) or any of its "|"-separated
    alternatives is a case-insensitive substring of the title."""
    if not match:
        return True
    low = title.lower()
    return any(tok.strip() in low for tok in match.lower().split("|") if tok.strip())


def _fetch_transcript(video_id):
    """Download and flatten the English auto-captions for one video, or None."""
    import yt_dlp

    with tempfile.TemporaryDirectory() as tmp:
        base = os.path.join(tmp, "sub")
        opts = {
            "skip_download": True,
            "writeautomaticsub": True,
            "writesubtitles": True,
            "subtitleslangs": ["en", "en-orig"],
            "subtitlesformat": "json3",
            "outtmpl": base,
            "quiet": True,
            "no_warnings": True,
            "retries": 5,
            "extractor_retries": 3,
            "socket_timeout": 20,
        }
        files = []
        for attempt in range(2):  # caption downloads are intermittently flaky; retry once
            try:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    ydl.download([f"https://www.youtube.com/watch?v={video_id}"])
            except Exception:
                pass
            files = sorted(glob.glob(base + "*.json3"))
            if files:
                break
            time.sleep(2)
        if not files:
            return None  # no captions yet, or YouTube blocked the fetch
        parts = []
        try:
            data = json.load(open(files[0], encoding="utf-8"))
        except Exception:
            return None
        for ev in data.get("events", []):
            for seg in ev.get("segs", []) or []:
                t = seg.get("utf8", "")
                if t and t.strip():
                    parts.append(t)
        text = " ".join(" ".join(parts).split())
        return text or None


def latest_with_transcript(feed_url, match=None, min_words=0, max_check=8):
    """Return {video_id, title, url, transcript} for the newest video on the feed that
    matches `match` (a title substring; None = any video), has captions, and is at least
    `min_words` long (to skip Shorts), or None if none of the recent matching videos
    qualify yet."""
    d = feedparser.parse(feed_url)
    # Drop Shorts up front so they don't use up the max_check budget on channels (like
    # MarketMobster) that post many Shorts between substantive uploads.
    videos = [
        (getattr(e, "yt_videoid", None), e.title, e.link)
        for e in d.entries
        if getattr(e, "yt_videoid", None)
        and _matches(e.title, match)
        and "/shorts/" not in (e.link or "")
    ]
    for vid, title, url in videos[:max_check]:
        text = _fetch_transcript(vid)
        if text and len(text.split()) >= min_words:
            return {"video_id": vid, "title": title, "url": url, "transcript": text}
    return None


def latest_video_link(feed_url, match=None):
    """Return {title, url} of the newest matching non-Short video, ignoring captions.
    A fallback so a channel's slot can link its latest upload even when no transcript
    is available."""
    d = feedparser.parse(feed_url)
    for e in d.entries:
        if (
            getattr(e, "yt_videoid", None)
            and _matches(e.title, match)
            and "/shorts/" not in (e.link or "")
        ):
            return {"title": e.title, "url": e.link}
    return None
