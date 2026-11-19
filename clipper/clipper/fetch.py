"""Download what the analysis needs from a stream URL via yt-dlp.

To keep long VODs cheap, only the audio track is downloaded in full. Video
frames for each clip are read straight from the stream at render time.
"""
from __future__ import annotations

from pathlib import Path


def _ydl(opts=None):
    import yt_dlp
    base = {"quiet": True, "no_warnings": True, "noprogress": True}
    base.update(opts or {})
    return yt_dlp.YoutubeDL(base)


def probe(url: str) -> dict:
    with _ydl() as y:
        info = y.extract_info(url, download=False)
    if info.get("_type") == "playlist":
        raise SystemExit("That link is a playlist/channel. Pass a single stream or video link.")
    if info.get("is_live") or info.get("live_status") == "is_live":
        raise SystemExit("This stream is still live. Run the clipper on the VOD once it has ended.")
    return info


def download_audio(url: str, workdir: Path, log=print) -> Path:
    existing = sorted(workdir.glob("audio.*"))
    existing = [p for p in existing if p.suffix not in (".part", ".ytdl")]
    if existing:
        return existing[0]
    log("Downloading audio track (only the audio, to keep it fast)...")
    opts = {"format": "bestaudio/bestaudio*/best", "outtmpl": str(workdir / "audio.%(ext)s"),
            "noprogress": False, "quiet": False}
    with _ydl(opts) as y:
        y.download([url])
    files = [p for p in workdir.glob("audio.*") if p.suffix not in (".part", ".ytdl")]
    if not files:
        raise RuntimeError("Audio download failed.")
    return files[0]


def video_stream(url: str, max_height: int = 1080) -> tuple[str, dict]:
    """Direct media URL (+ HTTP headers) for the best video up to max_height."""
    fmt = (f"bv*[height<={max_height}][vcodec!^=av01]/bv*[height<={max_height}]/"
           f"b[height<={max_height}]/bv*/b")
    with _ydl({"format": fmt}) as y:
        info = y.extract_info(url, download=False)
    chosen = info["requested_formats"][0] if info.get("requested_formats") else info
    headers = chosen.get("http_headers") or info.get("http_headers") or {}
    return chosen["url"], headers


def download_video(url: str, workdir: Path, max_height: int = 1080) -> Path:
    existing = [p for p in workdir.glob("video.*") if p.suffix not in (".part", ".ytdl")]
    if existing:
        return existing[0]
    fmt = f"bv*[height<={max_height}]+ba/b[height<={max_height}]/b"
    with _ydl({"format": fmt, "outtmpl": str(workdir / "video.%(ext)s"),
               "merge_output_format": "mp4", "quiet": False, "noprogress": False}) as y:
        y.download([url])
    return [p for p in workdir.glob("video.*") if p.suffix not in (".part", ".ytdl")][0]


def comments(url: str, limit: int = 3000) -> list[dict]:
    opts = {"getcomments": True, "skip_download": True,
            "extractor_args": {"youtube": {"max_comments": [str(limit)], "comment_sort": ["top"]}}}
    try:
        with _ydl(opts) as y:
            info = y.extract_info(url, download=False)
        return info.get("comments") or []
    except Exception:
        return []
