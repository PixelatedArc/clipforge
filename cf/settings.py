"""User settings kept on this PC (workspace/settings.json - never uploaded or committed)."""
import json
import os
import threading

from . import config

_FILE = os.path.join(config.WORKSPACE, "settings.json")
_lock = threading.Lock()

# OpenAI-compatible endpoints. The model list is fetched live from the provider (models change often).
PROVIDERS = {
    "none": {"label": "Off - use built-in templates", "base_url": ""},
    "gemini": {"label": "Google Gemini (free key: aistudio.google.com)",
               "base_url": "https://generativelanguage.googleapis.com/v1beta/openai"},
    "groq": {"label": "Groq (free key: console.groq.com)", "base_url": "https://api.groq.com/openai/v1"},
    "custom": {"label": "Other OpenAI-compatible (OpenRouter, Ollama, ...)", "base_url": ""},
}

DEFAULTS = {
    "game": "GTA 6",
    "streamer": "",          # who you clip - used for "Credit: ..." lines
    "streamer_url": "",
    "my_handle": "",         # your channel handle, e.g. @gta6moments
    "show_handle": True,     # small @handle on the video
    "show_credit": True,     # small "credit: Streamer" on the video
    "extra_hashtags": "",
    "ai_provider": "none",
    "ai_base_url": "",
    "ai_key": "",
    "ai_model": "",
}


def load():
    with _lock:
        data = dict(DEFAULTS)
        try:
            with open(_FILE, encoding="utf-8") as f:
                data.update({k: v for k, v in json.load(f).items() if k in DEFAULTS})
        except (OSError, ValueError):
            pass
        return data


def save(new):
    cur = load()
    for k, v in new.items():
        if k in DEFAULTS:
            cur[k] = type(DEFAULTS[k])(v) if not isinstance(DEFAULTS[k], bool) else bool(v)
    with _lock:
        tmp = _FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cur, f, ensure_ascii=False, indent=1)
        os.replace(tmp, _FILE)
    return cur


def public():
    """Settings for the browser: the key is never sent back, only whether one is saved."""
    s = load()
    s["ai_key_set"] = bool(s.pop("ai_key"))
    return s


def ai_endpoint(s=None):
    s = s or load()
    p = s.get("ai_provider", "none")
    if p == "none" or not s.get("ai_key") and p != "custom":
        return None
    base = (s.get("ai_base_url") if p == "custom" else PROVIDERS.get(p, {}).get("base_url")) or ""
    return {"base_url": base.rstrip("/"), "key": s.get("ai_key", ""), "model": s.get("ai_model", "")} if base else None
