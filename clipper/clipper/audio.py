"""Audio excitement analysis: loudness spikes, shouting/screaming and laughter.

The audio is streamed out of ffmpeg as 16 kHz mono, so even a 10-hour VOD
never sits in memory. For every 0.5 s hop we look at a 1 s window and measure:

* loudness (RMS in dB)                       -> "the audio raises"
* share of energy in the 1-4 kHz band and the
  spectral centroid                          -> shouting / screaming is loud
                                                AND bright (energy moves up)
* 3-8 Hz modulation of the loudness envelope -> laughter is a rapid, regular
                                                "ha-ha-ha" pulse
* YAMNet sound-event probabilities          -> an AudioSet-trained model that
  (see sound_events.py)                         recognises laughter, screaming,
                                                shouting, yelling and cheering

Each feature is converted to a local z-score (relative to the surrounding
few minutes), so a quiet streamer getting loud counts as much as a loud one.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass

import numpy as np

from .signals import HOP, local_z, smooth

SR = 16000
HOP_N = int(SR * HOP)
WIN_N = SR  # 1 s analysis window
_FRAME = 160  # 10 ms envelope frames


@dataclass
class AudioFeatures:
    rms_db: np.ndarray
    hi_ratio: np.ndarray
    centroid: np.ndarray
    modulation: np.ndarray
    # YAMNet probabilities per bin (empty arrays when the model is unavailable)
    p_laugh: np.ndarray = None
    p_scream: np.ndarray = None
    p_cheer: np.ndarray = None

    @property
    def has_model(self):
        return self.p_laugh is not None and len(self.p_laugh) == len(self.rms_db) > 0

    def save(self, path):
        np.savez_compressed(path, **{k: v for k, v in self.__dict__.items() if v is not None})

    @classmethod
    def load(cls, path):
        d = np.load(path)
        return cls(**{k: d[k] for k in d.files})


def _window_features(win: np.ndarray, hann: np.ndarray, freqs: np.ndarray):
    rms = np.sqrt(np.mean(win ** 2) + 1e-12)
    rms_db = 20 * np.log10(rms + 1e-9)

    spec = np.abs(np.fft.rfft(win * hann)) ** 2
    voice = (freqs >= 80) & (freqs <= 8000)
    total = spec[voice].sum() + 1e-12
    hi = spec[(freqs >= 1000) & (freqs <= 4000)].sum() / total
    centroid = float((spec[voice] * freqs[voice]).sum() / total)

    env = np.sqrt(np.mean(win[: len(win) // _FRAME * _FRAME].reshape(-1, _FRAME) ** 2, axis=1))
    env = env - env.mean()
    em = np.abs(np.fft.rfft(env * np.hanning(len(env)))) ** 2
    ef = np.fft.rfftfreq(len(env), d=_FRAME / SR)
    band = em[(ef >= 3) & (ef <= 8)].sum()
    allb = em[(ef >= 1) & (ef <= 25)].sum() + 1e-12
    return rms_db, hi, centroid, band / allb


def analyze_file(path: str, progress=None, use_model: bool = True, log=print) -> AudioFeatures:
    """Decode `path` with ffmpeg and compute per-hop audio features."""
    model = None
    if use_model:
        from . import sound_events
        model = sound_events.load(log)
    events = []
    cmd = ["ffmpeg", "-nostdin", "-v", "error", "-i", path, "-vn", "-ac", "1",
           "-ar", str(SR), "-f", "s16le", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE)
    hann = np.hanning(WIN_N)
    freqs = np.fft.rfftfreq(WIN_N, d=1 / SR)
    buf = np.zeros(WIN_N, dtype=np.float32)
    feats = []
    hops = 0
    try:
        while True:
            raw = proc.stdout.read(HOP_N * 2)
            if not raw:
                break
            chunk = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
            if len(chunk) < HOP_N:
                chunk = np.pad(chunk, (0, HOP_N - len(chunk)))
            buf = np.concatenate([buf[HOP_N:], chunk])
            feats.append(_window_features(buf, hann, freqs))
            if model is not None:
                e = model(buf)
                events.append((e["laugh"], e["scream"], e["cheer"]))
            hops += 1
            if progress and hops % 1200 == 0:
                progress(hops * HOP)
    finally:
        proc.stdout.close()
        rc = proc.wait()
    if rc != 0 and not feats:
        raise RuntimeError(f"ffmpeg could not decode audio from {path}")
    # Window i ends at (i+1)*HOP; centre it so index i ~ time i*HOP.
    arr = np.array(feats, dtype=np.float64) if feats else np.zeros((0, 4))
    arr = np.roll(arr, -1, axis=0) if len(arr) > 1 else arr
    out = AudioFeatures(arr[:, 0], arr[:, 1], arr[:, 2], arr[:, 3])
    if model is not None and events:
        ev = np.array(events, dtype=np.float64)
        ev = np.roll(ev, -1, axis=0) if len(ev) > 1 else ev
        out.p_laugh, out.p_scream, out.p_cheer = ev[:, 0], ev[:, 1], ev[:, 2]
    return out


def _relu(x):
    return np.maximum(x, 0.0)


@dataclass
class AudioScores:
    loud: np.ndarray
    scream: np.ndarray
    laugh: np.ndarray
    total: np.ndarray


def score(f: AudioFeatures, baseline_s: float = 180.0) -> AudioScores:
    w = int(baseline_s / HOP)
    z_loud = local_z(f.rms_db, w)
    z_hi = local_z(f.hi_ratio, w)
    z_cent = local_z(f.centroid, w)
    z_mod = local_z(f.modulation, w)

    loud = _relu(z_loud - 0.5)
    louder = (z_loud > 0.8).astype(float)
    brightness = _relu(0.5 * z_hi + 0.5 * z_cent)
    scream = np.sqrt(loud * brightness) * louder
    laugh = np.sqrt(_relu(z_mod - 0.5) * _relu(z_loud)) * (z_loud > 0.3)

    if f.has_model:
        # The model knows what laughter/screaming actually sound like, so its
        # labels replace the heuristics (which also fire on any loud, bright
        # noise). A small loudness gate keeps constant background audio out.
        def prob_score(p):
            return 8 * np.clip((p - 0.08) / 0.5, 0, 1)
        gate = np.clip(z_loud + 1.0, 0, 1)
        laugh = prob_score(f.p_laugh) * gate
        scream = prob_score(f.p_scream) * gate
        loud = loud + 0.5 * prob_score(f.p_cheer) * gate

    loud, scream, laugh = (np.clip(smooth(s, 3), 0, 8) for s in (loud, scream, laugh))
    total = loud + 0.8 * scream + 0.8 * laugh
    return AudioScores(loud, scream, laugh, total)


def quiet_points(f: AudioFeatures) -> np.ndarray:
    """Per-bin loudness, used to snap clip edges to pauses instead of mid-word."""
    return smooth(f.rms_db, 2)
