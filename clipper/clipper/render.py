"""Render a 9:16 1080x1920 clip with burned-in captions using ffmpeg."""
from __future__ import annotations

import subprocess
from dataclasses import dataclass, field

from .captions import W, H


@dataclass
class Source:
    """Where frames and sound come from. Video may be a remote stream URL."""
    video: str
    audio: str | None = None          # separate local audio track (optional)
    headers: dict = field(default_factory=dict)


def _filter_escape(path: str) -> str:
    return path.replace("\\", "/").replace(":", r"\:").replace("'", r"\'")


def layout_filter(layout: str, crop_x: float = 0.5, facecam: tuple | None = None) -> str:
    """Filter graph from [0:v] to [v] in 1080x1920."""
    if layout == "crop":
        # Fill the frame, choose which horizontal slice to keep (0=left, 1=right).
        return (f"[0:v]scale=-2:{H},crop={W}:{H}:(iw-{W})*{crop_x:.3f}:0,setsar=1[v]")
    if layout == "split":
        if not facecam:
            raise ValueError("split layout needs --facecam x,y,w,h (fractions of the frame)")
        fx, fy, fw, fh = facecam
        top_h = int(H * 0.4)
        bot_h = H - top_h
        return (
            "[0:v]split[s1][s2];"
            f"[s1]crop=iw*{fw}:ih*{fh}:iw*{fx}:ih*{fy},scale={W}:{top_h}:force_original_aspect_ratio=increase,"
            f"crop={W}:{top_h},setsar=1[cam];"
            f"[s2]scale=-2:{bot_h},crop={W}:{bot_h}:(iw-{W})*{crop_x:.3f}:0,setsar=1[game];"
            "[cam][game]vstack[v]"
        )
    # Default "blur": whole frame, centred, over a blurred zoomed copy of itself.
    return (
        "[0:v]split[a][b];"
        f"[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
        "boxblur=24:4,eq=brightness=-0.08:saturation=1.1,setsar=1[bg];"
        f"[b]scale={W}:-2,setsar=1[fg];"
        "[bg][fg]overlay=(W-w)/2:(H-h)/2[v]"
    )


def caption_y(layout: str) -> int:
    # Captions sit where they do not cover the action / the face.
    return {"split": int(H * 0.4) + 40, "crop": int(H * 0.68)}.get(layout, int(H * 0.73))


def render(src: Source, start: float, duration: float, out_path: str, layout: str = "blur",
           ass_path: str | None = None, crop_x: float = 0.5, facecam: tuple | None = None,
           fps: int = 30, crf: int = 20, preset: str = "veryfast", loudnorm: bool = True):
    cmd = ["ffmpeg", "-nostdin", "-y", "-v", "error"]
    if src.headers and src.video.startswith("http"):
        hdr = "".join(f"{k}: {v}\r\n" for k, v in src.headers.items())
        cmd += ["-headers", hdr]
    cmd += ["-ss", f"{start:.3f}", "-t", f"{duration:.3f}", "-i", src.video]
    audio_in = "0:a:0?"
    if src.audio:
        cmd += ["-ss", f"{start:.3f}", "-t", f"{duration:.3f}", "-i", src.audio]
        audio_in = "1:a:0"

    vf = layout_filter(layout, crop_x, facecam)
    tail = f"fps={fps}"
    if ass_path:
        tail += f",subtitles=filename='{_filter_escape(ass_path)}'"
    vf = vf[: -len("[v]")] + f"[pre];[pre]{tail}[v]"
    cmd += ["-filter_complex", vf, "-map", "[v]", "-map", audio_in]
    if loudnorm:
        cmd += ["-af", "loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000"]
    cmd += ["-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-pix_fmt", "yuv420p",
            "-profile:v", "high", "-c:a", "aac", "-b:a", "160k", "-ar", "48000",
            "-movflags", "+faststart", "-shortest", out_path]
    subprocess.run(cmd, check=True)
