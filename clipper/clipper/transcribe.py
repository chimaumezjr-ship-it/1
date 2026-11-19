"""Word-level transcription of each clip with faster-whisper (runs locally)."""
from __future__ import annotations

import subprocess

import numpy as np

_MODELS = {}


def _load(model_size: str):
    if model_size not in _MODELS:
        from faster_whisper import WhisperModel
        try:
            _MODELS[model_size] = WhisperModel(model_size, device="auto", compute_type="default")
        except Exception:
            _MODELS[model_size] = WhisperModel(model_size, device="cpu", compute_type="int8")
    return _MODELS[model_size]


def read_audio(path: str, start: float, duration: float, sr: int = 16000) -> np.ndarray:
    cmd = ["ffmpeg", "-nostdin", "-v", "error", "-ss", f"{start:.3f}", "-t", f"{duration:.3f}",
           "-i", path, "-vn", "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"]
    raw = subprocess.run(cmd, check=True, stdout=subprocess.PIPE).stdout
    return np.frombuffer(raw, dtype=np.float32)


def transcribe(audio_path: str, start: float, duration: float, model_size: str = "small",
               language: str | None = None) -> list[dict]:
    """Return [{'start','end','word'}] with times relative to the clip start."""
    model = _load(model_size)
    audio = read_audio(audio_path, start, duration)
    segments, _ = model.transcribe(audio, language=language, word_timestamps=True,
                                   vad_filter=True, condition_on_previous_text=False)
    words = []
    for seg in segments:
        for w in seg.words or []:
            text = w.word.strip()
            if text:
                words.append({"start": max(0.0, float(w.start)), "end": float(w.end), "word": text})
    return words
