"""Upload kit for each reel: on-screen hook, titles, captions and hashtags per platform.

Uses an AI writer when the user saved a (free) API key, otherwise smart templates. Either way the
streamer credit is always added, and every text is kept inside each platform's limits.
"""
import json
import re
import urllib.request

from . import settings as user_settings

YT_TITLE_MAX = 100
TIKTOK_MAX = 2200
IG_MAX = 2200

_GAME_TAGS = {
    "gta": ["#gta6", "#gtavi", "#gta", "#gta6gameplay", "#rockstargames"],
    "minecraft": ["#minecraft", "#minecraftmemes"],
    "valorant": ["#valorant", "#valorantclips"],
    "fortnite": ["#fortnite", "#fortniteclips"],
    "bgmi": ["#bgmi", "#pubgmobile"],
}
_GENERIC_TAGS = ["#gaming", "#gamingclips", "#gamer", "#funnymoments", "#streamer"]
_PLATFORM_TAGS = {"yt": ["#shorts"], "ig": ["#reels", "#reelsinstagram", "#explore"], "tt": ["#fyp", "#foryou"]}

# On-screen hooks: short, punchy, no emoji (emoji don't render in burned-in text).
_HOOKS = {
    "loud": ["HE WAS NOT READY FOR THIS", "WAIT FOR HIS REACTION", "THIS WENT WRONG SO FAST",
             "HE DID NOT EXPECT THIS", "THE WAY HE REACTED"],
    "crowd": ["EVERYONE REPLAYED THIS PART", "CHAT LOST IT HERE", "THE MOST REPLAYED MOMENT"],
    "funny": ["THIS IS TOO FUNNY", "I CAN'T STOP LAUGHING", "BRO REALLY SAID THAT"],
    "default": ["{GAME} IS INSANE", "WATCH TILL THE END", "THIS {GAME} MOMENT THOUGH", "NOBODY TALKS ABOUT THIS"],
}
_FUNNY = re.compile(r"\b(lol|lmao|haha+|hehe|😂|🤣|💀|funny|joke|mazaak|hasi)\b", re.I)


def _tag(s):
    s = re.sub(r"[^0-9A-Za-z]", "", s or "")
    return "#" + s.lower() if s else ""


def hashtags(game, streamer, platform, extra=""):
    tags = []
    g = (game or "").lower()
    for key, lst in _GAME_TAGS.items():
        if key in g:
            tags += lst
    if not tags and game:
        tags.append(_tag(game))
    if streamer:
        tags.append(_tag(streamer))
    tags += _PLATFORM_TAGS.get(platform, [])
    tags += _GENERIC_TAGS
    tags += [t if t.startswith("#") else "#" + t for t in re.split(r"[\s,]+", extra or "") if t.strip("#")]
    out, seen = [], set()
    for t in tags:
        if t and t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
    limit = {"yt": 5, "tt": 6, "ig": 15}.get(platform, 8)
    return out[:limit]


def _kind(ctx):
    reasons = " ".join(ctx.get("reasons") or []).lower()
    text = (ctx.get("text_en") or "") + " " + (ctx.get("text") or "") + " " + (ctx.get("top_comment") or "")
    if _FUNNY.search(text):
        return "funny"
    if "replayed" in reasons or "comment" in reasons:
        return "crowd"
    if "loud" in reasons:
        return "loud"
    return "default"


def _title_case(s):
    small = {"a", "an", "the", "of", "in", "on", "to", "for", "and", "or", "is"}
    w = s.lower().split()
    return " ".join(x if (i and x in small) else x[:1].upper() + x[1:] for i, x in enumerate(w))


