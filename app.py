"""ClipForge - local, free YouTube-to-Shorts clipper.  Run:  start.bat  (or  python app.py)"""
import os
import secrets
import socket
import subprocess
import sys
import threading
import webbrowser

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from flask import Flask, abort, jsonify, make_response, redirect, request, send_file, send_from_directory  # noqa: E402

from cf import config, control, dub, pipeline, publish, translate  # noqa: E402
from cf import render as render_mod  # noqa: E402
from cf import settings as user_settings  # noqa: E402

PORT = int(os.environ.get("CLIPFORGE_PORT", 8765))
LAN = "--lan" in sys.argv          # allow phones/other PCs on the same Wi-Fi
HOST = "0.0.0.0" if LAN else "127.0.0.1"
ACCESS_KEY = secrets.token_urlsafe(6) if LAN else None
app = Flask(__name__, static_folder="static", static_url_path="/static")
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 ** 3  # 20 GB uploads
app.json.sort_keys = False  # keep language list in our order (English first)


def _public(p):
    keys = ["id", "title", "url", "status", "stage", "progress", "log", "clips", "duration", "error",
            "source_lang", "renders", "render_status", "output_dir", "signal_curve", "settings", "warnings",
            "crop", "info", "channel"]
    d = {k: p.get(k) for k in keys}
    d["has_source"] = bool(p.get("source") and os.path.exists(p["source"]))
    d["paused"] = control.is_paused() and control.current() == p["id"]
    return d


def _is_local():
    return request.remote_addr in ("127.0.0.1", "::1")


_preview_lock = threading.Lock()
VIDEO_EXT = (".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v", ".flv", ".wmv", ".ts")
_pick_lock = threading.Lock()


def _clean_path(raw):
    # "Copy as path" in Explorer wraps the path in quotes.
    return os.path.abspath(os.path.expanduser((raw or "").strip().strip('"').strip("'").strip())) if raw and raw.strip() else None


@app.before_request
def require_key():
    """In LAN mode, other devices must open the link with ?key=... once (stored in a cookie)."""
    if not LAN or request.remote_addr in ("127.0.0.1", "::1"):
        return None
    key = request.args.get("key")
    if key and secrets.compare_digest(key, ACCESS_KEY):
        resp = make_response(redirect(request.path))
        resp.set_cookie("cf_key", ACCESS_KEY, httponly=True, samesite="Strict", max_age=12 * 3600)
        return resp
    if secrets.compare_digest(request.cookies.get("cf_key", ""), ACCESS_KEY):
        return None
    return ("ClipForge: access key required. Open the full link shown in the ClipForge window on the PC.", 403)


@app.after_request
def no_stale_ui(resp):
    # Always revalidate, so an updated app.js/index.html is picked up right after an update.
    if not request.path.startswith("/media/"):
        resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.get("/")
def index():
    return send_from_directory("static", "index.html")


@app.get("/api/health")
def health():
    return jsonify(dict(config.health(), local=_is_local()))


@app.post("/api/pick-file")
def pick_file():
    """Open the normal Windows 'Open file' window on the PC and return the chosen path."""
    if not _is_local():
        return jsonify({"error": "Choosing a file on the PC only works on the PC itself."}), 400
    if not _pick_lock.acquire(blocking=False):
        return jsonify({"error": "A file window is already open - check your taskbar."}), 409
    try:
        import tkinter
        from tkinter import filedialog
        root = tkinter.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        path = filedialog.askopenfilename(
            parent=root, title="ClipForge - choose a video",
            filetypes=[("Video files", " ".join("*" + e for e in VIDEO_EXT)), ("All files", "*.*")])
        root.destroy()
        return jsonify({"path": os.path.normpath(path) if path else None})
    except Exception as e:
        return jsonify({"error": "Could not open the file window (%s). Paste the file path instead." % e}), 500
    finally:
        _pick_lock.release()


@app.get("/api/languages")
def languages():
    return jsonify(translate.LANGS)


@app.get("/api/voices/<lang>")
def voices(lang):
    try:
        return jsonify(dub.voices_for(lang))
    except Exception as e:
        return jsonify({"error": "Could not load voices: %s" % e}), 502


@app.get("/api/projects")
def projects():
    return jsonify(pipeline.list_projects())


