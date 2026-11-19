"""Small numpy helpers shared by the chat and audio analysers."""
import numpy as np

# Every signal is sampled on the same grid so they can be added together.
HOP = 0.5  # seconds per bin


def local_z(x: np.ndarray, window_bins: int) -> np.ndarray:
    """Robust z-score of x against a slowly moving local baseline.

    Streams drift (quiet intro, loud game, chat that grows over hours), so a
    spike is judged against the median/MAD of the surrounding window rather
    than the whole stream. Baselines are computed per chunk and interpolated,
    which stays O(n) on multi-hour streams.
    """
    x = np.asarray(x, dtype=np.float64)
    n = len(x)
    if n == 0:
        return x
    w = max(8, min(window_bins, n))
    starts = np.arange(0, n, max(1, w // 2))
    centers, meds, mads = [], [], []
    for s in starts:
        seg = x[max(0, s - w // 2): s + w // 2 + 1]
        m = np.median(seg)
        centers.append(s)
        meds.append(m)
        mads.append(np.median(np.abs(seg - m)))
    idx = np.arange(n)
    med = np.interp(idx, centers, meds)
    mad = np.interp(idx, centers, mads)
    # Floor the spread so a near-constant stretch (silence, dead chat) does
    # not turn tiny wiggles into huge z-scores.
    spread = 1.4826 * mad
    floor = max(1e-6, 0.25 * float(np.std(x)))
    return (x - med) / np.maximum(spread, floor)


def smooth(x: np.ndarray, bins: int) -> np.ndarray:
    if bins <= 1 or len(x) == 0:
        return np.asarray(x, dtype=np.float64)
    k = np.ones(bins) / bins
    return np.convolve(x, k, mode="same")


def rolling_sum(x: np.ndarray, bins: int) -> np.ndarray:
    return smooth(x, bins) * bins


def shift(x: np.ndarray, bins: int) -> np.ndarray:
    """Shift a series earlier by `bins` (positive) — used for chat delay."""
    if bins <= 0:
        return x
    out = np.zeros_like(x)
    out[: len(x) - bins] = x[bins:]
    return out
