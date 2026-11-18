"""Projects, background jobs, and the analyze / render pipelines."""
import json
import os
import queue
import threading
import time
import traceback
import uuid

import numpy as np

from . import config, render, signals, transcript, youtube

_projects = {}
_lock = threading.RLock()
_q = queue.Queue()


# ---------------------------------------------------------------- persistence
def _pfile(pid):
    return os.path.join(config.WORKSPACE, pid, "project.json")


def save(p):
    with _lock:
        data = {k: v for k, v in p.items() if not k.startswith("_")}
        tmp = _pfile(p["id"]) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, _pfile(p["id"]))


def get(pid):
    with _lock:
        if pid in _projects:
            return _projects[pid]
        path = _pfile(pid)
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                p = json.load(f)
            if p.get("status") in ("queued", "running"):
                p["status"] = "error"
                p["error"] = "The app was closed while this was running. Start it again."
            _projects[pid] = p
            return p
    return None


def list_projects():
    out = []
    for pid in os.listdir(config.WORKSPACE):
        if os.path.exists(_pfile(pid)):
            p = get(pid)
            out.append({"id": pid, "title": p.get("title"), "created": p.get("created"),
                        "status": p.get("status"), "n_clips": len(p.get("clips") or [])})
    return sorted(out, key=lambda x: -(x["created"] or 0))


