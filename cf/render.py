"""Turn a clip into a finished reel: vertical layout, hook, animated captions, pacing effects,
an ending, mixed/normalized audio, and an upload kit."""
import json
import os
import re
import subprocess
import sys

import numpy as np

from . import config, dub, publish, transcript, translate

FORMATS = {"9:16": (1080, 1920), "4:5": (1080, 1350), "1:1": (1080, 1080)}
MUSIC_DIR = os.path.join(config.ROOT, "music")
os.makedirs(MUSIC_DIR, exist_ok=True)
MUSIC_EXT = (".mp3", ".m4a", ".wav", ".aac", ".ogg", ".flac")

_INDIC = ("hi", "mr", "bn", "pa", "ta", "te")
if sys.platform == "win32":
    FONT_FOR = {**{k: "Nirmala UI" for k in _INDIC}, "ur": "Segoe UI", "ar": "Segoe UI",
                "ja": "Yu Gothic UI", "ko": "Malgun Gothic", "zh-CN": "Microsoft YaHei UI"}
    DEFAULT_FONT = "Arial Black"
elif sys.platform == "darwin":
    FONT_FOR = {**{k: "Kohinoor Devanagari" for k in ("hi", "mr")}, "bn": "Kohinoor Bangla",
                "ta": "Tamil Sangam MN", "te": "Kohinoor Telugu", "pa": "Gurmukhi MN",
                "ur": "Geeza Pro", "ar": "Geeza Pro", "ja": "Hiragino Sans", "ko": "Apple SD Gothic Neo",
                "zh-CN": "PingFang SC"}
    DEFAULT_FONT = "Arial Black"
else:  # Linux: needs the fonts-noto package
    FONT_FOR = {**{k: "Noto Sans Devanagari" for k in ("hi", "mr")}, "bn": "Noto Sans Bengali",
                "ta": "Noto Sans Tamil", "te": "Noto Sans Telugu", "pa": "Noto Sans Gurmukhi",
                "ur": "Noto Sans Arabic", "ar": "Noto Sans Arabic", "ja": "Noto Sans CJK JP",
                "ko": "Noto Sans CJK KR", "zh-CN": "Noto Sans CJK SC"}
    DEFAULT_FONT = "DejaVu Sans"

# Caption looks. Inline ASS colours are &HBBGGRR&.
CAPTION_STYLES = {
    "yellow": {"hl": "&H00E6FF&", "upper": True, "pop": True, "words": 3},
    "green": {"hl": "&H35FF7C&", "upper": True, "pop": True, "words": 3},
    "clean": {"hl": None, "upper": False, "pop": False, "words": 6},
}
_LATIN_UPPER = ("en", "es", "pt", "fr", "de", "it", "id", "tr")


def list_music():
    try:
        return sorted(f for f in os.listdir(MUSIC_DIR) if f.lower().endswith(MUSIC_EXT))
    except OSError:
        return []


def _even(x):
    return max(2, int(round(x / 2.0)) * 2)


def _clamp(v, lo=0.0, hi=1.0):
    try:
        return max(lo, min(hi, float(v)))
    except (TypeError, ValueError):
        return (lo + hi) / 2


