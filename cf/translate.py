"""Free translation: Google (unofficial endpoint) first, MyMemory as fallback."""
import json
import os
import threading

from deep_translator import GoogleTranslator, MyMemoryTranslator

from . import config

LANGS = {
    "en": "English", "hi": "Hindi", "ur": "Urdu", "bn": "Bengali", "pa": "Punjabi",
    "ta": "Tamil", "te": "Telugu", "mr": "Marathi", "es": "Spanish", "pt": "Portuguese",
    "fr": "French", "de": "German", "it": "Italian", "ar": "Arabic", "tr": "Turkish",
    "ru": "Russian", "id": "Indonesian", "ja": "Japanese", "ko": "Korean", "zh-CN": "Chinese",
}

# MyMemory wants locale codes.
_MM = {"en": "en-GB", "hi": "hi-IN", "ur": "ur-PK", "bn": "bn-IN", "pa": "pa-IN", "ta": "ta-IN",
       "te": "te-IN", "mr": "mr-IN", "es": "es-ES", "pt": "pt-BR", "fr": "fr-FR", "de": "de-DE",
       "it": "it-IT", "ar": "ar-SA", "tr": "tr-TR", "ru": "ru-RU", "id": "id-ID", "ja": "ja-JP",
       "ko": "ko-KR", "zh-CN": "zh-CN"}

_CACHE_FILE = os.path.join(config.WORKSPACE, "translation_cache.json")
_lock = threading.Lock()
_google_dead = False  # set after a 429 so we stop wasting time on it this session


def _load_cache():
    try:
        with open(_CACHE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_cache(c):
    tmp = _CACHE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(c, f, ensure_ascii=False)
    os.replace(tmp, _CACHE_FILE)


def _norm_src(src):
    if not src:
        return "auto"
    src = src.split("-")[0] if src not in _MM else src
    return src


def translate_lines(lines, src, dst, log, mymemory_email=None):
    """Translate a list of short strings. Returns list of same length."""
    global _google_dead
    src = _norm_src(src)
    if src != "auto" and src.split("-")[0] == dst.split("-")[0]:
        return list(lines)
    with _lock:
        cache = _load_cache()
    out, todo = [], []
    for i, line in enumerate(lines):
        key = "%s|%s|%s" % (src, dst, line)
        if key in cache:
            out.append(cache[key])
        else:
            out.append(None)
            todo.append(i)
    if todo and not _google_dead:
        try:
            g = GoogleTranslator(source=src, target=dst)
            res = g.translate_batch([lines[i] for i in todo])
            for i, r in zip(todo, res):
                out[i] = r
            todo = []
        except Exception as e:
            log("Google Translate unavailable (%s) - switching to MyMemory." % type(e).__name__)
            _google_dead = True
            for i in todo:
                out[i] = None
    if todo:
        mm_src = _MM.get(src, "en-GB") if src != "auto" else "en-GB"
        mm = MyMemoryTranslator(source=mm_src, target=_MM.get(dst, dst))
        for i in todo:
            try:
                kw = {"de": mymemory_email} if mymemory_email else {}
                out[i] = mm.translate(lines[i][:480], **kw)
            except Exception as e:
                raise RuntimeError("Translation failed (Google blocked/rate-limited and MyMemory said: %s). "
                                   "MyMemory's free limit is ~5,000 characters/day; add an email in "
                                   "Settings to raise it to ~50,000." % str(e)[:160])
    with _lock:
        cache = _load_cache()
        for line, r in zip(lines, out):
            if r:
                cache["%s|%s|%s" % (src, dst, line)] = r
        _save_cache(cache)
    return [r or l for r, l in zip(out, lines)]
