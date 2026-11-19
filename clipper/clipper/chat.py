"""Chat / comment activity: how hard the audience is reacting, second by second.

Sources:
* Live-chat replay of a Twitch VOD or YouTube stream (via chat-downloader).
* A local chat file: chat-downloader JSON/JSONL, TwitchDownloader JSON, or a
  simple JSONL of {"time": seconds, "text": "..."} lines.
* YouTube comments that mention timestamps ("12:34 LMAO"), for videos that
  have no live chat. Each mention counts as a vote for that moment.

Chat lags the moment it reacts to (stream delay + typing time), so the
series is shifted earlier by `delay` seconds before it is scored.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .signals import HOP, local_z, rolling_sum, shift, smooth

# Messages containing these count extra: they are what chat spams when
# something clip-worthy happens.
HYPE = {
    "laugh": ["lul", "lol", "lmao", "lmfao", "kekw", "kek", "omegalul", "icant",
              "😂", "🤣", "💀", "haha", "hahaha", "dead", "crying", "pepelaugh",
              "xd", "lulw", "aware"],
    "hype": ["pog", "poggers", "pogchamp", "w", "www", "clip", "clip it", "clipit",
             "no way", "noway", "insane", "omg", "holy", "letsgo", "lets go",
             "🔥", "goat", "huge", "monkas", "pogu", "ez", "gg"],
    "shock": ["?", "??", "???", "wtf", "what", "bruh", "omegalul", "monkaw",
              "😳", "😱", "nah", "jumpscare", "scream"],
}
_TOKEN = re.compile(r"[\w']+|[^\w\s]", re.UNICODE)
_STAMP = re.compile(r"(?<![\d:])(?:(\d{1,2}):)?(\d{1,2}):(\d{2})(?![\d:])")


@dataclass
class Message:
    time: float
    text: str
    author: str = ""


@dataclass
class ChatScores:
    rate: np.ndarray          # messages per second (raw, unshifted)
    total: np.ndarray         # z-scored, delay-corrected excitement
    kind: dict = field(default_factory=dict)  # per-bin laugh/hype/shock share
    messages: list = field(default_factory=list)


def classify(text: str) -> set[str]:
    t = text.lower()
    toks = set(_TOKEN.findall(t))
    kinds = set()
    for kind, words in HYPE.items():
        for w in words:
            if (" " in w and w in t) or w in toks or (len(w) > 3 and w in t):
                kinds.add(kind)
                break
    if re.search(r"(ha){2,}|(he){3,}|(ja){3,}|a{4,}h|lo{2,}l", t):
        kinds.add("laugh")
    if re.fullmatch(r"\s*\?+\s*", t) or "!!!" in t:
        kinds.add("shock")
    return kinds


# ---------------------------------------------------------------- loading

def load_file(path: str | Path) -> list[Message]:
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    items = []
    stripped = text.lstrip()
    if stripped.startswith("{") and "\n{" not in stripped[:2000] and '"comments"' in stripped[:5000]:
        data = json.loads(text)  # TwitchDownloader format
        for c in data.get("comments", []):
            body = (c.get("message") or {}).get("body", "")
            who = (c.get("commenter") or {}).get("display_name", "")
            items.append(Message(float(c.get("content_offset_seconds", 0)), body, who))
        return items
    if stripped.startswith("["):
        rows = json.loads(text)
    else:
        rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    for r in rows:
        t = r.get("time", r.get("time_in_seconds", r.get("content_offset_seconds")))
        if t is None:
            continue
        body = r.get("text", r.get("message", ""))
        if isinstance(body, dict):
            body = body.get("body", "")
        who = r.get("author", "")
        if isinstance(who, dict):
            who = who.get("name", "")
        items.append(Message(float(t), body or "", who or ""))
    return items


def save_jsonl(messages: list[Message], path: str | Path):
    with open(path, "w", encoding="utf-8") as fh:
        for m in messages:
            fh.write(json.dumps({"time": m.time, "text": m.text, "author": m.author},
                                ensure_ascii=False) + "\n")


def download_chat(url: str, log=print, cookies: str | None = None) -> list[Message]:
    """Download a stream's live-chat replay. Returns [] if it has none."""
    try:
        from chat_downloader import ChatDownloader
    except ImportError:
        log("  chat-downloader is not installed; skipping chat.")
        return []
    out = []
    try:
        chat = ChatDownloader(cookies=cookies).get_chat(url, message_groups=["messages"])
        for i, m in enumerate(chat):
            t = m.get("time_in_seconds")
            if t is None:
                continue
            author = (m.get("author") or {}).get("name", "")
            out.append(Message(float(t), m.get("message") or "", author))
            if i and i % 20000 == 0:
                log(f"  ...{i:,} chat messages")
    except Exception as e:  # no replay, private, unsupported site, etc.
        log(f"  No chat replay available ({type(e).__name__}: {e}).")
    return out


def comment_timestamp_mentions(comments: list[dict], duration: float) -> list[Message]:
    """Turn 'at 1:23:45 he loses it' style comments into point messages."""
    out = []
    for c in comments:
        text = c.get("text") or ""
        weight = 1 + min(int(c.get("like_count") or 0), 500) ** 0.5 / 4
        for h, m, s in _STAMP.findall(text):
            t = int(h or 0) * 3600 + int(m) * 60 + int(s)
            if 0 <= t <= duration:
                # Represent liked comments as several votes.
                for _ in range(int(round(weight))):
                    out.append(Message(float(t), text, c.get("author", "")))
    return out


# ---------------------------------------------------------------- scoring

def score(messages: list[Message], n_bins: int, delay: float = 6.0,
          window_s: float = 10.0, baseline_s: float = 600.0) -> ChatScores:
    count = np.zeros(n_bins)
    weighted = np.zeros(n_bins)
    kinds = {k: np.zeros(n_bins) for k in HYPE}
    for m in messages:
        i = int(m.time / HOP)
        if not 0 <= i < n_bins:
            continue
        ks = classify(m.text)
        count[i] += 1
        weighted[i] += 1 + 0.75 * len(ks)  # hype messages count more
        for k in ks:
            kinds[k][i] += 1

    rate = smooth(count, int(2 / HOP)) / HOP
    if not messages:
        return ChatScores(rate, np.zeros(n_bins), {k: np.zeros(n_bins) for k in HYPE}, [])

    win = int(window_s / HOP)
    activity = rolling_sum(weighted, win)
    z = local_z(np.log1p(activity), int(baseline_s / HOP))
    d = int(delay / HOP)
    total = np.clip(shift(z, d), 0, 8)
    share = {k: shift(rolling_sum(v, win), d) / np.maximum(shift(rolling_sum(count, win), d), 1)
             for k, v in kinds.items()}
    return ChatScores(rate, total, share, messages)


def messages_between(messages: list[Message], start: float, end: float) -> list[Message]:
    return [m for m in messages if start <= m.time <= end]
