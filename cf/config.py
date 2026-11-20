"""Paths, tool discovery and one-time environment fixes."""
import glob
import os
import shutil
import subprocess
import sys
from functools import lru_cache

# Trust the Windows certificate store. Needed on networks that inspect HTTPS
# (corporate proxies); harmless everywhere else.
try:
    import truststore
    truststore.inject_into_ssl()
except Exception:  # pragma: no cover
    pass

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKSPACE = os.path.join(ROOT, "workspace")
OUTPUT = os.path.join(ROOT, "output")
MODELS = os.path.join(ROOT, "models")
for _d in (WORKSPACE, OUTPUT, MODELS):
    os.makedirs(_d, exist_ok=True)

NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def _find_exe(name):
    p = shutil.which(name)
    if p:
        return p
    local = os.environ.get("LOCALAPPDATA", "")
    candidates = [
        os.path.join(ROOT, "tools", name + ".exe"),
        os.path.join(local, "Microsoft", "WinGet", "Links", name + ".exe"),
    ]
    candidates += glob.glob(os.path.join(local, "Microsoft", "WinGet", "Packages", "*FFmpeg*", "*", "bin", name + ".exe"))
    candidates += [r"C:\ffmpeg\bin\%s.exe" % name, r"C:\Program Files\ffmpeg\bin\%s.exe" % name]
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    return None


@lru_cache(None)
def ffmpeg():
    return _find_exe("ffmpeg")


@lru_cache(None)
def ffprobe():
    return _find_exe("ffprobe")


@lru_cache(None)
def node():
    return shutil.which("node")


def run(cmd, cwd=None, check=True):
    """Run a command, return CompletedProcess; raise with stderr tail on failure.

    Goes through control.run so the Pause / Stop buttons reach it."""
    from . import control
    return control.run(cmd, cwd=cwd, check=check)


@lru_cache(None)
def video_encoder():
    """Pick Intel QuickSync if it actually works on this machine, else libx264."""
    ff = ffmpeg()
    if not ff:
        return None
    test = os.path.join(WORKSPACE, "_enc_test.mp4")
    try:
        r = run([ff, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                 "testsrc2=size=1080x1920:rate=30", "-t", "1", "-vf", "format=nv12",
                 "-c:v", "h264_qsv", "-global_quality", "23", "-y", test], check=False)
        ok = r.returncode == 0 and os.path.exists(test) and os.path.getsize(test) > 0
    finally:
        if os.path.exists(test):
            os.remove(test)
    return "h264_qsv" if ok else "libx264"


def encoder_args(enc):
    if enc == "h264_qsv":
        return ["-c:v", "h264_qsv", "-global_quality", "22", "-look_ahead", "0"]
    from . import control
    return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-threads", str(control.THREADS)]


def health():
    enc = video_encoder()
    return {
        "ffmpeg": ffmpeg(),
        "ffprobe": ffprobe(),
        "node": node(),
        "encoder": enc,
        "python": sys.version.split()[0],
    }
