"""Combine chat and audio into one excitement curve and pick the best moments."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field, asdict

import numpy as np

from .audio import AudioScores
from .chat import ChatScores, messages_between, classify
from .signals import HOP, smooth


@dataclass
class Moment:
    rank: int
    start: float
    end: float
    peak: float
    score: float
    chat_score: float
    audio_score: float
    reasons: list = field(default_factory=list)
    top_chat: list = field(default_factory=list)
    chat_rate: float = 0.0
    chat_rate_baseline: float = 0.0

    @property
    def duration(self):
        return self.end - self.start

    def to_dict(self):
        d = asdict(self)
        d["duration"] = round(self.duration, 2)
        d["start_hms"] = hms(self.start)
        d["peak_hms"] = hms(self.peak)
        return d


def hms(t: float) -> str:
    t = int(round(t))
    return f"{t // 3600:d}:{t // 60 % 60:02d}:{t % 60:02d}"


def combine(audio: AudioScores, chat: ChatScores, chat_weight=1.0, audio_weight=1.0,
            has_chat=True) -> np.ndarray:
    n = min(len(audio.total), len(chat.total))
    a = audio.total[:n]
    c = chat.total[:n]
    if not has_chat:
        return smooth(a, int(3 / HOP))
    # Both signals agreeing is the strongest evidence, so reward overlap.
    agree = np.sqrt(np.maximum(a, 0) * np.maximum(c, 0))
    return smooth(chat_weight * c + audio_weight * a + 0.5 * agree, int(3 / HOP))


def _snap(t: float, loudness: np.ndarray, radius: float, lo: float, hi: float) -> float:
    """Move a cut to the quietest point within `radius` seconds (a pause)."""
    i0 = max(int((t - radius) / HOP), int(lo / HOP), 0)
    i1 = min(int((t + radius) / HOP) + 1, int(hi / HOP), len(loudness))
    if i1 <= i0:
        return t
    return (i0 + int(np.argmin(loudness[i0:i1]))) * HOP


def pick(curve: np.ndarray, audio: AudioScores, chat: ChatScores, loudness: np.ndarray,
         n: int = 5, min_len: float = 15, max_len: float = 60, lead: float = 10.0,
         tail: float = 4.0, min_score: float = 1.0, duration: float | None = None) -> list[Moment]:
    duration = duration or len(curve) * HOP
    taken = np.zeros(len(curve), dtype=bool)
    order = np.argsort(curve)[::-1]
    moments: list[Moment] = []
    median_rate = float(np.median(chat.rate[chat.rate > 0])) if np.any(chat.rate > 0) else 0.0

    for i in order:
        if len(moments) >= n:
            break
        peak_val = curve[i]
        if peak_val < min_score:
            break
        if taken[i]:
            continue
        # Grow the region while excitement stays above 35% of the peak.
        thr = 0.35 * peak_val
        a = i
        while a > 0 and curve[a - 1] >= thr and not taken[a - 1]:
            a -= 1
        b = i
        while b < len(curve) - 1 and curve[b + 1] >= thr and not taken[b + 1]:
            b += 1
        peak_t = i * HOP
        # Clips need the build-up before the reaction, so lead in generously.
        start = a * HOP - lead
        end = b * HOP + tail
        if end - start < min_len:
            pad = min_len - (end - start)
            start -= pad * 0.6
            end += pad * 0.4
        if end - start > max_len:
            # Keep the peak about two thirds of the way through.
            start = max(start, peak_t - max_len * 0.65)
            end = start + max_len
        start, end = max(0.0, start), min(duration, end)
        start = _snap(start, loudness, 1.5, 0, peak_t - 3)
        end = _snap(end, loudness, 1.5, peak_t + 2, duration)
        if end - start < min_len * 0.8:
            continue
        lo, hi = int(start / HOP), int(end / HOP) + 1
        if taken[lo:hi].mean() > 0.2:
            continue
        taken[max(0, lo - int(5 / HOP)): hi + int(5 / HOP)] = True

        seg = slice(max(0, i - 6), i + 7)
        reasons = []
        c_peak = float(chat.total[seg].max()) if len(chat.total) else 0.0
        if chat.messages:
            r_hi = float(chat.rate[lo:hi].max()) if hi > lo else 0
            if c_peak > 1.0 and median_rate > 0:
                reasons.append(f"chat spike ({r_hi:.1f} msg/s vs {median_rate:.1f} usual)")
            for kind, label in (("laugh", "chat laughing"), ("hype", "chat hyped"), ("shock", "chat shocked")):
                share = chat.kind.get(kind)
                if share is not None and share[seg].max() > 0.3:
                    reasons.append(label)
        if audio.laugh[seg].max() > 1.0:
            reasons.append("laughter")
        if audio.scream[seg].max() > 1.0:
            reasons.append("shouting/screaming")
        elif audio.loud[seg].max() > 1.0:
            reasons.append("volume spike")

        msgs = messages_between(chat.messages, peak_t - 5, end + 12)
        counts = Counter(m.text.strip() for m in msgs if m.text.strip() and classify(m.text))
        if not counts:
            counts = Counter(m.text.strip() for m in msgs if m.text.strip())
        moments.append(Moment(
            rank=len(moments) + 1, start=round(start, 2), end=round(end, 2),
            peak=round(peak_t, 2), score=round(float(peak_val), 2),
            chat_score=round(c_peak, 2), audio_score=round(float(audio.total[seg].max()), 2),
            reasons=reasons or ["overall excitement"],
            top_chat=[t for t, _ in counts.most_common(5)],
            chat_rate=round(float(chat.rate[lo:hi].max()) if hi > lo else 0, 2),
            chat_rate_baseline=round(median_rate, 2),
        ))
    return moments
