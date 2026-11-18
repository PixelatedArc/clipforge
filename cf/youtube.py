"""Everything that talks to YouTube, via yt-dlp."""
import json
import os

import yt_dlp

from . import config


class _Logger:
    def __init__(self, log):
        self.log = log

    def debug(self, msg):
        pass

    def info(self, msg):
        pass

    def warning(self, msg):
        self.log("yt-dlp warning: " + str(msg)[:300])

    def error(self, msg):
        self.log("yt-dlp error: " + str(msg)[:300])


def _base_opts(log, cookies_browser=None):
    opts = {
        "quiet": True,
        "no_warnings": False,
        "noprogress": True,
        "logger": _Logger(log),
        "ffmpeg_location": os.path.dirname(config.ffmpeg() or "") or None,
        "retries": 3,
        "extractor_retries": 2,
        "socket_timeout": 30,
    }
    if config.node():
        # yt-dlp needs a JavaScript runtime to solve YouTube's player challenges.
        opts["js_runtimes"] = {"node": {"path": config.node()}}
    if cookies_browser:
        opts["cookiesfrombrowser"] = (cookies_browser,)
    return opts


def fetch_metadata(url, log, max_comments=400, cookies_browser=None):
    """Title, duration, Most-Replayed heatmap, top comments and caption tracks."""
    opts = _base_opts(log, cookies_browser)
    opts.update({
        "skip_download": True,
        "getcomments": max_comments > 0,
        "extractor_args": {"youtube": {"max_comments": [str(max_comments)], "comment_sort": ["top"]}},
        # Don't fail metadata just because no playable format came back
        # (we may be using a local file instead).
        "ignore_no_formats_error": True,
    })
    with yt_dlp.YoutubeDL(opts) as y:
        info = y.extract_info(url, download=False)
        info = y.sanitize_info(info)
    formats = [f for f in (info.get("formats") or []) if f.get("vcodec") not in (None, "none")]
    max_h = max([f.get("height") or 0 for f in formats] or [0])
    return {
        "id": info.get("id"),
        "title": info.get("title") or "video",
        "channel": info.get("channel") or info.get("uploader"),
        "duration": float(info.get("duration") or 0),
        "heatmap": info.get("heatmap") or [],
        "chapters": info.get("chapters") or [],
        "comments": [
            {"text": c.get("text", ""), "likes": c.get("like_count") or 0}
            for c in (info.get("comments") or [])
        ],
        "subtitles": _caption_tracks(info.get("subtitles") or {}),
        "auto_captions": _caption_tracks(info.get("automatic_captions") or {}),
        "language": info.get("language"),
        "max_height": max_h,
        "webpage_url": info.get("webpage_url") or url,
    }


def _caption_tracks(d):
    out = {}
    for lang, fmts in d.items():
        for f in fmts:
            if f.get("ext") == "json3" and f.get("url"):
                out[lang] = f["url"]
                break
    return out


def pick_caption_track(meta, prefer_lang=None):
    """Return (lang, url, is_auto). Manual subs beat auto; original language beats translations."""
    subs, auto = meta["subtitles"], meta["auto_captions"]
    orig = meta.get("language")
    wanted = [l for l in [prefer_lang, orig, "en"] if l]
    for l in wanted:
        if l in subs:
            return l, subs[l], False
    # "xx-orig" is YouTube's untranslated auto track for the spoken language.
    for k in auto:
        if k.endswith("-orig"):
            return k[:-5], auto[k], True
    for l in wanted:
        if l in auto:
            return l, auto[l], True
    if subs:
        k = next(iter(subs))
        return k, subs[k], False
    return None, None, None


def download_captions(url_json3, log, cookies_browser=None):
    with yt_dlp.YoutubeDL(_base_opts(log, cookies_browser)) as y:
        data = y.urlopen(url_json3).read().decode("utf-8", "replace")
    return json.loads(data)


def download_video(url, out_dir, log, progress, max_height=1080, cookies_browser=None):
    """Download best video <= max_height (H.264 preferred: fast to cut, previews in any browser)."""
    def hook(d):
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            if total:
                progress(d.get("downloaded_bytes", 0) / total)

    opts = _base_opts(log, cookies_browser)
    opts.update({
        "format": "bv*+ba/b",
        "format_sort": ["res:%d" % max_height, "vcodec:h264", "acodec:m4a"],
        "merge_output_format": "mp4",
        "outtmpl": os.path.join(out_dir, "source.%(ext)s"),
        "progress_hooks": [hook],
        "overwrites": True,
    })
    with yt_dlp.YoutubeDL(opts) as y:
        y.extract_info(url, download=True)
    for name in os.listdir(out_dir):
        if name.startswith("source.") and not name.endswith((".part", ".ytdl")):
            return os.path.join(out_dir, name)
    raise RuntimeError("Download finished but no video file was found.")
