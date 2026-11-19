"""clipper — give it a stream link, get back finished vertical clips.

    python -m clipper "https://www.twitch.tv/videos/123456789"
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from pathlib import Path

import numpy as np

from . import audio as audio_mod
from . import chat as chat_mod
from .captions import build_ass, build_srt
from .detect import combine, hms, pick
from .render import Source, caption_y, render
from .signals import HOP


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def slug(s: str, n: int = 40) -> str:
    s = re.sub(r"[^\w\s-]", "", s).strip().lower()
    return re.sub(r"[\s_-]+", "-", s)[:n].strip("-") or "clip"


def parse_args(argv=None):
    p = argparse.ArgumentParser(prog="clipper", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("source", help="Stream/VOD URL (Twitch, YouTube, Kick, ...) or a local video file")
    p.add_argument("-n", "--clips", type=int, default=5, help="how many clips to make (default 5)")
    p.add_argument("-o", "--out", default="clips", help="output folder (default ./clips)")
    p.add_argument("--min-len", type=float, default=15, help="shortest clip, seconds (default 15)")
    p.add_argument("--max-len", type=float, default=60, help="longest clip, seconds (default 60)")
    p.add_argument("--lead", type=float, default=10, help="seconds of build-up before the spike (default 10)")
    p.add_argument("--chat", help="local chat file (chat-downloader JSON/JSONL or TwitchDownloader JSON)")
    p.add_argument("--no-chat", action="store_true", help="ignore chat; use audio only")
    p.add_argument("--chat-delay", type=float, default=6,
                   help="how many seconds chat lags behind the stream (default 6)")
    p.add_argument("--chat-weight", type=float, default=1.0)
    p.add_argument("--audio-weight", type=float, default=1.0)
    p.add_argument("--no-ai-audio", action="store_true",
                   help="skip the YAMNet laughter/scream model and use signal heuristics only")
    p.add_argument("--min-score", type=float, default=1.0, help="ignore moments weaker than this")
    p.add_argument("--layout", choices=["blur", "crop", "split"], default="blur",
                   help="blur: full frame on blurred background; crop: fill by cropping; "
                        "split: facecam on top, gameplay below (needs --facecam)")
    p.add_argument("--crop-x", type=float, default=0.5,
                   help="horizontal crop position 0=left .. 1=right (crop/split layouts)")
    p.add_argument("--facecam", help="facecam box as fractions x,y,w,h, e.g. 0.75,0.0,0.25,0.3")
    p.add_argument("--no-captions", action="store_true")
    p.add_argument("--whisper-model", default="small",
                   help="faster-whisper model: tiny, base, small, medium, large-v3 (default small)")
    p.add_argument("--language", help="spoken language code, e.g. en (auto-detected if omitted)")
    p.add_argument("--font", default="Arial Black", help="caption font family")
    p.add_argument("--font-size", type=int, default=110)
    p.add_argument("--max-height", type=int, default=1080, help="source video quality cap")
    p.add_argument("--download-video", action="store_true",
                   help="download the full video instead of streaming each clip's frames")
    p.add_argument("--analyze-only", action="store_true", help="find moments, don't render")
    p.add_argument("--cache", default=".clipper_cache", help="where downloads/analysis are cached")
    p.add_argument("--preset", default="veryfast", help="x264 preset (slower = smaller files)")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    t0 = time.time()
    local = Path(args.source).expanduser()
    is_local = local.exists()

    # ---------------------------------------------------------------- input
    if is_local:
        info = {"id": hashlib.sha1(str(local.resolve()).encode()).hexdigest()[:12],
                "title": local.stem, "duration": None, "extractor_key": "local"}
    else:
        from . import fetch
        log("Looking up stream...")
        info = fetch.probe(args.source)
    work = Path(args.cache) / slug(f"{info.get('extractor_key', '')}-{info['id']}", 80)
    work.mkdir(parents=True, exist_ok=True)
    title = info.get("title") or "stream"
    log(f"  {title}")

    audio_path = local if is_local else fetch.download_audio(args.source, work, log)

    # ---------------------------------------------------------------- audio
    feat_path = work / ("audio_features_basic.npz" if args.no_ai_audio else "audio_features.npz")
    if feat_path.exists():
        feats = audio_mod.AudioFeatures.load(feat_path)
    else:
        log("Analysing audio (loudness, shouting, laughter)...")
        feats = audio_mod.analyze_file(str(audio_path), progress=lambda s: log(f"  ...{hms(s)}"),
                                       use_model=not args.no_ai_audio, log=log)
        feats.save(feat_path)
    n_bins = len(feats.rms_db)
    duration = n_bins * HOP
    if duration < args.min_len:
        log("Video is shorter than --min-len; nothing to clip.")
        return 1
    a_scores = audio_mod.score(feats)

    # ---------------------------------------------------------------- chat
    messages = []
    if not args.no_chat:
        if args.chat:
            messages = chat_mod.load_file(args.chat)
            log(f"Loaded {len(messages):,} chat messages from {args.chat}")
        elif not is_local:
            cache = work / "chat.jsonl"
            if cache.exists():
                messages = chat_mod.load_file(cache)
            else:
                log("Downloading chat replay...")
                messages = chat_mod.download_chat(args.source, log)
                if not messages and "youtube" in info.get("extractor_key", "").lower():
                    log("  No live chat; reading YouTube comments for timestamp mentions...")
                    messages = chat_mod.comment_timestamp_mentions(fetch.comments(args.source), duration)
                chat_mod.save_jsonl(messages, cache)
            log(f"  {len(messages):,} chat messages")
    has_chat = len(messages) > 0
    if not has_chat and not args.no_chat:
        log("  No chat found: picking moments from audio only.")
    c_scores = chat_mod.score(messages, n_bins, delay=args.chat_delay)

    # ---------------------------------------------------------------- detect
    curve = combine(a_scores, c_scores, args.chat_weight, args.audio_weight, has_chat)
    moments = pick(curve, a_scores, c_scores, audio_mod.quiet_points(feats), n=args.clips,
                   min_len=args.min_len, max_len=args.max_len, lead=args.lead,
                   min_score=args.min_score, duration=duration)
    out_dir = Path(args.out) / slug(title)
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_timeline(out_dir / "timeline.csv", curve, a_scores, c_scores)

    if not moments:
        log("No moments stood out. Try --min-score 0.5 or --no-chat.")
        return 1
    log(f"\nTop {len(moments)} moments:")
    for m in moments:
        log(f"  #{m.rank}  {hms(m.start)} -> {hms(m.end)}  ({m.duration:4.0f}s)  "
            f"score {m.score:5.2f}  | {', '.join(m.reasons)}")
        if m.top_chat:
            log(f"        chat: {' | '.join(t[:30] for t in m.top_chat[:3])}")

    report = {"source": args.source, "title": title, "duration": duration,
              "moments": [m.to_dict() for m in moments]}
    if args.analyze_only:
        (out_dir / "moments.json").write_text(json.dumps(report, indent=2, ensure_ascii=False))
        log(f"\nSaved {out_dir / 'moments.json'}")
        return 0

    # ---------------------------------------------------------------- render
    facecam = tuple(float(x) for x in args.facecam.split(",")) if args.facecam else None
    if args.layout == "split" and (not facecam or len(facecam) != 4):
        log("--layout split needs --facecam x,y,w,h")
        return 2
    if is_local:
        src = Source(str(local))
    elif args.download_video:
        log("\nDownloading full video...")
        src = Source(str(fetch.download_video(args.source, work, args.max_height)))
    else:
        url, headers = fetch.video_stream(args.source, args.max_height)
        src = Source(url, audio=str(audio_path), headers=headers)

    for m in moments:
        name = f"{m.rank:02d}_{hms(m.peak).replace(':', '-')}"
        log(f"\n[{m.rank}/{len(moments)}] {hms(m.start)} - {hms(m.end)}")
        ass_path = None
        if not args.no_captions:
            from .transcribe import transcribe
            log(f"  transcribing ({args.whisper_model})...")
            words = transcribe(str(audio_path), m.start, m.duration, args.whisper_model, args.language)
            m_dict = report["moments"][m.rank - 1]
            m_dict["transcript"] = " ".join(w["word"] for w in words)
            ass_path = out_dir / f"{name}.ass"
            # Per-word loudness and chat echoes feed the "funniest word" pick.
            loud = [float(feats.rms_db[min(len(feats.rms_db) - 1,
                                           int((m.start + (w["start"] + w["end"]) / 2) / HOP))])
                    for w in words]
            chat_words = {re.sub(r"[^\w']", "", t.lower()) for msg in
                          chat_mod.messages_between(messages, m.peak - 5, m.end + 12)
                          for t in msg.text.split()}
            ass_path.write_text(build_ass(words, m.duration, font=args.font, font_size=args.font_size,
                                          y=caption_y(args.layout), loudness=loud,
                                          chat_words=chat_words), encoding="utf-8")
            (out_dir / f"{name}.srt").write_text(build_srt(words), encoding="utf-8")
        out = out_dir / f"{name}.mp4"
        log("  rendering 1080x1920...")
        render(src, m.start, m.duration, str(out), layout=args.layout,
               ass_path=str(ass_path) if ass_path else None, crop_x=args.crop_x,
               facecam=facecam, preset=args.preset)
        report["moments"][m.rank - 1]["file"] = str(out)
        log(f"  -> {out}")

    (out_dir / "moments.json").write_text(json.dumps(report, indent=2, ensure_ascii=False))
    log(f"\nDone in {time.time() - t0:.0f}s. Clips are in {out_dir}/")
    return 0


def _write_timeline(path, curve, a, c):
    """Per-second scores, handy for checking why a moment was (not) picked."""
    step = int(1 / HOP)
    with open(path, "w") as fh:
        fh.write("time,total,chat,audio,loud,scream,laugh,chat_msgs_per_s\n")
        for i in range(0, len(curve), step):
            fh.write(f"{i * HOP:.0f},{curve[i]:.3f},{c.total[i]:.3f},{a.total[i]:.3f},"
                     f"{a.loud[i]:.3f},{a.scream[i]:.3f},{a.laugh[i]:.3f},{c.rate[i]:.2f}\n")
