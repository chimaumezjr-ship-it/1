# clipper

Give it a stream link. It finds the best moments and renders finished
**vertical 1080×1920 clips with burned-in captions**.

```bash
python -m clipper "https://www.youtube.com/live/qSyo5d-dVzA"
```

## How it picks moments

Every half-second of the stream gets an excitement score built from:

| Signal | What it detects | How |
| --- | --- | --- |
| **Chat spikes** | Chat suddenly moving faster than usual for that part of the stream | Live-chat replay (Twitch VODs, YouTube streams). Reaction messages (KEKW, LMAO, 💀, ????, POG, "clip it"...) count extra. Chat lags the stream, so it's shifted back 6 s (`--chat-delay`). |
| **Comments** | Moments people point to in the comments ("12:34 LMAO") | YouTube comments, used when a video has no live chat |
| **Laughter, screaming, shouting** | The streamer laughing, screaming, yelling | YAMNet, Google's AudioSet sound model (~4 MB, CPU, about 1 ms per second of audio) |
| **Volume spikes** | The audio getting much louder than the last few minutes | Loudness compared with a rolling local baseline |

Moments where chat **and** audio spike together score highest. Each clip
starts about 10 s before the spike so the build-up is included, and its cuts
land on pauses rather than mid-word.

## Captions

Bold white text with a thick black outline, **two words at a time**, and the
**funniest word in yellow**. "Funniest" is picked from words that were yelled
(louder than the rest of the clip), words chat echoed back, reaction words
(no, what, bro, wait, swears, laugh words) and stretched spellings ("nooo").
Speech is transcribed locally with faster-whisper. Each clip also gets an
`.srt` file for platform uploads.

## Install

You need Python 3.10+ and [ffmpeg](https://ffmpeg.org/download.html) on your PATH.

```bash
pip install -r requirements.txt
```

## Usage

```bash
python -m clipper URL [options]
```

| Option | Default | |
| --- | --- | --- |
| `-n, --clips` | 5 | how many clips to make |
| `--min-len / --max-len` | 15 / 60 | clip length in seconds |
| `--layout` | `blur` | `blur`: full frame over a blurred background. `crop`: fill the frame by cropping. `split`: facecam on top, gameplay below |
| `--facecam x,y,w,h` | | facecam box as fractions of the frame, for `split` (e.g. `0.75,0,0.25,0.3` = top-right corner) |
| `--crop-x` | 0.5 | which part to keep when cropping (0 = left, 1 = right) |
| `--whisper-model` | `small` | `tiny`/`base` are faster; `medium`/`large-v3` are more accurate |
| `--language` | auto | e.g. `en` |
| `--chat FILE` | | use a chat file you already have (chat-downloader or TwitchDownloader JSON) |
| `--no-chat` | | pick moments from audio only |
| `--analyze-only` | | just list the moments, don't render |
| `--download-video` | | download the whole video instead of streaming each clip's frames |

To keep long streams cheap, only the **audio** is downloaded in full. Each
clip's video is read straight from the stream at render time. Downloads and
analysis are cached in `.clipper_cache/`, so re-running with different
options is fast.

Output goes in `clips/<stream-title>/`:

- `01_1-23-45.mp4`, ... the finished clips, best first
- `.srt` / `.ass` caption files
- `moments.json`: why each moment was picked, its top chat messages and its transcript
- `timeline.csv`: per-second scores, so you can see why something was or wasn't picked

## Tests

```bash
python tests/test_pipeline.py
```

Builds a synthetic 6-minute stream with a planted scream, laughter and a
chat-only hype moment. It checks that all three are found and rendered at
1080×1920. No network is needed.
