"""Per-second "interest" signals and the clip picker.

Signals (each an array with one value per second, normalised 0..1):
  heatmap   - YouTube "Most replayed" graph
  comments  - how many (liked) comments point at this moment with a timestamp
  loudness  - audio spikes vs. the local baseline (screams, explosions, hype)
  speech    - excited words in the transcript
"""
import re
import subprocess

import numpy as np

from . import config, control

WEIGHTS = {"heatmap": 0.45, "comments": 0.30, "loudness": 0.20, "speech": 0.05}

TS_RE = re.compile(r"(?<![\d:])(?:(\d{1,2}):)?(\d{1,2}):(\d{2})(?![\d:])")
HYPE_WORDS = re.compile(
    r"\b(oh my god|omg|no way|what|wow|insane|crazy|bro|holy|let'?s go|run|help|wait|dude|"
    r"look|yes|no no|nah|damn|clip (that|it)|wtf|hahaha+|lol)\b", re.I)


def heatmap_signal(heatmap, n):
    s = np.zeros(n)
    for h in heatmap:
        a, b = int(h["start_time"]), int(np.ceil(h["end_time"]))
        s[max(a, 0):min(b, n)] = np.maximum(s[max(a, 0):min(b, n)], float(h["value"]))
    return s


def comment_signal(comments, n):
    """Returns (signal, list of (second, text, likes))."""
    s = np.zeros(n)
    hits = []
    for c in comments:
        text = c.get("text") or ""
        found = set()
        for m in TS_RE.finditer(text):
            h, mi, se = m.group(1), int(m.group(2)), int(m.group(3))
            if se >= 60:
                continue
            t = (int(h) * 3600 if h else 0) + mi * 60 + se
            if 0 <= t < n and t not in found:
                found.add(t)
        for t in found:
            w = 1.0 + np.log1p(c.get("likes") or 0)
            hits.append((t, text[:140], c.get("likes") or 0))
            # People timestamp the START of a moment; credit the next ~20 s.
            a, b = max(t - 3, 0), min(t + 20, n)
            ramp = np.exp(-np.arange(b - a) / 12.0)
            s[a:b] += w * ramp
    return s, hits


def loudness_signal(path, n):
    """RMS loudness per second (dB) minus a rolling 60 s median -> spikes."""
    sr = 4000
    cmd = [config.ffmpeg(), "-v", "error", "-i", path, "-vn", "-ac", "1", "-ar", str(sr), "-f", "s16le", "-"]
    p = control.popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    rms = []
    try:
        while True:
            chunk = p.stdout.read(sr * 2)
            if not chunk:
                break
            a = np.frombuffer(chunk, np.int16).astype(np.float32) / 32768.0
            rms.append(20 * np.log10(np.sqrt(np.mean(a * a)) + 1e-6))
        p.wait()
    finally:
        control.forget(p)
    control.checkpoint()
    db = np.full(n, -60.0)
    m = min(n, len(rms))
    db[:m] = rms[:m]
    base = np.array([np.median(db[max(0, i - 30):i + 30]) for i in range(n)])
    spike = np.clip(db - base, 0, None)
    return spike, db


def speech_signal(words, n):
    s = np.zeros(n)
    text_by_sec = {}
    for w in words:
        i = int(w["t"])
        if 0 <= i < n:
            text_by_sec.setdefault(i, []).append(w["w"])
    for i, toks in text_by_sec.items():
        line = " ".join(toks)
        s[i] += len(HYPE_WORDS.findall(line)) + line.count("!") * 0.5
    return s


def _norm(x):
    x = np.asarray(x, float)
    hi = np.percentile(x, 99.5) if x.size else 0
    if hi <= 0:
        return np.zeros_like(x)
    return np.clip(x / hi, 0, 1)


def _smooth(x, k=5):
    if k <= 1:
        return x
    return np.convolve(x, np.ones(k) / k, mode="same")


def combine(sig):
    """sig: dict name -> raw array. Missing/empty signals are dropped and weights renormalised."""
    used = {k: _norm(v) for k, v in sig.items() if v is not None and np.any(np.asarray(v) > 0)}
    if not used:
        n = len(next(iter(sig.values())))
        return np.zeros(n), []
    total = sum(WEIGHTS[k] for k in used)
    score = sum(WEIGHTS[k] / total * _smooth(v, 3) for k, v in used.items())
    return _smooth(score, 5), list(used)


def pick_clips(score, n_clips, clip_len, duration, phrases=None, min_len=12):
    """Greedy non-overlapping windows with the highest mean score; edges snapped to speech."""
    n = len(score)
    L = int(min(clip_len, max(min_len, duration)))
    if n <= L:
        return [{"start": 0.0, "end": float(duration), "score": 100}]
    c = np.concatenate([[0], np.cumsum(score)])
    win = (c[L:] - c[:-L]) / L  # mean score of window starting at i
    taken = np.zeros(len(win), bool)
    best = float(win.max()) or 1.0
    clips = []
    for _ in range(n_clips):
        cand = np.where(taken, -1, win)
        i = int(np.argmax(cand))
        if cand[i] < 0:
            break
        start, end = float(i), float(i + L)
        if phrases:
            start, end = _snap(start, end, phrases, duration)
        clips.append({"start": round(start, 2), "end": round(end, 2),
                      "score": int(round(100 * win[i] / best))})
        taken[max(0, i - L + 1):min(len(win), i + L)] = True
    clips.sort(key=lambda x: -x["score"])
    return clips


def _snap(start, end, phrases, duration):
    """Move start back to the beginning of a sentence, end forward to the end of one (<= 4 s)."""
    starts = [p["t"] for p in phrases if start - 4 <= p["t"] <= start + 0.5]
    if starts:
        before = [s for s in starts if s <= start]
        start = max(before) if before else min(starts)
    ends = [p["e"] for p in phrases if end - 0.5 <= p["e"] <= end + 4]
    if ends:
        end = min(ends, key=lambda e: abs(e - end))
    start = max(0.0, start - 0.15)
    end = min(duration, end + 0.35)
    return start, end


def explain(clip, sig_raw, comment_hits, used):
    """Human-readable reasons a clip was chosen."""
    a, b = int(clip["start"]), int(np.ceil(clip["end"]))
    reasons = []
    if "heatmap" in used:
        h = sig_raw["heatmap"]
        peak = float(np.max(h[a:b])) if b > a else 0.0
        top_pct = 100 * float(np.mean(h >= peak))  # share of the video at least this replayed
        reasons.append("Most replayed: top %d%% of the video" % max(1, round(top_pct)))
    hits = [x for x in comment_hits if a <= x[0] < b]
    if hits:
        reasons.append("%d timestamp comment%s point here" % (len(hits), "s" if len(hits) > 1 else ""))
    if "loudness" in used:
        full = sig_raw["loudness"]
        sp = full[a:b]
        # Only call it out if this is among the loudest 5% of moments in the whole video.
        if sp.size and float(sp.max()) > 6 and float(sp.max()) >= float(np.percentile(full, 95)):
            if float(np.mean(full >= sp.max())) <= 0.05:
                reasons.append("Loud reaction (+%d dB spike)" % round(float(sp.max())))
    top = sorted(hits, key=lambda x: -x[2])[:2]
    return reasons, [{"t": x[0], "text": x[1], "likes": x[2]} for x in top]
