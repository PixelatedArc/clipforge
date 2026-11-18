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

from cf import config, dub, pipeline, translate  # noqa: E402

PORT = 8765
LAN = "--lan" in sys.argv          # allow phones/other PCs on the same Wi-Fi
HOST = "0.0.0.0" if LAN else "127.0.0.1"
ACCESS_KEY = secrets.token_urlsafe(6) if LAN else None
app = Flask(__name__, static_folder="static", static_url_path="/static")
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 ** 3  # 20 GB uploads
app.json.sort_keys = False  # keep language list in our order (English first)


def _public(p):
    keys = ["id", "title", "url", "status", "stage", "progress", "log", "clips", "duration", "error",
            "source_lang", "renders", "render_status", "output_dir", "signal_curve", "settings", "warnings"]
    d = {k: p.get(k) for k in keys}
    d["has_source"] = bool(p.get("source") and os.path.exists(p["source"]))
    return d


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
    return jsonify(config.health())


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
    if not url and not (f and f.filename):
        return jsonify({"error": "Paste a YouTube link or attach a video file."}), 400
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
    p = pipeline.create_project(url, upload_name, settings)
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
    opts = {
        "layout": o.get("layout", "blur"),
        "captions": o.get("captions", "original"),
        "caption_lang": o.get("caption_lang") or None,
        "dub_lang": o.get("dub_lang") or None,
        "dub_voice": o.get("dub_voice") or None,
        "original_volume": max(0.0, min(1.0, float(o.get("original_volume", 0.15)))),
        "whisper_lang": p["settings"].get("spoken_lang"),
        "mymemory_email": o.get("mymemory_email") or None,
    }
    if opts["dub_lang"] and not opts["dub_voice"]:
        return jsonify({"error": "Pick a voice for the dub."}), 400
    pipeline.start_render(p, sorted(edits), opts)
    return jsonify({"ok": True})


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
