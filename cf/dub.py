"""AI dubbing with free Microsoft Edge neural voices (edge-tts).

Each translated phrase is spoken, sped up if it is longer than the original phrase's
time slot, then placed at the phrase's start time. The original audio is kept
underneath at a low volume so game sounds/music survive.
"""
import asyncio
import os

import edge_tts
from mutagen.mp3 import MP3

from . import config, control

_voices_cache = None


def list_voices():
    global _voices_cache
    if _voices_cache is None:
        vs = asyncio.run(edge_tts.list_voices())
        _voices_cache = [{"name": v["ShortName"], "locale": v["Locale"], "gender": v["Gender"]} for v in vs]
    return _voices_cache


def voices_for(lang):
    base = lang.split("-")[0].lower()
    if lang == "zh-CN":
        return [v for v in list_voices() if v["locale"] == "zh-CN"]
    return [v for v in list_voices() if v["locale"].split("-")[0].lower() == base]


async def _say(text, voice, rate_pct, path):
    rate = "%+d%%" % rate_pct
    await edge_tts.Communicate(text, voice, rate=rate).save(path)


def _duration(path):
    return MP3(path).info.length


def synth_phrase(text, voice, slot, path):
    """Speak `text`; if it overruns `slot` seconds, re-speak faster (max +60%), then atempo."""
    asyncio.run(_say(text, voice, 0, path))
    d = _duration(path)
    if d > slot and slot > 0.3:
        rate = min(60, int((d / slot - 1) * 100) + 5)
        asyncio.run(_say(text, voice, rate, path))
        d = _duration(path)
    tempo = 1.0
    if d > slot * 1.05 and slot > 0.3:
        tempo = min(1.35, d / slot)  # gentle extra squeeze; beyond this it sounds robotic
    return d / tempo, tempo


def build_dub_track(phrases, clip_start, clip_dur, voice, work_dir, log):
    """phrases: [{"t","e","text"}] in absolute seconds, text already translated.
    Returns path to a WAV the length of the clip, or None."""
    os.makedirs(work_dir, exist_ok=True)
    parts = []
    for i, p in enumerate(phrases):
        text = p["text"].strip()
        if not text:
            continue
        t = max(0.0, p["t"] - clip_start)
        if t >= clip_dur:
            continue
        nxt = phrases[i + 1]["t"] - clip_start if i + 1 < len(phrases) else clip_dur
        slot = max(0.4, min(nxt, clip_dur) - t)
        path = os.path.join(work_dir, "tts_%03d.mp3" % i)
        control.checkpoint()
        try:
            _, tempo = synth_phrase(text, voice, slot, path)
        except Exception as e:
            raise RuntimeError("Voice generation failed (edge-tts): %s" % str(e)[:200])
        parts.append((path, t, tempo))
    if not parts:
        return None
    out = os.path.join(work_dir, "dub.wav")
    cmd = [config.ffmpeg(), "-v", "error", "-y"]
    for path, _, _ in parts:
        cmd += ["-i", path]
    chains, labels = [], []
    for k, (_, t, tempo) in enumerate(parts):
        f = "[%d:a]aresample=48000,aformat=channel_layouts=stereo" % k
        if tempo > 1.001:
            f += ",atempo=%.3f" % tempo
        f += ",adelay=%d:all=1[d%d]" % (int(t * 1000), k)
        chains.append(f)
        labels.append("[d%d]" % k)
    graph = ";".join(chains) + ";" + "".join(labels) + \
        "amix=inputs=%d:normalize=0:dropout_transition=0,apad,atrim=0:%.3f[out]" % (len(parts), clip_dur)
    # Long graphs can exceed the Windows command-line limit -> pass via file.
    gpath = os.path.join(work_dir, "dub_graph.txt")
    with open(gpath, "w", encoding="utf-8") as f:
        f.write(graph)
    cmd += ["-/filter_complex", gpath, "-map", "[out]", "-ar", "48000", out]
    config.run(cmd)
    return out