@app.post("/api/analyze")
def analyze():
    url = (request.form.get("url") or "").strip() or None
    f = request.files.get("file")
    local_path = _clean_path(request.form.get("local_path"))
    if local_path:
        if not _is_local():
            return jsonify({"error": "Use the upload button on other devices."}), 400
        if not os.path.isfile(local_path):
            return jsonify({"error": "No file found at: %s" % local_path}), 400
        if not local_path.lower().endswith(VIDEO_EXT):
            return jsonify({"error": "That doesn't look like a video file (%s)." % os.path.basename(local_path)}), 400
        f = None
    if not url and not local_path and not (f and f.filename):
        return jsonify({"error": "Paste a YouTube link or choose a video file."}), 400
    settings = {
        "clip_len": max(10, min(180, int(request.form.get("clip_len", 40)))),
        "num_clips": max(1, min(50, int(request.form.get("num_clips", 20)))),
        "max_height": int(request.form.get("max_height", 1080)),
        "spoken_lang": request.form.get("spoken_lang") or None,
        "cookies_browser": request.form.get("cookies_browser") or None,
        "full_whisper": request.form.get("full_whisper") == "1",
        "max_comments": int(request.form.get("max_comments", 400)),
    }
    upload_name = None
    if f and f.filename:
        ext = os.path.splitext(f.filename)[1].lower() or ".mp4"
        upload_name = "upload" + ext
    p = pipeline.create_project(url, upload_name, settings, local_path=local_path)
    if f and f.filename:
        f.save(os.path.join(p["dir"], upload_name))
        p["title"] = os.path.splitext(f.filename)[0] if not url else None
    pipeline.start_analyze(p)
    return jsonify({"id": p["id"]})


@app.get("/api/project/<pid>")
def project(pid):
    p = pipeline.get(pid)
    if not p:
        abort(404)
    return jsonify(_public(p))


@app.post("/api/project/<pid>/<action>")
def job_control(pid, action):
    if action not in ("pause", "resume", "stop", "restart"):
        abort(404)
    p = pipeline.get(pid)
    if not p:
        abort(404)
    if action == "restart":
        if pipeline.busy(p):
            return jsonify({"error": "It's still working."}), 409
        pipeline.start_analyze(p)
        return jsonify({"ok": True})
    if not pipeline.busy(p):
        return jsonify({"error": "Nothing is running."}), 409
    if action == "stop" and control.current() != pid:  # still waiting in the queue
        control.stop(pid)
        pipeline._mark_stopped(p, pipeline._analyze if p.get("status") == "queued" else pipeline._render)
        pipeline.save(p)
        return jsonify({"ok": True})
    ok = getattr(control, action)(pid)
    if not ok:
        return jsonify({"error": "It hasn't started yet - you can Stop it, but not pause it."}), 409
    pipeline._log(p, {"pause": "Paused.", "resume": "Resumed.", "stop": "Stopping..."}[action])
    return jsonify({"ok": True})


