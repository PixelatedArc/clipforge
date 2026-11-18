"""Turn a clip into a 1080x1920 reel: crop/blur layout, burned captions, optional dub."""
import json
import os
import re
import sys

from . import config, dub, transcript, translate

W, H = 1080, 1920

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


def _ts(sec):
    sec = max(0.0, sec)
    h = int(sec // 3600)
    m = int(sec % 3600 // 60)
    s = sec % 60
    return "%d:%02d:%05.2f" % (h, m, s)


def _esc(text):
    return text.replace("\\", "\\\\").replace("{", "(").replace("}", ")").replace("\n", " ")


def _ass_header(font, size, margin_v):
    # Colours are &HAABBGGRR. White text, thick black outline, soft shadow.
    return (
        "[Script Info]\nScriptType: v4.00+\nPlayResX: %d\nPlayResY: %d\nWrapStyle: 0\n"
        "ScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: Cap,%s,%d,&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,6,3,2,70,70,%d,1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    ) % (W, H, font, size, margin_v)


def captions_word_highlight(words, clip_start, clip_end, path, font, margin_v, upper=True):
    """Short chunks (<=3 words) with the word being spoken highlighted yellow."""
    lines = [_ass_header(font, 86, margin_v)]
    chunks, cur = [], []
    for w in words:
        if cur and (len(cur) >= 3 or w["t"] - cur[-1]["e"] > 0.6):
            chunks.append(cur)
            cur = []
        cur.append(w)
    if cur:
        chunks.append(cur)
    for ci, ch in enumerate(chunks):
        chunk_end = ch[-1]["e"]
        if ci + 1 < len(chunks):
            chunk_end = min(max(chunk_end, ch[-1]["e"]) + 0.25, chunks[ci + 1][0]["t"])
        for wi, w in enumerate(ch):
            a = w["t"]
            b = ch[wi + 1]["t"] if wi + 1 < len(ch) else chunk_end
            if b <= a:
                continue
            parts = []
            for k, x in enumerate(ch):
                t = _esc(x["w"].upper() if upper else x["w"])
                parts.append("{\\c&H00F0FF&}%s{\\c&HFFFFFF&}" % t if k == wi else t)
            lines.append("Dialogue: 0,%s,%s,Cap,,0,0,0,,%s\n" % (
                _ts(max(a, clip_start) - clip_start), _ts(min(b, clip_end) - clip_start), " ".join(parts)))
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(lines)


def captions_phrases(phrases, clip_start, clip_end, path, font, margin_v):
    """Phrase-level captions (used for translated text), split into readable pieces."""
    lines = [_ass_header(font, 74, margin_v)]
    for i, p in enumerate(phrases):
        a = max(p["t"], clip_start)
        b = min(phrases[i + 1]["t"] if i + 1 < len(phrases) else p["e"] + 0.4, p["e"] + 0.6, clip_end)
        if b <= a:
            continue
        toks = p["text"].split()
        n_pieces = max(1, (len(toks) + 5) // 6)
        per = (b - a) / n_pieces
        for k in range(n_pieces):
            piece = " ".join(toks[k * len(toks) // n_pieces:(k + 1) * len(toks) // n_pieces])
            if piece:
                lines.append("Dialogue: 0,%s,%s,Cap,,0,0,0,,%s\n" % (
                    _ts(a + k * per - clip_start), _ts(a + (k + 1) * per - clip_start), _esc(piece)))
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(lines)


def _video_filter(layout, ass_name):
    if layout == "center":
        v = "[0:v]scale=-2:%d,crop=%d:%d,setsar=1" % (H, W, H)
    else:  # blurred background
        v = ("[0:v]split=2[bgsrc][fgsrc];"
             "[bgsrc]scale=%d:%d:force_original_aspect_ratio=increase,crop=%d:%d,"
             "scale=270:480,boxblur=12:2,scale=%d:%d,eq=brightness=-0.08[bg];"
             "[fgsrc]scale=%d:%d:force_original_aspect_ratio=decrease[fg];"
             "[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1") % (W, H, W, H, W, H, W, H)
    if ass_name:
        v += ",ass=%s" % ass_name
    return v


def safe_name(s, n=60):
    s = re.sub(r"[^\w\- ]+", "", s, flags=re.UNICODE).strip().replace(" ", "_")
    return (s or "clip")[:n]


def render_clip(project, clip, opts, out_path, log):
    """
    project: dict with source, words, source_lang
    clip: {"start","end",...}
    opts: {
      layout: "blur"|"center",
      captions: "off"|"original"|"translated",
      caption_lang: "hi", ...
      dub_lang: None|"hi", dub_voice: "hi-IN-MadhurNeural", original_volume: 0.15,
      whisper_lang: None|"en", mymemory_email
    }
    """
    src = project["source"]
    start, end = float(clip["start"]), float(clip["end"])
    dur = end - start
    work = os.path.join(project["dir"], "render", "clip_%d_%d" % (int(start * 100), int(end * 100)))
    os.makedirs(work, exist_ok=True)
    src_lang = project.get("source_lang") or "en"

    need_words = opts["captions"] != "off" or opts.get("dub_lang")
    words = transcript.words_in(project.get("words") or [], start, end) if need_words else []
    if need_words and not words:
        cache = os.path.join(work, "whisper_words.json")
        if os.path.exists(cache):
            with open(cache, encoding="utf-8") as f:
                words, lang = json.load(f)
        else:
            log("No captions for this part - transcribing with Whisper (CPU, may take a minute)...")
            words, lang = transcript.whisper_words(src, start, dur, language=opts.get("whisper_lang"))
            with open(cache, "w", encoding="utf-8") as f:
                json.dump([words, lang], f, ensure_ascii=False)
        if lang and not project.get("source_lang"):
            src_lang = lang
    phrases = transcript.phrases(words)

    # Dubbing (translate -> speak -> mix)
    dub_wav = None
    dub_lang = opts.get("dub_lang")
    translated = None
    if dub_lang and phrases:
        if dub_lang.split("-")[0] == src_lang.split("-")[0]:
            log("Dub language equals the spoken language - skipping dub.")
            dub_lang = None
        else:
            log("Translating %d lines to %s for dub..." % (len(phrases), dub_lang))
            texts = translate.translate_lines([p["text"] for p in phrases], src_lang, dub_lang, log,
                                              opts.get("mymemory_email"))
            translated = [dict(p, text=t) for p, t in zip(phrases, texts)]
            log("Generating %s voice (%s)..." % (dub_lang, opts["dub_voice"]))
            dub_wav = dub.build_dub_track(translated, start, dur, opts["dub_voice"], os.path.join(work, "dub"), log)

    # Captions
    ass_name = None
    margin_v = 470 if opts.get("layout", "blur") == "blur" else 560
    if opts["captions"] != "off" and words:
        ass_name = "subs.ass"
        ass_path = os.path.join(work, ass_name)
        if dub_lang:  # when dubbed, captions always match what is being heard
            cap_lang = dub_lang
        elif opts["captions"] == "translated":
            cap_lang = opts.get("caption_lang") or src_lang
        else:
            cap_lang = src_lang
        if cap_lang.split("-")[0] == src_lang.split("-")[0]:
            font = FONT_FOR.get(src_lang.split("-")[0], DEFAULT_FONT)
            captions_word_highlight(words, start, end, ass_path, font, margin_v,
                                    upper=src_lang.split("-")[0] in ("en", "es", "pt", "fr", "de", "it", "id", "tr"))
        else:
            if translated is None or dub_lang != cap_lang:
                log("Translating captions to %s..." % cap_lang)
                texts = translate.translate_lines([p["text"] for p in phrases], src_lang, cap_lang, log,
                                                  opts.get("mymemory_email"))
                translated = [dict(p, text=t) for p, t in zip(phrases, texts)]
            font = FONT_FOR.get(cap_lang if cap_lang == "zh-CN" else cap_lang.split("-")[0], DEFAULT_FONT)
            captions_phrases(translated, start, end, ass_path, font, margin_v)

    # Render
    enc = config.video_encoder()
    vf = _video_filter(opts.get("layout", "blur"), ass_name)
    if enc == "h264_qsv":
        vf += ",format=nv12"
    vf += "[v]"
    cmd = [config.ffmpeg(), "-v", "error", "-y", "-ss", "%.3f" % start, "-t", "%.3f" % dur, "-i", src]
    if dub_wav:
        vol = float(opts.get("original_volume", 0.15))
        cmd += ["-i", dub_wav]
        af = "[0:a]aresample=48000,volume=%.2f[o];[1:a]volume=1.0[d];[o][d]amix=inputs=2:normalize=0:duration=first[a]" % vol
        graph = vf + ";" + af
        amap = "[a]"
    else:
        graph = vf
        amap = "0:a?"
    gpath = os.path.join(work, "graph.txt")
    with open(gpath, "w", encoding="utf-8") as f:
        f.write(graph)
    cmd += ["-/filter_complex", "graph.txt", "-map", "[v]", "-map", amap, "-r", "30"]
    cmd += config.encoder_args(enc)
    cmd += ["-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", out_path]
    log("Encoding with %s..." % enc)
    try:
        config.run(cmd, cwd=work)
    except RuntimeError:
        if enc == "h264_qsv":
            log("Intel encoder failed on this clip - retrying with software encoder.")
            cmd = [c for c in cmd]
            i = cmd.index("-c:v")
            cmd[i:i + 6] = config.encoder_args("libx264")
            with open(gpath, "w", encoding="utf-8") as f:
                f.write(graph.replace(",format=nv12[v]", "[v]"))
            config.run(cmd, cwd=work)
        else:
            raise
    return out_path