def template_kit(ctx):
    game = ctx.get("game") or "this game"
    pool = _HOOKS[_kind(ctx)]
    hook = pool[(int(ctx.get("clip_id") or 0) - 1) % len(pool)].replace("{GAME}", game.upper())
    nice = _title_case(hook)
    streamer = ctx.get("streamer") or ctx.get("channel") or ""
    line = ctx.get("text_en") or ""
    quote = ""
    if line:
        words = line.split()
        quote = " ".join(words[:14]) + ("..." if len(words) > 14 else "")
    return {
        "hook": hook,
        "yt_title": "%s 😳 %s" % (nice, ("| " + game) if game else ""),
        "yt_description": "%s%s" % (nice + " 😂", ("\n\n\"" + quote + "\"") if quote else ""),
        "ig_caption": "%s 😂🔥" % nice,
        "tiktok_caption": "%s 😂" % nice,
        "hashtags": [],
        "writer": "templates",
    }


# ------------------------------------------------------------------ AI writer
def _http_json(url, key, body=None, timeout=40):
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": "Bearer " + key, "Content-Type": "application/json",
                                          "User-Agent": "ClipForge"},
                                 method="POST" if body is not None else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def list_models(ep):
    """Ask the provider which models this key can use (so we never hard-code stale names)."""
    data = _http_json(ep["base_url"] + "/models", ep["key"])
    ids = [m.get("id", "") for m in data.get("data", []) if m.get("id")]
    skip = ("embed", "tts", "whisper", "image", "vision-only", "guard", "audio", "live", "veo", "imagen", "aqa")
    chat = [i for i in ids if not any(s in i.lower() for s in skip)]
    return chat or ids


def pick_default_model(models, provider):
    def score(m):
        m = m.lower()
        s = 0
        if provider == "gemini":
            s += 5 * ("flash" in m) - 3 * ("lite" in m) - 4 * ("preview" in m or "exp" in m) - 6 * ("pro" in m)
        else:
            s += 4 * ("70b" in m or "120b" in m) + 2 * ("versatile" in m) - 3 * ("8b" in m or "instant" in m)
        return s
    return sorted(models, key=score, reverse=True)[0] if models else ""


_PROMPT = """You write viral short-form gaming content (YouTube Shorts, Instagram Reels, TikTok) for a clip
channel. Write in ENGLISH (the streamer may speak Hindi/Hinglish - understand it, write English).

Game: {game}
Streamer (credit them, don't pretend to be them): {streamer}
Why this moment was picked: {reasons}
Top viewer comment about it: {comment}
What is said in the clip (original): {text}
English meaning (auto): {text_en}

Return ONLY a JSON object with these keys:
"hook": on-screen text for the first 2 seconds, 3-7 words, curiosity or shock, NO emoji, no hashtags,
"yt_title": YouTube Shorts title under 70 characters, punchy, 1 emoji max, no hashtags,
"yt_description": 1-2 short sentences about the moment, no hashtags,
"ig_caption": Instagram caption, 1-2 lines with emoji, ends with a question to drive comments, no hashtags,
"tiktok_caption": under 100 characters with emoji, no hashtags,
"hashtags": 6-10 relevant lowercase hashtags starting with #.
Be specific to what actually happens; never invent events that the text doesn't support."""


def ai_kit(ctx, ep):
    prompt = _PROMPT.format(
        game=ctx.get("game") or "unknown", streamer=ctx.get("streamer") or ctx.get("channel") or "unknown",
        reasons=", ".join(ctx.get("reasons") or []) or "loud moment", comment=(ctx.get("top_comment") or "-")[:300],
        text=(ctx.get("text") or "-")[:1500], text_en=(ctx.get("text_en") or "-")[:1500])
    data = _http_json(ep["base_url"] + "/chat/completions", ep["key"], {
        "model": ep["model"], "temperature": 0.8,
        "messages": [{"role": "user", "content": prompt}]})
    content = data["choices"][0]["message"]["content"] or ""
    m = re.search(r"\{.*\}", content, re.S)
    if not m:
        raise ValueError("AI reply had no JSON")
    out = json.loads(m.group(0))
    kit = {k: str(out.get(k) or "").strip() for k in ("hook", "yt_title", "yt_description", "ig_caption", "tiktok_caption")}
    if not kit["hook"] or not kit["yt_title"]:
        raise ValueError("AI reply was missing the hook/title")
    tags = out.get("hashtags") or []
    kit["hashtags"] = [("#" + re.sub(r"[^0-9A-Za-z_]", "", t)).lower() for t in tags if re.sub(r"[^0-9A-Za-z_]", "", str(t))]
    kit["hook"] = re.sub(r"\s+", " ", re.sub(r"[^\w\s'?!.,-]", "", kit["hook"])).strip().upper()[:60]
    kit["writer"] = "AI (%s)" % ep["model"]
    return kit