@app.post("/api/project/<pid>/render")
def render(pid):
    p = pipeline.get(pid)
    if not p or p.get("status") != "ready":
        return jsonify({"error": "Project is not ready."}), 400
    if p.get("render_status") in ("queued", "running"):
        return jsonify({"error": "Already exporting - wait for it to finish."}), 409
    body = request.get_json(force=True)
    edits = {int(c["id"]): c for c in body.get("clips", [])}
    if not edits:
        return jsonify({"error": "Select at least one clip."}), 400
    for c in p["clips"]:
        e = edits.get(c["id"])
        if e:
            s, en = float(e["start"]), float(e["end"])
            if not (0 <= s < en <= p["duration"] + 0.01) or en - s < 3:
                return jsonify({"error": "Clip %d has invalid start/end." % c["id"]}), 400
            c["start"], c["end"] = round(s, 2), round(en, 2)
    o = body.get("options", {})

    def pick(key, allowed, default):
        v = o.get(key, default)
        return v if v in allowed else default

    flag = lambda k, d=True: bool(o.get(k, d))  # noqa: E731
    opts = {
        "format": pick("format", render_mod.FORMATS, "9:16"),
        "layout": pick("layout", ("split", "fill", "zoom", "blur"), "fill"),
        "caption_style": pick("caption_style", render_mod.CAPTION_STYLES, "yellow"),
        "captions": pick("captions", ("original", "translated", "off"), "original"),
        "caption_lang": o.get("caption_lang") or None,
        "hook_mode": pick("hook_mode", ("auto", "off"), "auto"),
        "hooks": {str(k): str(v)[:80] for k, v in (o.get("hooks") or {}).items() if str(v).strip()},
        "teaser": flag("teaser"), "intro": flag("intro"), "cut_dead_air": flag("cut_dead_air"),
        "punch_zoom": flag("punch_zoom"), "progress": flag("progress"),
        "ending": pick("ending", ("cta", "fade", "loop"), "cta"),
        "normalize": flag("normalize"), "whoosh": flag("whoosh"),
        "music": o.get("music") if o.get("music") in render_mod.list_music() else None,
        "music_volume": max(0.0, min(1.0, float(o.get("music_volume", 0.18)))),
        "dub_lang": o.get("dub_lang") or None,
        "dub_voice": o.get("dub_voice") or None,
        "original_volume": max(0.0, min(1.0, float(o.get("original_volume", 0.15)))),
        "whisper_lang": p["settings"].get("spoken_lang"),
        "whisper_size": pick("whisper_size", ("base", "small"), "base"),
        "mymemory_email": o.get("mymemory_email") or None,
    }
    opts["layouts"] = {str(k): v for k, v in (o.get("layouts") or {}).items() if v in ("split", "fill", "zoom", "blur")}
    uses_split = any((opts["layouts"].get(str(cid)) or opts["layout"]) == "split" for cid in edits)
    if uses_split and not (p.get("crop") or {}).get("face"):
        return jsonify({"error": "Split layout needs the facecam: click “Mark facecam” and drag a box over it."}), 400
    if opts["dub_lang"] and not opts["dub_voice"]:
        return jsonify({"error": "Pick a voice for the dub."}), 400
    pipeline.start_render(p, sorted(edits), opts)
    return jsonify({"ok": True})


@app.post("/api/project/<pid>/crop")
def save_crop(pid):
    p = pipeline.get(pid)
    if not p:
        abort(404)
    b = request.get_json(force=True)
    crop = dict(p.get("crop") or {})
    if "focus_x" in b:
        crop["focus_x"] = max(0.0, min(1.0, float(b["focus_x"])))
    if "split" in b:
        crop["split"] = max(0.25, min(0.55, float(b["split"])))
    if "face" in b:
        f = b["face"]
        if f is None:
            crop.pop("face", None)
        else:
            x, y = max(0.0, min(0.98, float(f["x"]))), max(0.0, min(0.98, float(f["y"])))
            w, h = max(0.02, min(1 - x, float(f["w"]))), max(0.02, min(1 - y, float(f["h"])))
            crop["face"] = {"x": round(x, 4), "y": round(y, 4), "w": round(w, 4), "h": round(h, 4)}
    p["crop"] = crop
    pipeline.save(p)
    return jsonify(crop)


@app.get("/api/project/<pid>/preview.jpg")
def preview(pid):
    p = pipeline.get(pid)
    if not p or not p.get("source") or not os.path.exists(p["source"]):
        abort(404)
    a = request.args
    opts = {k: a.get(k) for k in ("format", "layout", "caption_style", "captions", "hook_mode", "hook_text")}
    us = user_settings.load()
    proj = {"source": p["source"], "dir": p["dir"], "info": p.get("info"), "crop": p.get("crop"),
            "ctx": dict(us, streamer=us.get("streamer") or p.get("channel") or "")}
    out = os.path.join(p["dir"], "render", "preview.jpg")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    try:
        with _preview_lock:
            render_mod.preview_frame(proj, opts, float(a.get("t", 0)), out)
            with open(out, "rb") as f:
                data = f.read()
    except Exception as e:
        return jsonify({"error": "Preview failed: %s" % e}), 500
    resp = make_response(data)
    resp.headers["Content-Type"] = "image/jpeg"
    return resp


@app.get("/api/music")
def music():
    return jsonify(render_mod.list_music())


@app.get("/api/settings")
def get_settings():
    return jsonify({"settings": user_settings.public(),
                    "providers": {k: v["label"] for k, v in user_settings.PROVIDERS.items()}})


@app.post("/api/settings")
def set_settings():
    if not _is_local():
        return jsonify({"error": "Change settings on the PC."}), 403
    b = request.get_json(force=True)
    if b.get("ai_key") == "":  # empty field = keep the saved key
        b.pop("ai_key")
    user_settings.save(b)
    return jsonify(user_settings.public())