def words_of(p):
    path = os.path.join(p["dir"], "transcript.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return []


def _log(p, msg):
    with _lock:
        p.setdefault("log", []).append(time.strftime("%H:%M:%S ") + msg)
        p["log"] = p["log"][-200:]


def _mmss(s):
    s = int(s)
    return "%d:%02d:%02d" % (s // 3600, s % 3600 // 60, s % 60) if s >= 3600 else "%d:%02d" % (s // 60, s % 60)


def _stage(p, stage, progress=None):
    with _lock:
        p["stage"] = stage
        if progress is not None:
            p["progress"] = round(progress, 3)
    _log(p, stage)


# ---------------------------------------------------------------- jobs
def _worker():
    while True:
        fn, p, args = _q.get()
        try:
            fn(p, *args)
        except Exception as e:  # surface every failure to the UI
            with _lock:
                p["status"] = "error"
                p["error"] = str(e)
            _log(p, "ERROR: " + str(e))
            _log(p, traceback.format_exc()[-1500:])
        finally:
            save(p)
            _q.task_done()


threading.Thread(target=_worker, daemon=True).start()


def create_project(url=None, upload_path=None, settings=None):
    pid = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
    d = os.path.join(config.WORKSPACE, pid)
    os.makedirs(d, exist_ok=True)
    p = {"id": pid, "dir": d, "created": time.time(), "url": url or None, "title": None,
         "status": "queued", "stage": "Waiting...", "progress": 0, "log": [], "clips": [],
         "settings": settings or {}, "source": None, "renders": {}}
    if upload_path:
        p["upload_name"] = os.path.basename(upload_path)
    with _lock:
        _projects[pid] = p
    save(p)
    return p


def start_analyze(p):
    _q.put((_analyze, p, ()))


def start_render(p, clip_ids, opts):
    with _lock:
        for cid in clip_ids:
            p["renders"][str(cid)] = {"status": "queued"}
        p["render_status"] = "queued"
    save(p)
    _q.put((_render, p, (clip_ids, opts)))


# ---------------------------------------------------------------- analyze
def _analyze(p):
    s = p["settings"]
    p["status"] = "running"
    cookies = s.get("cookies_browser") or None
    meta = None

    if p.get("url"):
        _stage(p, "Reading video info, Most-Replayed graph and top comments...", 0.02)
        meta = youtube.fetch_metadata(p["url"], lambda m: _log(p, m),
                                      max_comments=int(s.get("max_comments", 400)), cookies_browser=cookies)
        p["title"] = meta["title"]
        p["duration"] = meta["duration"]
        _log(p, "Found: heatmap points=%d, comments=%d, caption tracks=%d manual / %d auto" % (
            len(meta["heatmap"]), len(meta["comments"]), len(meta["subtitles"]), len(meta["auto_captions"])))
        with open(os.path.join(p["dir"], "meta.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False)
        save(p)

    # ---- source video
    upload = os.path.join(p["dir"], p["upload_name"]) if p.get("upload_name") else None
    if upload and os.path.exists(upload):
        p["source"] = upload
        _log(p, "Using your uploaded file.")
    elif p.get("url"):
        _stage(p, "Downloading video (up to %sp)..." % s.get("max_height", 1080), 0.08)
        try:
            p["source"] = youtube.download_video(
                p["url"], p["dir"], lambda m: _log(p, m),
                lambda f: p.update(progress=round(0.08 + 0.5 * f, 3)),
                max_height=int(s.get("max_height", 1080)), cookies_browser=cookies)
        except Exception as e:
            raise RuntimeError(
                "Could not download the video: %s\n\nFixes: (1) try again on a home network - some office/"
                "school networks block YouTube video streams; (2) in Settings pick the browser you're logged "
                "into YouTube with (cookies); (3) download the video yourself and attach the file together "
                "with the link - you still get Most-Replayed + comments." % str(e)[:300])
    else:
        raise RuntimeError("Paste a YouTube link or attach a video file.")
    save(p)

    probe = config.run([config.ffprobe(), "-v", "error", "-show_entries", "format=duration:stream=codec_type,width,height",
                        "-of", "json", p["source"]])
    info = json.loads(probe.stdout)
    dur = float(info["format"].get("duration") or 0)
    if dur <= 0:
        raise RuntimeError("The video file looks broken (no duration).")
    if not any(st.get("codec_type") == "audio" for st in info.get("streams", [])):
        _log(p, "Warning: video has no audio track - loudness, captions and dub won't work.")
    p["warnings"] = []
    vh = max([st.get("height") or 0 for st in info.get("streams", []) if st.get("codec_type") == "video"] or [0])
    if 0 < vh < 720:
        p["warnings"].append(
            "Source is only %dp - reels will look soft. YouTube gave a low-quality stream (often a "
            "network/firewall limit). Try a home network, or attach a downloaded HD file with the link." % vh)
    if meta and meta["duration"] and abs(meta["duration"] - dur) > 5:
        p["warnings"].append(
            "The attached file is %s long but the YouTube video is %s - is it the same video? Most-replayed "
            "and comment timestamps only line up if it is." % (_mmss(dur), _mmss(meta["duration"])))
    for w in p["warnings"]:
        _log(p, "WARNING: " + w)
    p["duration"] = dur
    p["title"] = p.get("title") or os.path.splitext(p.get("upload_name") or "video")[0]
    n = int(np.ceil(dur))

    # ---- transcript
    words, src_lang = [], s.get("spoken_lang") or None
    if meta:
        lang, url, is_auto = youtube.pick_caption_track(meta, src_lang)
        if url:
            _stage(p, "Fetching YouTube %s captions (%s)..." % ("auto" if is_auto else "manual", lang), 0.6)
            try:
                words = transcript.parse_json3(youtube.download_captions(url, lambda m: _log(p, m), cookies))
                src_lang = lang
            except Exception as e:
                _log(p, "Could not fetch captions (%s) - will transcribe clips with Whisper instead." % str(e)[:120])
    if not words and s.get("full_whisper"):
        _stage(p, "Transcribing the whole video with Whisper (slow on CPU)...", 0.6)
        words, src_lang = transcript.whisper_words(p["source"], 0, None, language=src_lang)
    _log(p, "Transcript words: %d (language: %s)" % (len(words), src_lang or "unknown"))
    with open(os.path.join(p["dir"], "transcript.json"), "w", encoding="utf-8") as f:
        json.dump(words, f, ensure_ascii=False)
    p["source_lang"] = src_lang

    # ---- signals
    _stage(p, "Measuring audio loudness...", 0.7)
    loud, _ = signals.loudness_signal(p["source"], n)
    sig = {"loudness": loud, "speech": signals.speech_signal(words, n),
           "heatmap": signals.heatmap_signal(meta["heatmap"], n) if meta else None}
    hits = []
    if meta:
        sig["comments"], hits = signals.comment_signal(meta["comments"], n)
        _log(p, "Comments with timestamps: %d" % len(hits))
    score, used = signals.combine(sig)
    _log(p, "Signals used: %s" % (", ".join(used) or "none"))
    if not used:
        raise RuntimeError("No usable signal found (no heatmap, no timestamp comments, silent audio).")

    _stage(p, "Picking the best moments...", 0.9)
    phr = transcript.phrases(words) if words else None
    clips = signals.pick_clips(score, int(s.get("num_clips", 20)), int(s.get("clip_len", 40)), dur, phr)
    for i, c in enumerate(clips):
        c["id"] = i + 1
        c["reasons"], c["top_comments"] = signals.explain(c, sig, hits, used)
        ws = transcript.words_in(words, c["start"], c["end"])
        c["text"] = " ".join(w["w"] for w in ws)[:300]
    p["clips"] = clips
    p["signal_curve"] = [round(float(x), 3) for x in score[::max(1, n // 600)]]
    p["status"] = "ready"
    _stage(p, "Done - %d clips found." % len(clips), 1.0)


# ---------------------------------------------------------------- render
def _render(p, clip_ids, opts):
    p["render_status"] = "running"
    p["_words"] = words_of(p)
    proj = {"source": p["source"], "words": p["_words"], "source_lang": p.get("source_lang"), "dir": p["dir"]}
    out_dir = os.path.join(config.OUTPUT, render.safe_name(p.get("title") or p["id"]))
    os.makedirs(out_dir, exist_ok=True)
    p["output_dir"] = out_dir
    by_id = {c["id"]: c for c in p["clips"]}
    for k, cid in enumerate(clip_ids):
        c = by_id[int(cid)]
        r = p["renders"][str(cid)]
        r.update(status="running", started=time.time())
        _log(p, "Rendering clip %d (%d/%d)..." % (cid, k + 1, len(clip_ids)))
        suffix = ""
        if opts.get("dub_lang"):
            suffix = "_dub-" + opts["dub_lang"]
        elif opts.get("captions") == "translated":
            suffix = "_sub-" + (opts.get("caption_lang") or "")
        name = "%02d_score%d%s.mp4" % (cid, c["score"], suffix)
        try:
            render.render_clip(proj, c, opts, os.path.join(out_dir, name), lambda m: _log(p, "  " + m))
            r.update(status="done", file=name, secs=round(time.time() - r["started"], 1))
            _log(p, "  Saved %s (%.0fs)" % (name, r["secs"]))
        except Exception as e:
            r.update(status="error", error=str(e)[:500])
            _log(p, "  Clip %d failed: %s" % (cid, str(e)[:500]))
        save(p)
    p.pop("_words", None)
    p["render_status"] = "done"
