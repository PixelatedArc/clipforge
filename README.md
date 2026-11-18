# ClipForge ✂

**A free, local YouTube-to-Shorts clipper.** Paste a YouTube link (or attach a video file) and ClipForge
finds the best moments, then exports vertical 1080×1920 reels with captions. It can also translate
the subtitles and add an AI voice dub.

Everything runs on your own computer. There are no subscriptions, watermarks or uploads to a server.

## Features
- **Smart clip picking:** each moment is scored by YouTube's *Most replayed* graph, timestamp comments
  (`12:34 bro 😂`), loud reactions in the audio, and hype words in the transcript. Each clip shows *why* it was picked.
- **Preview and trim** in the browser, with a clickable "hype graph" of the whole video.
- **Vertical export:** blurred-background or center-crop layout.
- **Captions:** word-by-word highlighted captions, or subtitles translated into 20 languages.
- **AI dub:** translates the speech and voices it with free neural voices (e.g. English → Hindi),
  with the original audio kept underneath.
- **Phone mode:** control it from your phone over Wi-Fi while your PC does the work.

## Install

### Windows (tested)
1. Install [Python 3.13](https://www.python.org/downloads/). Python 3.14+ is too new for some of the libraries.
2. Download this repo (green **Code** button → *Download ZIP*, then extract it) or `git clone` it.
3. Double-click **`setup.bat`**. It installs everything, including ffmpeg and Node.js via winget if they're missing.
4. Double-click **`start.bat`**. Your browser opens at http://127.0.0.1:8765

### macOS / Linux (untested)
```bash
# macOS:  brew install python@3.13 ffmpeg node
# Ubuntu: sudo apt install python3.12 python3.12-venv ffmpeg nodejs fonts-noto
bash setup.sh
bash start.sh
```

### Phone / tablet
A phone can't run the processing itself, but it can control a PC that runs ClipForge:
1. On the PC, start **`start-phone.bat`** (Mac/Linux: `bash start.sh --lan`).
2. The window shows a link like `http://192.168.1.20:8765/?key=AbC123xy`. Open it on a phone **on the same Wi-Fi**.
3. If Windows asks about the firewall, allow **Private networks** only.

The key changes on every start, and devices without it are blocked. Use this on your home Wi-Fi,
not on public networks.

## Usage
Paste a link → **Find clips** → tick the clips you want → choose layout, captions and dub → **Export selected**.
The reels are saved in `output/<video title>/`.

## Good to know
- **Low quality (360p) warning:** some office/school networks block YouTube's HD streams. Use a home
  network, or download the video yourself and attach the file **together with the link**. You still
  get the Most-replayed graph and comments.
- **Captions:** YouTube's own captions are used when available. Otherwise the local
  [Whisper](https://github.com/SYSTRAN/faster-whisper) model transcribes each clip on the CPU
  (it downloads about 140 MB on first use).
- **Translation:** Google first, then MyMemory. MyMemory's free limit is about 5,000 characters a day; add an email
  under *Export → Translation limit* to raise it to about 50,000.
- **The dub voice** is natural AI speech ([edge-tts](https://github.com/rany2/edge-tts)), not a clone of the speaker.
- **Downloads failing?** `start.bat` updates yt-dlp on every launch. If YouTube asks you to sign in, pick your
  browser under *Settings → Use YouTube login from browser*.
- **Disk space:** each project keeps its source video in `workspace/`. Delete old folders there to free space.

## Responsible use
Clip your own videos, or creators who allow clipping. Downloading videos may be against YouTube's
Terms of Service, and reposting other people's content can get copyright strikes. You are responsible for how
you use this tool.

## How it works
| File | Job |
|---|---|
| `app.py` | Local web server and API |
| `cf/youtube.py` | Metadata, heatmap, comments, captions, download (yt-dlp) |
| `cf/signals.py` | Scoring every second and picking clips |
| `cf/transcript.py` | YouTube captions → timed words, Whisper fallback |
| `cf/translate.py` | Google → MyMemory translation with cache |
| `cf/dub.py` | edge-tts voices fitted to each line's timing |
| `cf/render.py` | ffmpeg layout, burned-in captions, audio mix |
| `static/` | The browser UI |

Built with [yt-dlp](https://github.com/yt-dlp/yt-dlp), [ffmpeg](https://ffmpeg.org),
[faster-whisper](https://github.com/SYSTRAN/faster-whisper), [edge-tts](https://github.com/rany2/edge-tts),
[deep-translator](https://github.com/nidhaloff/deep-translator) and [Flask](https://flask.palletsprojects.com).