@app.post("/api/ai/models")
def ai_models():
    """List the models the saved key can use and pick a sensible default."""
    if not _is_local():
        return jsonify({"error": "Change settings on the PC."}), 403
    s = user_settings.load()
    ep = user_settings.ai_endpoint(s)
    if not ep:
        return jsonify({"error": "Choose a provider and save your API key first."}), 400
    try:
        models = publish.list_models(ep)
    except Exception as e:
        return jsonify({"error": "Could not reach the provider: %s" % str(e)[:200]}), 502
    best = s.get("ai_model") if s.get("ai_model") in models else publish.pick_default_model(models, s.get("ai_provider"))
    return jsonify({"models": models, "selected": best})


@app.post("/api/ai/test")
def ai_test():
    if not _is_local():
        return jsonify({"error": "Change settings on the PC."}), 403
    s = user_settings.load()
    ep = user_settings.ai_endpoint(s)
    if not ep or not ep.get("model"):
        return jsonify({"error": "Save a provider, key and model first."}), 400
    ctx = {"game": s["game"], "streamer": s["streamer"], "reasons": ["Loud reaction (+20 dB spike)"],
           "text": "bhai ye kya ho gaya, police aa gayi, bhaago bhaago", "text_en": "bro what happened, the police came, run run",
           "clip_id": 1}
    try:
        kit = publish.ai_kit(ctx, ep)
    except Exception as e:
        return jsonify({"error": "AI test failed: %s" % str(e)[:300]}), 502
    return jsonify({"ok": True, "hook": kit["hook"], "title": kit["yt_title"]})


@app.get("/media/<pid>/source")
def media_source(pid):
    p = pipeline.get(pid)
    if not p or not p.get("source") or not os.path.exists(p["source"]):
        abort(404)
    return send_file(p["source"], conditional=True)


@app.get("/media/<pid>/out/<name>")
def media_out(pid, name):
    p = pipeline.get(pid)
    if not p or not p.get("output_dir"):
        abort(404)
    return send_from_directory(p["output_dir"], name, conditional=True)


@app.post("/api/project/<pid>/open-folder")
def open_folder(pid):
    p = pipeline.get(pid)
    if p and p.get("output_dir") and os.path.isdir(p["output_dir"]):
        if request.remote_addr not in ("127.0.0.1", "::1"):
            return jsonify({"error": "The folder is on the PC - use the download links instead."}), 400
        if sys.platform == "win32":
            os.startfile(p["output_dir"])
        else:
            subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", p["output_dir"]])
        return jsonify({"ok": True})
    return jsonify({"error": "Nothing exported yet."}), 404


def _lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))  # no packet is sent; just picks the outgoing interface
        return s.getsockname()[0]
    except OSError:
        return "YOUR-PC-IP"
    finally:
        s.close()


def _already_running():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.5)
    try:
        return s.connect_ex(("127.0.0.1", PORT)) == 0
    finally:
        s.close()


def main():
    if _already_running():
        print("ClipForge is already running - opening it in your browser.")
        webbrowser.open("http://127.0.0.1:%d" % PORT)
        sys.exit(0)
    control.lower_priority()  # heavy work yields to whatever else you're doing on the PC
    h = config.health()
    print("ClipForge")
    for k, v in h.items():
        print("  %-8s %s" % (k, v or "NOT FOUND"))
    if not h["ffmpeg"] or not h["ffprobe"]:
        print("\nffmpeg is missing. Run setup.bat (or: winget install Gyan.FFmpeg) and try again.")
        sys.exit(1)
    if not h["node"]:
        print("  (Node.js not found - YouTube downloads may fail. Install from nodejs.org.)")
    url = "http://127.0.0.1:%d" % PORT
    print("\nOpen %s  (Ctrl+C to stop)" % url)
    if LAN:
        print("\nPHONE / OTHER DEVICE MODE (same Wi-Fi only). On the other device open:")
        print("    http://%s:%d/?key=%s" % (_lan_ip(), PORT, ACCESS_KEY))
        print("  The key changes every time you start. Don't share it. If Windows asks about the")
        print("  firewall, allow 'Private networks' only.")
    print()
    if "--no-browser" not in sys.argv:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    app.run(host=HOST, port=PORT, threaded=True, debug=False)


if __name__ == "__main__":
    main()
