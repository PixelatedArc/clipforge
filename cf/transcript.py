"""Transcripts: YouTube json3 captions -> timed words, Whisper fallback, phrase grouping.

A transcript is a list of words: {"t": start_sec, "e": end_sec, "w": "text"}.
"""
import re
import threading

import numpy as np

from . import config, control

_model = None
_model_lock = threading.Lock()


def parse_json3(data):
    words = []
    events = [e for e in data.get("events", []) if e.get("segs")]
    for ev in events:
        t0 = ev.get("tStartMs", 0) / 1000.0
        t_end = t0 + ev.get("dDurationMs", 0) / 1000.0
        segs = [s for s in ev["segs"] if s.get("utf8", "").strip()]
        if not segs:
            continue
        has_offsets = any("tOffsetMs" in s for s in segs)
        if has_offsets or len(segs) == 1 and len(segs[0]["utf8"].split()) == 1:
            # Auto captions: one seg per word with offsets.
            for s in segs:
                txt = s["utf8"].replace("\n", " ").strip()
                if txt:
                    words.append({"t": t0 + s.get("tOffsetMs", 0) / 1000.0, "e": None, "w": txt})
        else:
            # Manual captions: a whole line per event -> spread words evenly.
            text = " ".join(s["utf8"] for s in segs).replace("\n", " ")
            toks = text.split()
            if not toks:
                continue
            span = max(t_end - t0, 0.2)
            for i, tok in enumerate(toks):
                words.append({"t": t0 + span * i / len(toks), "e": t0 + span * (i + 1) / len(toks), "w": tok})
        if words and words[-1]["e"] is None:
            words[-1]["_evend"] = t_end
    words.sort(key=lambda w: w["t"])
    # Fill in missing end times: next word start, capped.
    for i, w in enumerate(words):
        if w["e"] is None:
            nxt = words[i + 1]["t"] if i + 1 < len(words) else w["t"] + 0.6
            cap = w.pop("_evend", w["t"] + 1.5)
            w["e"] = max(w["t"] + 0.08, min(nxt, cap, w["t"] + 1.5))
        else:
            w.pop("_evend", None)
    # Drop bracketed non-speech like [Music], [Applause].
    return [w for w in words if not re.fullmatch(r"\[.*\]", w["w"])]


def load_audio(path, start=None, duration=None, sr=16000):
    cmd = [config.ffmpeg(), "-v", "error"]
    if start is not None:
        cmd += ["-ss", "%.3f" % start]
    if duration is not None:
        cmd += ["-t", "%.3f" % duration]
    cmd += ["-i", path, "-vn", "-f", "s16le", "-ac", "1", "-ar", str(sr), "-"]
    raw = control.run(cmd, check=False, text=False).stdout
    return np.frombuffer(raw, np.int16).astype(np.float32) / 32768.0


def _get_model(size="base"):
    global _model
    with _model_lock:
        if _model is None or _model[0] != size:
            from faster_whisper import WhisperModel
            _model = (size, WhisperModel(size, device="cpu", compute_type="int8", download_root=config.MODELS,
                                         cpu_threads=control.THREADS))
        return _model[1]


def whisper_words(path, start=0.0, duration=None, language=None, size="base", task="transcribe"):
    """Transcribe [start, start+duration] of a media file. Returns (words, language).

    task="translate" makes Whisper write English straight from the speech, which is far better
    than translating its text afterwards (especially for Hinglish / mixed speech).
    Word times are absolute (relative to the source file)."""
    audio = load_audio(path, start, duration)
    if audio.size < 1600:
        return [], language
    model = _get_model(size)
    # condition_on_previous_text=False stops Whisper's repetition loops (e.g. "& & & &" on noisy speech).
    segs, info = model.transcribe(audio, language=language or None, word_timestamps=True,
                                  vad_filter=True, beam_size=1 if size == "base" else 3, task=task,
                                  condition_on_previous_text=False)
    words = []
    for s in segs:
        control.checkpoint()   # Pause / Stop between segments
        for w in (s.words or []):
            txt = w.word.strip()
            if txt:
                words.append({"t": float(w.start) + start, "e": float(w.end) + start, "w": txt})
    return clean_words(words), info.language


_HAS_WORD = re.compile(r"\w", re.UNICODE)


def clean_words(words):
    """Drop tokens with no letters/digits and give up on obviously hallucinated output."""
    out = [w for w in words if _HAS_WORD.search(w["w"])]
    if len(out) >= 8:
        counts = {}
        for w in out:
            k = w["w"].lower().strip(".,!?")
            counts[k] = counts.get(k, 0) + 1
        top = max(counts.values())
        if top / len(out) > 0.5:  # one token repeated over and over = Whisper loop, not speech
            return []
    return out


def words_in(words, start, end):
    return [w for w in words if w["t"] >= start - 0.05 and w["t"] < end]


def phrases(words, max_words=12, max_gap=0.7, max_len=5.5):
    """Group words into short phrases (for translation, dubbing, snapping clip edges)."""
    out, cur = [], []
    for w in words:
        if cur:
            gap = w["t"] - cur[-1]["e"]
            too_long = len(cur) >= max_words or (w["e"] - cur[0]["t"]) > max_len
            if gap > max_gap or too_long:
                out.append(cur)
                cur = []
        cur.append(w)
        if re.search(r"[.!?।]$", w["w"]):  # sentence end (incl. Devanagari danda)
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return [{"t": p[0]["t"], "e": p[-1]["e"], "text": " ".join(x["w"] for x in p), "words": p} for p in out]
