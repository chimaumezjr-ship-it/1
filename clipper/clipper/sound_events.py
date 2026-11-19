"""AI sound-event detection with YAMNet (Google, trained on AudioSet).

YAMNet labels ~1 s of audio with 521 classes, including Laughter, Giggle,
Belly laugh, Screaming, Shout, Yell, Whoop, Cheering and Applause. It runs on
the CPU through LiteRT at roughly 1 ms per window, so a whole stream is
classified in well under a minute per hour of audio.

The model (~4 MB) is downloaded once to ~/.cache/clipper/. If LiteRT is not
installed or the download fails, the clipper falls back to the signal-based
heuristics in audio.py.
"""
from __future__ import annotations

import os
import urllib.request
from pathlib import Path

import numpy as np

MODEL_URL = ("https://storage.googleapis.com/mediapipe-models/audio_classifier/"
             "yamnet/float32/1/yamnet.tflite")
WINDOW = 15600  # 0.975 s at 16 kHz

# AudioSet class indices in YAMNet's output.
CLASSES = {
    "laugh": [13, 14, 15, 16, 17, 18],     # Laughter, Baby laughter, Giggle, Snicker, Belly laugh, Chuckle
    "scream": [6, 7, 8, 9, 10, 11],        # Shout, Bellow, Whoop, Yell, Children shouting, Screaming
    "cheer": [61, 62, 63],                 # Cheering, Applause, Chatter (crowd)
    "gasp": [39],
}


def _model_path() -> Path:
    d = Path(os.environ.get("CLIPPER_CACHE_HOME", Path.home() / ".cache" / "clipper"))
    d.mkdir(parents=True, exist_ok=True)
    return d / "yamnet.tflite"


class YamNet:
    def __init__(self, threads: int = 2):
        try:
            from ai_edge_litert.interpreter import Interpreter
        except ImportError:
            from tflite_runtime.interpreter import Interpreter  # older runtime
        path = _model_path()
        if not path.exists() or path.stat().st_size < 1_000_000:
            tmp = path.with_suffix(".part")
            urllib.request.urlretrieve(MODEL_URL, tmp)
            tmp.replace(path)
        self.it = Interpreter(str(path), num_threads=threads)
        self.it.allocate_tensors()
        self.inp = self.it.get_input_details()[0]["index"]
        self.out = self.it.get_output_details()[0]["index"]

    def __call__(self, window: np.ndarray) -> dict:
        w = window[-WINDOW:].astype(np.float32)
        self.it.set_tensor(self.inp, w)
        self.it.invoke()
        p = self.it.get_tensor(self.out)[0]
        return {k: float(p[idx].max()) for k, idx in CLASSES.items()}


def load(log=print):
    try:
        return YamNet()
    except Exception as e:
        log(f"  Sound-event model unavailable ({type(e).__name__}: {e}); using heuristics only.")
        return None