def _ts(sec):
    sec = max(0.0, sec)
    return "%d:%02d:%05.2f" % (int(sec // 3600), int(sec % 3600 // 60), sec % 60)


def _esc(text):
    return text.replace("\\", "\\\\").replace("{", "(").replace("}", ")").replace("\n", " ")


def safe_name(s, n=60):
    s = re.sub(r"[^\w\- ]+", "", s, flags=re.UNICODE).strip().replace(" ", "_")
    return (s or "clip")[:n]


# ------------------------------------------------------------------ ASS (all on-screen text)
def _style(name, font, size, primary="&H00FFFFFF", outline="&H00000000", back="&H90000000",
           border=1, ow=6, sh=3, ml=70, mr=70):
    return "Style: %s,%s,%d,%s,%s,%s,%s,-1,0,0,0,100,100,0,0,%d,%s,%s,5,%d,%d,0,1\n" % (
        name, font, size, primary, primary, outline, back, border, ow, sh, ml, mr)


def _ass_doc(Wd, Hd, styles, events):
    return ("[Script Info]\nScriptType: v4.00+\nPlayResX: %d\nPlayResY: %d\nWrapStyle: 0\n"
            "ScaledBorderAndShadow: yes\n\n[V4+ Styles]\n"
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
            "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
            "Alignment, MarginL, MarginR, MarginV, Encoding\n%s\n[Events]\n"
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n%s") % (
        Wd, Hd, "".join(styles), "".join(events))


def _dlg(layer, a, b, style, text):
    return "Dialogue: %d,%s,%s,%s,,0,0,0,,%s\n" % (layer, _ts(a), _ts(b), style, text)


def word_caption_events(words, x, y, cfg, upper, D):
    """Short chunks; the word being spoken is highlighted (and pops)."""
    ev, chunks, cur = [], [], []
    for w in words:
        if cur and (len(cur) >= cfg["words"] or w["t"] - cur[-1]["e"] > 0.6):
            chunks.append(cur)
            cur = []
        cur.append(w)
    if cur:
        chunks.append(cur)
    pos = "{\\an5\\pos(%d,%d)}" % (x, y)
    for ci, ch in enumerate(chunks):
        end = ch[-1]["e"] + 0.25
        if ci + 1 < len(chunks):
            end = min(end, chunks[ci + 1][0]["t"])
        toks = [_esc(t["w"].upper() if upper else t["w"]) for t in ch]
        if not cfg["hl"]:
            ev.append(_dlg(0, ch[0]["t"], min(end, D), "Cap", pos + " ".join(toks)))
            continue
        for wi, w in enumerate(ch):
            a = w["t"]
            b = ch[wi + 1]["t"] if wi + 1 < len(ch) else end
            if b <= a:
                continue
            pop = "{\\fscx80\\fscy80\\t(0,90,\\fscx100\\fscy100)}" if cfg["pop"] and wi == 0 else ""
            parts = ["{\\c%s\\fscx110\\fscy110}%s{\\c&HFFFFFF&\\fscx100\\fscy100}" % (cfg["hl"], t) if k == wi else t
                     for k, t in enumerate(toks)]
            ev.append(_dlg(0, a, min(b, D), "Cap", pos + pop + " ".join(parts)))
    return ev


def phrase_caption_events(phrases, x, y, cfg, upper, D):
    """Translated text has no word timings: show it in short pieces spread over each phrase."""
    ev = []
    pos = "{\\an5\\pos(%d,%d)}" % (x, y)
    for i, p in enumerate(phrases):
        a = p["t"]
        b = min(phrases[i + 1]["t"] if i + 1 < len(phrases) else p["e"] + 0.4, p["e"] + 0.6, D)
        toks = p["text"].split()
        if b <= a or not toks:
            continue
        per_piece = max(2, cfg["words"] + 1)
        n = max(1, (len(toks) + per_piece - 1) // per_piece)
        step = (b - a) / n
        for k in range(n):
            piece = " ".join(toks[k * len(toks) // n:(k + 1) * len(toks) // n])
            if piece:
                pop = "{\\fscx80\\fscy80\\t(0,90,\\fscx100\\fscy100)}" if cfg["pop"] else ""
                ev.append(_dlg(0, a + k * step, a + (k + 1) * step, "Cap", pos + pop + _esc(piece.upper() if upper else piece)))
    return ev


def build_ass(Wd, Hd, D, pos, cap_font, cap_events_fn, hook, hook_end, brand, cta, animate=True):
    scale = Hd / 1920.0 if Hd >= 1350 else 0.8
    styles = [
        _style("Cap", cap_font, int(92 * scale), ow=7, sh=4),
        _style("Hook", DEFAULT_FONT, int(76 * scale), outline="&H002F12E3", back="&H00000000", border=3, ow=18, sh=0, ml=90, mr=90),
        _style("Cta", DEFAULT_FONT, int(84 * scale), primary="&H00111111", outline="&H0000D7FF", border=3, ow=20, sh=0),
        _style("Sub", DEFAULT_FONT, int(46 * scale), ow=4, sh=2),
        _style("Brand", DEFAULT_FONT, int(36 * scale), primary="&H30FFFFFF", outline="&H60000000", ow=3, sh=0),
    ]
    ev = list(cap_events_fn()) if cap_events_fn else []
    cx = Wd // 2
    if hook:
        anim = "\\fscx40\\fscy40\\t(0,140,\\fscx108\\fscy108)\\t(140,230,\\fscx100\\fscy100)\\fad(0,250)" if animate else ""
        ev.append(_dlg(3, 0, hook_end, "Hook", "{\\an5\\pos(%d,%d)%s}%s" % (cx, pos["hook_y"], anim, _esc(hook))))
    if brand:
        ev.append(_dlg(1, 0, D, "Brand", "{\\an5\\pos(%d,%d)}%s" % (cx, pos["brand_y"], _esc(brand))))
    if cta:
        a = max(0.0, D - 1.9)
        anim = "\\fscx40\\fscy40\\t(0,150,\\fscx110\\fscy110)\\t(150,240,\\fscx100\\fscy100)" if animate else ""
        ev.append(_dlg(4, a, D, "Cta", "{\\an5\\pos(%d,%d)%s}%s" % (cx, pos["cta_y"], anim, _esc(cta[0]))))
        if cta[1]:
            ev.append(_dlg(4, a + 0.15, D, "Sub", "{\\an5\\pos(%d,%d)\\fad(150,0)}%s" % (cx, pos["cta_y"] + int(120 * scale), _esc(cta[1]))))
    return _ass_doc(Wd, Hd, styles, ev)


# ------------------------------------------------------------------ layouts
def layout_filter(layout, Wd, Hd, sw, sh, crop, sharpen, inp, out):
    """Return (filtergraph from [inp] to [out], text positions) for a layout."""
    crop = crop or {}
    fx = _clamp(crop.get("focus_x", 0.5))
    S = ":flags=lanczos"
    sharp = ",unsharp=5:5:0.6" if sharpen else ""

    def cover(w, h, x=0.5, y=0.5):
        return ("scale=%d:%d:force_original_aspect_ratio=increase%s,crop=%d:%d:x='(iw-ow)*%.3f':y='(ih-oh)*%.3f'"
                % (w, h, S, w, h, x, y))

    def blur_bg(a, b):
        bw, bh = _even(Wd / 4), _even(Hd / 4)
        return ("[%s]scale=%d:%d:force_original_aspect_ratio=increase,crop=%d:%d,boxblur=10:2,scale=%d:%d,"
                "eq=brightness=-0.12:saturation=1.25[%s]") % (a, bw, bh, bw, bh, Wd, Hd, b)

    pos = {"hook_y": int(Hd * 0.18), "brand_y": int(Hd * 0.105), "cta_y": int(Hd * 0.44)}
    face = crop.get("face")
    if layout == "split" and not face:
        layout = "fill"
    if layout == "zoom" and Hd <= Wd * 1.1:
        layout = "fill"

    if layout == "split":
        top_h = _even(Hd * _clamp(crop.get("split", 0.38), 0.25, 0.55))
        bot_h = Hd - top_h
        fxp, fyp = int(sw * _clamp(face["x"])), int(sh * _clamp(face["y"]))
        fw = max(16, min(_even(sw * _clamp(face["w"])), (sw - fxp) // 2 * 2))
        fh = max(16, min(_even(sh * _clamp(face["h"])), (sh - fyp) // 2 * 2))
        g = ("[%s]split=2[t0][g0];[t0]crop=%d:%d:%d:%d,%s%s[top];[g0]%s%s[bot];"
             "[top][bot]vstack=2,drawbox=x=0:y=%d:w=iw:h=6:color=0xFFD000:t=fill,setsar=1[%s]") % (
            inp, fw, fh, fxp, fyp, cover(Wd, top_h), sharp, cover(Wd, bot_h, fx), sharp, top_h - 3, out)
        # hook sits on the gameplay (never over the streamer's face), just under the seam captions
        pos.update(cap_y=top_h, hook_y=top_h + int(bot_h * 0.17), cta_y=top_h + int(bot_h * 0.36))
        return g, pos, "split"

    if layout == "fill":
        g = "[%s]%s%s,setsar=1[%s]" % (inp, cover(Wd, Hd, fx), sharp, out)
        pos.update(cap_y=int(Hd * 0.66))
        return g, pos, "fill"

    if layout == "zoom":  # square crop of the action, big in the middle, blurred background
        box = Wd
        y0 = (Hd - box) // 2
        g = "[%s]split=2[b0][f0];%s;[f0]%s%s[fg];[bg][fg]overlay=0:%d,setsar=1[%s]" % (
            inp, blur_bg("b0", "bg"), cover(box, box, fx), sharp, y0, out)
        pos.update(cap_y=min(int(Hd * 0.76), y0 + box + int(Hd * 0.05)))
        return g, pos, "zoom"

    # blur: the whole frame, letterboxed on a blurred copy
    if sw / float(sh) >= Wd / float(Hd):
        fw, fh = Wd, _even(Wd * sh / float(sw))
    else:
        fw, fh = _even(Hd * sw / float(sh)), Hd
    y0 = (Hd - fh) // 2
    g = "[%s]split=2[b0][f0];%s;[f0]scale=%d:%d%s%s[fg];[bg][fg]overlay=(W-w)/2:%d,setsar=1[%s]" % (
        inp, blur_bg("b0", "bg"), fw, fh, S, sharp, y0, out)
    pos.update(cap_y=min(int(Hd * 0.74), y0 + fh + int(Hd * 0.06)))
    return g, pos, "blur"


# ------------------------------------------------------------------ timeline (dead-air cuts, teaser)
def rms_db(src, start, dur):
    """Loudness every 0.1 s (dB)."""
    a = transcript.load_audio(src, start, dur, sr=16000)
    n = len(a) // 1600
    if n == 0:
        return np.zeros(0)
    fr = a[:n * 1600].reshape(n, 1600)
    return 20 * np.log10(np.sqrt((fr * fr).mean(1)) + 1e-6)


def plan_timeline(start, end, words, db, cut_dead_air, teaser):
    dur = end - start
    med = float(np.median(db)) if len(db) else -60.0
    main = [(start, end)]
    if cut_dead_air and len(words) >= 2:
        seq = [(start, start)] + [(w["t"], w["e"]) for w in words] + [(end, end)]
        cuts = []
        for (_, a1), (b0, _) in zip(seq, seq[1:]):
            g0, g1 = max(start, a1 + 0.25), min(end, b0 - 0.25)
            if g1 - g0 >= 0.6:
                seg = db[int((g0 - start) * 10):int((g1 - start) * 10)]
                if len(seg) and seg.max() > med + 8:  # something loud on screen - keep it
                    continue
                cuts.append((g0, g1))
        budget, chosen = 0.45 * dur, []
        for c in sorted(cuts, key=lambda c: c[0] - c[1]):  # biggest gaps first
            if budget - (c[1] - c[0]) >= 0:
                budget -= c[1] - c[0]
                chosen.append(c)
        main, t = [], start
        for a, b in sorted(chosen):
            if a - t >= 0.3:
                main.append((t, a))
            t = b
        if end - t >= 0.3:
            main.append((t, end))
        main = main or [(start, end)]
    tz = None
    if teaser and len(db) > 20:
        sm = np.convolve(db, np.ones(5) / 5, "same")
        pk = start + int(np.argmax(sm)) / 10.0
        if pk - main[0][0] > 5 and end - pk > 1.2:
            a = max(start, pk - 0.8)
            tz = (a, min(end, a + 1.8))
    T = (tz[1] - tz[0]) if tz else 0.0
    mapping, o = [], T
    for a, b in main:
        mapping.append((a, b, o))
        o += b - a
    return ([tz] if tz else []) + main, T, mapping, o


def remap_words(words, mapping):
    out = []
    for w in words:
        for a, b, o in mapping:
            if a - 0.05 <= w["t"] < b:
                t = o + max(0.0, w["t"] - a)
                out.append(dict(w, t=t, e=max(t + 0.08, o + min(w["e"], b) - a)))
                break
    return out


def punch_times(db, start, mapping, T, D, n=3):
    if len(db) < 30:
        return []
    med = float(np.median(db))
    cands = []
    for i in range(len(db)):
        lo, hi = max(0, i - 15), min(len(db), i + 16)
        if db[i] >= db[lo:hi].max() and db[i] > med + 9:
            cands.append((db[i], start + i / 10.0))
    out = []
    for _, t in sorted(cands, reverse=True):
        for a, b, o in mapping:
            if a <= t < b:
                ot = o + t - a
                if T + 1.0 < ot < D - 1.2 and all(abs(ot - x) > 3 for x in out):
                    out.append(ot)
                break
        if len(out) >= n:
            break
    return sorted(out)


# ------------------------------------------------------------------ words for a clip
def _clip_words(project, src, start, dur, opts, work, log, task="transcribe"):
    if task == "transcribe":
        words = transcript.words_in(project.get("words") or [], start, start + dur)
        if words:
            return words, project.get("source_lang")
    size = opts.get("whisper_size") or "base"
    name = "whisper_words.json" if (size == "base" and task == "transcribe") else "whisper_%s_%s.json" % (size, task)
    cache = os.path.join(work, name)
    if os.path.exists(cache):
        with open(cache, encoding="utf-8") as f:
            words, lang = json.load(f)
        return transcript.clean_words(words), lang
    log("%s with Whisper (%s, CPU)..." % ("Writing English captions from the speech" if task == "translate"
                                          else "Transcribing this part", size))
    words, lang = transcript.whisper_words(src, start, dur, language=opts.get("whisper_lang"), size=size, task=task)
    with open(cache, "w", encoding="utf-8") as f:
        json.dump([words, lang], f, ensure_ascii=False)
    return words, lang


def probe(src):
    r = config.run([config.ffprobe(), "-v", "error", "-show_entries", "stream=codec_type,width,height",
                    "-of", "json", src])
    st = json.loads(r.stdout).get("streams", [])
    v = next((s for s in st if s.get("codec_type") == "video"), {})
    return {"w": int(v.get("width") or 1920), "h": int(v.get("height") or 1080),
            "audio": any(s.get("codec_type") == "audio" for s in st)}


def _brand(ctx):
    bits = []
    if ctx.get("show_handle") and ctx.get("my_handle"):
        bits.append(ctx["my_handle"])
    if ctx.get("show_credit") and (ctx.get("streamer") or ctx.get("channel")):
        bits.append("credit: " + (ctx.get("streamer") or ctx.get("channel")))
    return "   |   ".join(bits)


# ------------------------------------------------------------------ main
def render_clip(project, clip, opts, out_path, log):
    """
    project: source, words, source_lang, dir, info{w,h,audio}, crop{focus_x, face, split}, ctx{...kit info}
    opts: format, layout, caption_style, captions(off|original|translated), caption_lang, hook_mode(auto|custom|off),
          hook_text, teaser, intro, cut_dead_air, punch_zoom, progress, ending(cta|fade|loop), normalize, whoosh,
          music, music_volume, dub_lang, dub_voice, original_volume, whisper_lang, whisper_size, mymemory_email
    Returns {"kit": ..., "duration": D}
    """
    src = project["source"]
    Wd, Hd = FORMATS.get(opts.get("format") or "9:16", FORMATS["9:16"])
    start, end = float(clip["start"]), float(clip["end"])
    dur = end - start
    work = os.path.join(project["dir"], "render", "clip_%d_%d" % (int(start * 100), int(end * 100)))
    os.makedirs(work, exist_ok=True)
    info = project.get("info") or probe(src)
    ctx = dict(project.get("ctx") or {})

    # --- words (source language) and, when useful, English straight from the speech
    words, lang = _clip_words(project, src, start, dur, opts, work, log)
    src_lang = (project.get("source_lang") or lang or "en")
    base = src_lang.split("-")[0]
    cap_mode = opts.get("captions", "original")
    dub_lang = opts.get("dub_lang") or None
    if dub_lang and dub_lang.split("-")[0] == base:
        log("Dub language equals the spoken language - skipping dub.")
        dub_lang = None
    target = None if cap_mode == "off" else (dub_lang or (opts.get("caption_lang") if cap_mode == "translated" else src_lang))
    want_en = base != "en" and words and (target == "en" or dub_lang == "en" or ctx.get("ai_on"))
    en_words = _clip_words(project, src, start, dur, opts, work, log, task="translate")[0] if want_en else []
    if base == "en":
        en_words = words

    # --- timeline
    db = rms_db(src, start, dur)
    segments, T, mapping, D = plan_timeline(start, end, words if len(words) >= len(en_words) / 2 else en_words,
                                            db, opts.get("cut_dead_air"), opts.get("teaser"))
    if len(mapping) > 1:
        log("Cut %.1fs of dead air (%d jump cuts)." % (dur - (D - T), len(mapping) - 1))
    if T:
        log("Teaser: opening with the loudest moment (%.1fs)." % T)
    w_out, en_out = remap_words(words, mapping), remap_words(en_words, mapping)

    # --- upload kit (+ hook text)
    ctx.update(clip_id=clip.get("id"), reasons=clip.get("reasons"),
               top_comment=(clip.get("top_comments") or [{}])[0].get("text") if clip.get("top_comments") else "",
               text=" ".join(w["w"] for w in words), text_en=" ".join(w["w"] for w in en_words))
    kit = publish.make_kit(ctx, log)
    hook = None
    if opts.get("hook_mode", "auto") != "off":
        hook = (opts.get("hook_text") or "").strip() or kit["hook"]
        kit["hook"] = hook

    # --- dub + captions text
    translated = None
    dub_wav = None
    if dub_lang and w_out:
        if dub_lang == "en" and en_out:
            translated = transcript.phrases(en_out)
        else:
            phr = transcript.phrases(w_out)
            log("Translating %d lines to %s for the dub..." % (len(phr), dub_lang))
            texts = translate.translate_lines([p["text"] for p in phr], src_lang, dub_lang, log, opts.get("mymemory_email"))
            translated = [dict(p, text=t) for p, t in zip(phr, texts)]
        log("Generating %s voice (%s)..." % (dub_lang, opts["dub_voice"]))
        dub_wav = dub.build_dub_track(translated, 0.0, D, opts["dub_voice"], os.path.join(work, "dub"), log)

    vf_layout, pos, used_layout = layout_filter(opts.get("layout", "split"), Wd, Hd, info["w"], info["h"],
                                                project.get("crop"), info["h"] < 720, "cvf", "lv")
    if used_layout != opts.get("layout", "split"):
        log("Layout '%s' not possible here (%s) - using '%s'." % (
            opts.get("layout"), "mark the facecam first" if opts.get("layout") == "split" else "format", used_layout))
    cfg = CAPTION_STYLES.get(opts.get("caption_style") or "yellow", CAPTION_STYLES["yellow"])
    cap_fn, cap_font = None, DEFAULT_FONT
    if target and w_out:
        tb = target.split("-")[0]
        cap_font = FONT_FOR.get(target if target == "zh-CN" else tb, DEFAULT_FONT)
        upper = cfg["upper"] and tb in _LATIN_UPPER
        if tb == base:
            cap_fn = lambda: word_caption_events(w_out, Wd // 2, pos["cap_y"], cfg, upper, D)  # noqa: E731
        elif tb == "en" and en_out:
            cap_fn = lambda: word_caption_events(en_out, Wd // 2, pos["cap_y"], cfg, upper, D)  # noqa: E731
        else:
            if translated is None or dub_lang != target:
                phr = transcript.phrases(w_out)
                log("Translating captions to %s..." % target)
                texts = translate.translate_lines([p["text"] for p in phr], src_lang, target, log, opts.get("mymemory_email"))
                translated = [dict(p, text=t) for p, t in zip(phr, texts)]
            tr = translated
            cap_fn = lambda: phrase_caption_events(tr, Wd // 2, pos["cap_y"], cfg, upper, D)  # noqa: E731

    ending = opts.get("ending", "cta")
    cta = None
    if ending == "cta":
        cta = ("FOLLOW FOR MORE", ctx.get("my_handle") or "")
    ass = build_ass(Wd, Hd, D, pos, cap_font, cap_fn, hook, max(2.6, T + 0.9) if hook else 0, _brand(ctx), cta)
    with open(os.path.join(work, "subs.ass"), "w", encoding="utf-8") as f:
        f.write(ass)

    # --- inputs
    cmd = [config.ffmpeg(), "-v", "error", "-y"]
    for a, b in segments:
        cmd += ["-ss", "%.3f" % a, "-t", "%.3f" % (b - a), "-i", src]
    n_seg = len(segments)
    idx = n_seg
    parts = []
    for i in range(n_seg):
        parts.append("[%d:v]setpts=PTS-STARTPTS[v%d]" % (i, i))
        if info["audio"]:
            parts.append("[%d:a]asetpts=PTS-STARTPTS,aresample=48000,aformat=channel_layouts=stereo[a%d]" % (i, i))
    if info["audio"]:
        parts.append("".join("[v%d][a%d]" % (i, i) for i in range(n_seg)) + "concat=n=%d:v=1:a=1[cv][ca]" % n_seg)
    else:
        parts.append("".join("[v%d]" % i for i in range(n_seg)) + "concat=n=%d:v=1:a=0[cv]" % n_seg)
        cmd += ["-f", "lavfi", "-t", "%.3f" % D, "-i", "anullsrc=r=48000:cl=stereo"]
        parts.append("[%d:a]anull[ca]" % idx)
        idx += 1
    parts.append("[cv]fps=30,format=yuv420p[cvf]")
    parts.append(vf_layout)

    # --- video effects
    zt = []
    if opts.get("intro", True):
        zt.append("0.12*clip(1-t/0.35,0,1)")
    if T:
        zt.append("0.12*clip(1-(t-%.3f)/0.35,0,1)*gte(t,%.3f)" % (T, T))
    if opts.get("punch_zoom", True):
        for p in punch_times(db, start, mapping, T, D):
            zt.append("0.10*(clip((t-%.3f)/0.10,0,1)-clip((t-%.3f)/0.30,0,1))" % (p, p + 0.8))
    if len(mapping) > 1:  # alternate framing on jump cuts so they feel intentional
        for k, (a, b, o) in enumerate(mapping):
            if k % 2:
                zt.append("0.06*between(t,%.3f,%.3f)" % (o, o + b - a))
    vf = []
    if zt:
        vf.append("scale=w='trunc(%d*(1+%s)/2)*2':h=-2:eval=frame:flags=bicubic,crop=%d:%d" % (Wd, "+".join(zt), Wd, Hd))
    if opts.get("intro", True):
        vf.append("fade=t=in:st=0:d=0.2:color=white")
    if T:
        vf.append("drawbox=x=0:y=0:w=iw:h=ih:color=white@0.8:t=fill:enable='between(t,%.3f,%.3f)'" % (T, T + 0.07))
    if ending == "fade":
        vf.append("fade=t=out:st=%.3f:d=0.5" % max(0.0, D - 0.5))
    if opts.get("progress", True):
        vf.append("drawbox=x=0:y=0:w='max(2,iw*t/%.3f)':h=%d:color=0xFFD000@0.95:t=fill" % (D, max(6, Hd // 190)))
    vf.append("ass=subs.ass")
    enc = config.video_encoder()
    if enc == "h264_qsv":
        vf.append("format=nv12")
    parts.append("[lv]" + ",".join(vf) + "[v]")

    # --- audio
    cur = "ca"
    if dub_wav:
        cmd += ["-i", dub_wav]
        parts.append("[ca]volume=%.2f[o];[%d:a]aresample=48000,aformat=channel_layouts=stereo[d];"
                     "[o][d]amix=inputs=2:normalize=0:duration=first[vx]" % (float(opts.get("original_volume", 0.15)), idx))
        idx += 1
        cur = "vx"
    if opts.get("normalize", True):
        parts.append("[%s]loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000,aformat=channel_layouts=stereo[vn]" % cur)
        cur = "vn"
    mix = [cur]
    music = opts.get("music")
    if music and os.path.isfile(os.path.join(MUSIC_DIR, music)):
        cmd += ["-stream_loop", "-1", "-i", os.path.join(MUSIC_DIR, music)]
        parts.append("[%s]asplit=2[vm][vsc];[%d:a]aresample=48000,aformat=channel_layouts=stereo,volume=%.2f[mu];"
                     "[mu][vsc]sidechaincompress=threshold=0.03:ratio=8:attack=20:release=400[duck]"
                     % (cur, idx, _clamp(opts.get("music_volume", 0.18), 0, 1)))
        idx += 1
        mix = ["vm", "duck"]
    wt = []
    if opts.get("whoosh", True):
        if opts.get("intro", True):
            wt.append(0.0)
        if T:
            wt.append(max(0.0, T - 0.3))
    if wt:
        cmd += ["-f", "lavfi", "-t", "0.6", "-i", "anoisesrc=color=pink:amplitude=0.5:duration=0.6"]
        labels = "".join("[w%d]" % i for i in range(len(wt)))
        parts.append("[%d:a]highpass=f=400,lowpass=f=6000,afade=t=in:d=0.2,afade=t=out:st=0.2:d=0.4,volume=0.55,"
                     "aresample=48000,aformat=channel_layouts=stereo,asplit=%d%s" % (idx, len(wt), labels))
        for i, t in enumerate(wt):
            parts.append("[w%d]adelay=%d:all=1[wd%d]" % (i, int(t * 1000), i))
            mix.append("wd%d" % i)
        idx += 1
    if len(mix) > 1:
        parts.append("".join("[%s]" % m for m in mix) + "amix=inputs=%d:normalize=0:duration=first[mx]" % len(mix))
        cur = "mx"
    else:
        cur = mix[0]
    fo = 0.5 if ending == "fade" else 0.25
    parts.append("[%s]atrim=0:%.3f,afade=t=in:d=0.03,afade=t=out:st=%.3f:d=%.2f,alimiter=limit=0.95[a]"
                 % (cur, D, max(0.0, D - fo), fo))

    graph = ";\n".join(parts)
    with open(os.path.join(work, "graph.txt"), "w", encoding="utf-8") as f:
        f.write(graph)
    cmd += ["-/filter_complex", "graph.txt", "-map", "[v]", "-map", "[a]", "-t", "%.3f" % D, "-r", "30"]
    cmd += config.encoder_args(enc)
    cmd += ["-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart", out_path]
    log("Encoding %dx%d, %s layout, %.1fs (%s)..." % (Wd, Hd, used_layout, D, enc))
    try:
        config.run(cmd, cwd=work)
    except RuntimeError:
        if enc != "h264_qsv":
            raise
        log("Intel encoder failed on this clip - retrying with software encoder.")
        i = cmd.index("-c:v")
        cmd[i:i + 6] = config.encoder_args("libx264")
        with open(os.path.join(work, "graph.txt"), "w", encoding="utf-8") as f:
            f.write(graph.replace(",format=nv12[v]", "[v]"))
        config.run(cmd, cwd=work)
    return {"kit": kit, "duration": round(D, 2), "layout": used_layout}


# ------------------------------------------------------------------ quick preview frame
def preview_frame(project, opts, t, out_jpg):
    """One still frame of the chosen format/layout with sample text, for the phone preview."""
    src = project["source"]
    Wd, Hd = FORMATS.get(opts.get("format") or "9:16", FORMATS["9:16"])
    info = project.get("info") or probe(src)
    work = os.path.join(project["dir"], "render", "preview")
    os.makedirs(work, exist_ok=True)
    g, pos, used = layout_filter(opts.get("layout", "split"), Wd, Hd, info["w"], info["h"],
                                 project.get("crop"), info["h"] < 720, "cvf", "lv")
    cfg = CAPTION_STYLES.get(opts.get("caption_style") or "yellow", CAPTION_STYLES["yellow"])
    cap_fn = None
    if opts.get("captions", "original") != "off":
        cap_fn = lambda: [_dlg(0, 0, 9, "Cap", "{\\an5\\pos(%d,%d)}THIS %s %s" % (  # noqa: E731
            Wd // 2, pos["cap_y"], ("{\\c%s}IS{\\c&HFFFFFF&}" % cfg["hl"]) if cfg["hl"] else "IS", "INSANE"))]
    hook = None if opts.get("hook_mode") == "off" else ((opts.get("hook_text") or "").strip() or "YOUR HOOK TEXT HERE")
    ass = build_ass(Wd, Hd, 9, pos, DEFAULT_FONT, cap_fn, hook, 9, _brand(project.get("ctx") or {}), None, animate=False)
    with open(os.path.join(work, "subs.ass"), "w", encoding="utf-8") as f:
        f.write(ass)
    graph = "[0:v]format=yuv420p[cvf];%s;[lv]ass=subs.ass[v]" % g
    with open(os.path.join(work, "graph.txt"), "w", encoding="utf-8") as f:
        f.write(graph)
    r = subprocess.run([config.ffmpeg(), "-v", "error", "-y", "-ss", "%.2f" % t, "-i", src, "-/filter_complex", "graph.txt",
                        "-map", "[v]", "-frames:v", "1", "-q:v", "3", out_jpg],
                       cwd=work, capture_output=True, text=True, creationflags=config.NO_WINDOW)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or "preview failed").strip().splitlines()[-1])
    return used
