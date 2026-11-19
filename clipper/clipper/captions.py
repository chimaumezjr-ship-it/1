"""Captions: bold white words, thick black outline, two words at a time,
with the funniest word of each moment in yellow.

"Funniest" is scored from what we know about each word:
* how loud it was said compared with the rest of the clip (yelled words land)
* whether chat echoed it around the moment ("BRO", "NO WAY" spammed back)
* reaction/exclamation vocabulary (no, what, bro, wait, swears, laugh words)
* stretched spellings and punctuation ("nooo", "what?!")
Filler words never get highlighted. The clip's top word is always yellow, and
other captions get a yellow word only when one scores close to it.
"""
from __future__ import annotations

import re

W, H = 1080, 1920
WHITE, YELLOW, BLACK = "&H00FFFFFF", "&H0000E5FF", "&H00000000"

STOP = set("""a an the and or but so to of in on at for with from by as is are was were be been
am i im i'm you your it its it's this that these those he she they we me my our his her their them
him us do does did have has had just like um uh yeah okay ok then there here if when can could
would will gonna got get go going really very kind sort of out up about into""".split())
REACTION = set("""no nope nah what wait bro dude bruh why how yes yo oh omg god jesus holy wow
insane crazy dead dying help stop run scream screaming hilarious funny lol lmao haha hahaha
ha bro's ew yikes damn hell shit fuck fucking wtf ass bitch dang shut nooo whaaat noo""".split())


def _norm(w: str) -> str:
    return re.sub(r"[^\w']", "", w.lower())


def funny_scores(words: list[dict], loudness: list[float] | None = None,
                 chat_words: set[str] | None = None) -> list[float]:
    chat_words = chat_words or set()
    if loudness:
        med = sorted(loudness)[len(loudness) // 2]
    scores = []
    for i, w in enumerate(words):
        raw = w["word"]
        n = _norm(raw)
        if not n or (n in STOP and n not in REACTION):
            scores.append(0.0)
            continue
        s = 1.0 + min(len(n), 8) * 0.05
        if n in REACTION or re.sub(r"(.)\1+", r"\1", n) in REACTION:
            s += 1.5
        if re.search(r"(.)\1{2,}", n):       # nooo, whaaat
            s += 1.0
        if re.search(r"[!?]", raw):
            s += 0.7
        if n in chat_words:
            s += 1.5
        if loudness:
            s += max(0.0, min(3.0, (loudness[i] - med) / 4.0))  # ~+1 per 4 dB louder
        scores.append(s)
    return scores


def pick_highlights(groups: list[list[int]], scores: list[float], ratio: float = 0.7) -> set[int]:
    if not scores or max(scores) <= 0:
        return set()
    top = max(scores)
    out = {scores.index(top)}
    for g in groups:
        best = max(g, key=lambda i: scores[i])
        if scores[best] >= ratio * top and scores[best] > 1.5:
            out.add(best)
    return out


def _ts(t: float) -> str:
    cs = int(round(max(0.0, t) * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace("{", "(").replace("}", ")")


def group_words(words: list[dict], max_words: int = 2, max_chars: int = 18,
                max_gap: float = 0.6) -> list[list[int]]:
    """Indices of words grouped into captions (two words at a time by default)."""
    groups, cur = [], []
    for i, w in enumerate(words):
        if cur:
            prev = words[cur[-1]]
            chars = sum(len(words[j]["word"]) + 1 for j in cur) + len(w["word"])
            if (len(cur) >= max_words or chars > max_chars or w["start"] - prev["end"] > max_gap
                    or re.search(r"[.!?,]$", prev["word"])):
                groups.append(cur)
                cur = []
        cur.append(i)
    if cur:
        groups.append(cur)
    return groups


def build_ass(words: list[dict], duration: float, font: str = "Arial Black",
              font_size: int = 110, y: int = 1250, uppercase: bool = True,
              loudness: list[float] | None = None, chat_words: set[str] | None = None) -> str:
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{font},{font_size},{WHITE},{WHITE},{BLACK},&H96000000,-1,0,0,0,100,100,1,0,1,10,3,5,60,60,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    groups = group_words(words)
    yellow = pick_highlights(groups, funny_scores(words, loudness, chat_words))
    lines = []
    for gi, g in enumerate(groups):
        start = words[g[0]]["start"]
        nxt = words[groups[gi + 1][0]]["start"] if gi + 1 < len(groups) else duration
        end = min(words[g[-1]]["end"] + 0.25, nxt, duration)  # linger briefly, never overlap
        if end <= start:
            continue
        parts = []
        for i in g:
            t = _esc(words[i]["word"].upper() if uppercase else words[i]["word"])
            parts.append(r"{\c" + YELLOW + "&}" + t + r"{\c" + WHITE + "&}" if i in yellow else t)
        pop = r"\fscx80\fscy80\t(0,80,\fscx100\fscy100)"
        lines.append(f"Dialogue: 0,{_ts(start)},{_ts(end)},Cap,,0,0,0,,"
                     + "{\\pos(" + f"{W // 2},{y}" + ")" + pop + "}" + " ".join(parts))
    return header + "\n".join(lines) + "\n"


def build_srt(words: list[dict]) -> str:
    out = []
    for n, g in enumerate(group_words(words, max_words=7, max_chars=42), 1):
        def f(t):
            ms = int(round(t * 1000))
            return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"
        out.append(f"{n}\n{f(words[g[0]]['start'])} --> {f(words[g[-1]]['end'])}\n"
                   + " ".join(words[i]["word"] for i in g) + "\n")
    return "\n".join(out)
