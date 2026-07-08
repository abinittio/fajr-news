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

import feedparser


def _matches(title, match):
    """A title matches if `match` is falsy (any video) or is a case-insensitive substring."""
    return not match or match.lower() in title.lower()


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
        }
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([f"https://www.youtube.com/watch?v={video_id}"])
        except Exception:
            return None  # no captions yet, or YouTube blocked the fetch

        files = sorted(glob.glob(base + "*.json3"))
        if not files:
            return None
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
    videos = [
        (getattr(e, "yt_videoid", None), e.title, e.link)
        for e in d.entries
        if getattr(e, "yt_videoid", None) and _matches(e.title, match)
    ]
    for vid, title, url in videos[:max_check]:
        if "/shorts/" in url:
            continue  # Shorts are too short to summarise usefully
        text = _fetch_transcript(vid)
        if text and len(text.split()) >= min_words:
            return {"video_id": vid, "title": title, "url": url, "transcript": text}
    return None
