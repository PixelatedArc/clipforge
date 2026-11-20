# ClipForge ✂

**A free, local YouTube-to-Shorts clipper.** Paste a YouTube link (or attach a video file) and ClipForge
finds the best moments, then exports vertical 1080×1920 reels with captions. It can also translate
the subtitles and add an AI voice dub.

Everything runs on your own computer. There are no subscriptions, watermarks or uploads to a server.

## Features
- **Smart clip picking:** each moment is scored by YouTube's *Most replayed* graph, timestamp comments
  (`12:34 bro 😂`), loud reactions in the audio, and hype words in the transcript. Each clip shows *why* it was picked.
- **Preview and trim** in the browser, with a clickable "hype graph" of the whole video.
- **Reel formats:** 9:16 (Shorts/Reels/TikTok), 4:5 (Instagram feed), 1:1. Layouts: **split screen** (streamer's
  facecam on top, gameplay below; mark the facecam once with a drag), full screen, zoomed, or whole video on blur.
  Each clip can use its own layout, and there's a phone-shaped preview.
- **Editing that holds attention:** a big hook text in the first 2 seconds, an optional flash-forward teaser of the
  best moment, zoom + flash intro, dead-air removal (jump cuts), zoom punches on hype moments, a progress bar and a
  "Follow for more" ending (or fade / loop-friendly).
- **Audio:** loudness matched to platforms (-14 LUFS), whoosh on transitions, optional background music from the
  `music` folder (automatically lowered under speech).
- **Captions:** bold animated word-by-word captions. For non-English speech, English captions come straight from the
  audio (Whisper translate), which works much better for Hinglish than translating text. 20 languages in total.
- **Upload kit:** for every reel, a ready-to-paste YouTube Shorts title + description, Instagram caption and TikTok
  caption with hashtags and the streamer credit, saved as `<reel>_upload.txt` and in `UPLOAD_KIT.txt`. Built-in
  templates work offline; a free Gemini or Groq API key (*Channel & upload kit* panel) lets an AI write clip-specific text.
- **AI dub:** translates the speech and voices it with free neural voices (e.g. English → Hindi),
  with the original audio kept underneath.
- **Phone mode:** control it from your phone over Wi-Fi while your PC does the work.

## Install

### Windows (tested)
1. Install [Python 3.13](https://www.python.org/downloads/). Python 3.14+ is too new for some of the libraries.
2. Download this repo (green **Code** button → *Download ZIP*, then extract it) or `git clone` it.
3. Double-click **`setup.bat`**. It installs everything (including ffmpeg and Node.js via winget if they're missing)
   and puts a **ClipForge icon on your Desktop**.
4. Double-click the **ClipForge** icon. Your browser opens at http://127.0.0.1:8765. A small window
   appears on the taskbar; keep it open while you use the app, and close it to stop ClipForge.

Moved the folder? Run **`create-shortcut.bat`** again to fix the icon.

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
1. Once: open **Channel & upload kit** (left) and fill in the game, the streamer you credit and your handle.
2. Paste a link (or choose a file) → **Find clips**.
3. For streams: pause on a frame with the streamer's camera → **Mark facecam** → drag a box over it.
   Use **Preview this frame as a reel** to check the framing.
4. Tick the clips you want (optionally type your own hook text or change a clip's layout) → pick a preset
   (🔥 Viral / ✨ Clean / ✂ Just the clip) → **Export selected**.
5. The reels and their upload text are saved in `output/<video title>/`. Use the Copy buttons under each reel.

- **Already downloaded the video?** Click **Choose video on this PC** (or paste its path). ClipForge uses the file
  where it is, so nothing is copied and it starts instantly. Add the YouTube link too to get Most-replayed and comments.
- **Need your PC back?** Press **Pause**: the work stops using your CPU and continues where it left off when you press
  **Resume**. **Stop** cancels it; finished reels are kept, and **Run again** restarts a stopped search.
- ClipForge runs at low priority and leaves CPU cores free, so other apps stay usable while it works.

## Good to know
- **Low quality (360p) warning:** some office/school networks block YouTube's HD streams. Use a home
  network, or download the video yourself and attach the file **together with the link**. You still
  get the Most-replayed graph and comments.
- **Captions:** YouTube's own captions are used when available. Otherwise the local
  [Whisper](https://github.com/SYSTRAN/faster-whisper) model transcribes each clip on the CPU
  (it downloads about 140 MB on first use).
- **"Transcribe whole video"** is slow on long videos (roughly 20–40 minutes of CPU work for a 2-hour stream).
  Leave it off unless you need it: captions for each exported clip are made anyway.
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