# ------------------------------------------------------------------ assemble per platform
def _merge_tags(ai_tags, base):
    out, seen = [], set()
    for t in list(base[:3]) + list(ai_tags) + list(base[3:]):
        if t and t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
    return out


def make_kit(ctx, log=lambda m: None):
    """ctx: game, streamer, streamer_url, channel, my_handle, reasons, text, text_en, top_comment, clip_id,
    extra_hashtags. Returns the finished kit with ready-to-paste text per platform."""
    s = user_settings.load()
    kit = None
    ep = user_settings.ai_endpoint(s)
    if ep and ep.get("model"):
        try:
            kit = ai_kit(ctx, ep)
            log("Upload kit written by AI (%s)." % ep["model"])
        except Exception as e:
            log("AI writer failed (%s) - using templates." % str(e)[:150])
    if kit is None:
        kit = template_kit(ctx)

    game, streamer = ctx.get("game") or "", ctx.get("streamer") or ctx.get("channel") or ""
    credit = ("🎥 Credit: %s%s" % (streamer, (" - " + ctx["streamer_url"]) if ctx.get("streamer_url") else "")) if streamer else ""
    follow = ("Follow %s for daily %s clips 🔥" % (ctx["my_handle"], game or "gaming")) if ctx.get("my_handle") else ""
    extra = ctx.get("extra_hashtags", "")
    yt_tags = _merge_tags(kit["hashtags"][:3], hashtags(game, streamer, "yt", extra))[:5]
    ig_tags = _merge_tags(kit["hashtags"], hashtags(game, streamer, "ig", extra))[:15]
    tt_tags = _merge_tags(kit["hashtags"][:3], hashtags(game, streamer, "tt", extra))[:6]

    yt_title = kit["yt_title"].strip()
    if "#shorts" not in yt_title.lower() and len(yt_title) + 8 <= YT_TITLE_MAX:
        yt_title += " #shorts"
    yt_title = yt_title[:YT_TITLE_MAX]

    yt_desc = "\n\n".join(x for x in [kit["yt_description"], credit, follow, " ".join(yt_tags)] if x)
    ig = "\n\n".join(x for x in [kit["ig_caption"], follow, credit, ".\n.\n" + " ".join(ig_tags)] if x)[:IG_MAX]
    tt = " ".join(x for x in [kit["tiktok_caption"], " ".join(tt_tags)] if x)
    if credit:
        tt += "\n" + credit
    return {
        "hook": kit["hook"],
        "writer": kit["writer"],
        "youtube": {"title": yt_title, "description": yt_desc},
        "instagram": {"caption": ig},
        "tiktok": {"caption": tt[:TIKTOK_MAX]},
    }


def kit_text(name, kit):
    """Plain-text version saved next to the reel."""
    return (
        "REEL: {name}\nOn-screen hook: {hook}\nWritten by: {writer}\n\n"
        "=== YOUTUBE SHORTS ===\nTitle:\n{yt_t}\n\nDescription:\n{yt_d}\n\n"
        "=== INSTAGRAM REELS ===\n{ig}\n\n"
        "=== TIKTOK ===\n{tt}\n"
    ).format(name=name, hook=kit["hook"], writer=kit["writer"], yt_t=kit["youtube"]["title"],
             yt_d=kit["youtube"]["description"], ig=kit["instagram"]["caption"], tt=kit["tiktok"]["caption"])
