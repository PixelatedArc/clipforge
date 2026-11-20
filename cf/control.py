"""Pause / resume / stop for the running job, and keeping the PC responsive.

There is one worker, so one job runs at a time. Pausing freezes the ffmpeg processes it started
(they use no CPU while frozen) and holds the Python side at the next checkpoint. Stopping kills
those processes and makes the job end at the next checkpoint.
"""
import os
import subprocess
import sys
import threading

from . import config


class Cancelled(Exception):
    """Raised inside a job when the user pressed Stop."""


_lock = threading.Lock()
_running = threading.Event()   # set = not paused
_running.set()
_cancel = threading.Event()
_procs = set()
_current = None                # project id of the job in progress
_stopped_pids = set()          # queued jobs the user stopped before they began

# Leave CPU cores free so the PC stays usable while ClipForge works.
CPU = os.cpu_count() or 4
THREADS = max(1, CPU - 2) if CPU > 4 else max(1, CPU - 1)


# ------------------------------------------------------------ process priority
def lower_priority():
    """Run ClipForge (and every ffmpeg it starts, which inherits this) at below-normal priority."""
    try:
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes
            BELOW_NORMAL = 0x4000
            k32 = ctypes.windll.kernel32
            k32.GetCurrentProcess.restype = wintypes.HANDLE   # 64-bit handle; default int truncates it
            k32.SetPriorityClass.argtypes = (wintypes.HANDLE, wintypes.DWORD)
            k32.SetPriorityClass(k32.GetCurrentProcess(), BELOW_NORMAL)
        else:
            os.nice(10)
    except Exception:
        pass


def _suspend(p, on):
    try:
        if sys.platform == "win32":
            import ctypes
            fn = ctypes.windll.ntdll.NtSuspendProcess if on else ctypes.windll.ntdll.NtResumeProcess
            fn.argtypes = (ctypes.c_void_p,)
            fn(ctypes.c_void_p(int(p._handle)))
        else:
            import signal
            os.kill(p.pid, signal.SIGSTOP if on else signal.SIGCONT)
    except Exception:
        pass


# ------------------------------------------------------------ job lifecycle
def begin(pid):
    global _current
    with _lock:
        _current = pid
        _cancel.clear()
        _running.set()


def end():
    global _current
    with _lock:
        _current = None
        _cancel.clear()
        _running.set()


def current():
    return _current


def is_paused():
    return _current is not None and not _running.is_set()


def take_stopped(pid):
    """True (once) if this queued job was stopped before it started."""
    with _lock:
        if pid in _stopped_pids:
            _stopped_pids.discard(pid)
            return True
        return False


def pause(pid):
    with _lock:
        if pid != _current or _cancel.is_set():
            return False
        _running.clear()
        for p in list(_procs):
            _suspend(p, True)
        return True


def resume(pid):
    with _lock:
        if pid != _current:
            return False
        for p in list(_procs):
            _suspend(p, False)
        _running.set()
        return True


def stop(pid):
    with _lock:
        if pid != _current:
            _stopped_pids.add(pid)   # still waiting in the queue
            return True
        _cancel.set()
        for p in list(_procs):
            _suspend(p, False)       # a frozen process can't be killed cleanly on every OS
            try:
                p.kill()
            except Exception:
                pass
        _running.set()
        return True


def cancelled():
    return _cancel.is_set()


def checkpoint():
    """Call often from job code: waits while paused, raises Cancelled after Stop."""
    while not _running.wait(0.5):
        pass
    if _cancel.is_set():
        raise Cancelled()


# ------------------------------------------------------------ tracked processes
def popen(cmd, **kw):
    checkpoint()
    kw.setdefault("creationflags", config.NO_WINDOW)
    p = subprocess.Popen(cmd, **kw)
    with _lock:
        _procs.add(p)
        if not _running.is_set():
            _suspend(p, True)       # paused while it was starting
    return p


def forget(p):
    with _lock:
        _procs.discard(p)


def run(cmd, cwd=None, check=True, text=True):
    """Like subprocess.run(capture_output=True) but pausable and stoppable."""
    kw = dict(cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if text:
        kw.update(text=True, encoding="utf-8", errors="replace")
    p = popen(cmd, **kw)
    try:
        out, err = p.communicate()
    finally:
        forget(p)
    if _cancel.is_set():
        raise Cancelled()
    r = subprocess.CompletedProcess(cmd, p.returncode, out, err)
    if check and r.returncode != 0:
        tail = (err or "").strip().splitlines()[-12:] if text else []
        raise RuntimeError("Command failed: %s\n%s" % (os.path.basename(cmd[0]), "\n".join(tail)))
    return r
