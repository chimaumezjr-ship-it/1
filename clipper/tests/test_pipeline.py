"""End-to-end test on a synthetic 'stream' (no network, no Whisper download).

Run:  python -m pytest tests -q      (or: python tests/test_pipeline.py)
"""
import json
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from clipper import cli, transcribe  # noqa: E402

SR = 16000
DUR = 360
SCREAM_T, LAUGH_T, CHAT_ONLY_T = 90, 200, 290


def _make_stream(tmp: Path):
    rng = np.random.default_rng(0)
    t = np.arange(DUR * SR) / SR
    # "Talking": quiet filtered noise in irregular syllable bursts and pauses.
    noise = np.convolve(rng.standard_normal(len(t)), np.ones(8) / 8, mode="same")
    env = np.zeros(len(t))
    pos = 0
    while pos < len(t):
        n = int(SR * rng.uniform(0.08, 0.35))
        env[pos:pos + n] = rng.uniform(0.2, 1.0) if rng.random() < 0.75 else 0.02
        pos += n
    env = np.convolve(env, np.ones(400) / 400, mode="same")
    sig = noise * env * 0.06

    def win(c, w):
        m = (t >= c) & (t < c + w)
        return m

    # Scream: loud, bright (1.5-3 kHz band + high tone), 2.5 s.
    m = win(SCREAM_T, 2.5)
    bright = np.diff(np.r_[0, rng.standard_normal(m.sum())])
    sig[m] = 0.35 * bright + 0.25 * np.sin(2 * np.pi * 1400 * t[m] * (1 + 0.05 * np.sin(6 * t[m])))
    # Laughter: louder noise pulsed at 5 Hz ("ha-ha-ha"), 3 s.
    m = win(LAUGH_T, 3.0)
    pulse = np.clip(np.sin(2 * np.pi * 5 * t[m]), 0, None) ** 2
    sig[m] = 0.45 * rng.standard_normal(m.sum()) * pulse

    pcm = (np.clip(sig, -1, 1) * 32767).astype(np.int16)
    wav = tmp / "a.wav"
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR); w.writeframes(pcm.tobytes())
    video = tmp / "stream.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    f"testsrc2=size=1280x720:rate=30:duration={DUR}", "-i", str(wav),
                    "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest",
                    str(video)], check=True)

    chat = []
    for s in range(DUR):
        for _ in range(rng.poisson(0.8)):
            chat.append({"time": s + rng.random(), "text": rng.choice(["hi", "nice", "gg", "lol ok", "what game"])})
    for c, words in ((SCREAM_T, ["?????", "WTF", "monkaW", "JUMPSCARE"]),
                     (LAUGH_T, ["KEKW", "LMAOOO", "OMEGALUL", "💀💀"]),
                     (CHAT_ONLY_T, ["W", "POGGERS", "clip it", "no way"])):
        for _ in range(140):
            chat.append({"time": c + 4 + rng.random() * 10, "text": str(rng.choice(words))})
    chat_path = tmp / "chat.jsonl"
    chat_path.write_text("\n".join(json.dumps(m) for m in chat))
    return video, chat_path


def _fake_transcribe(audio_path, start, duration, model_size="small", language=None):
    words = "oh my god no way that was insane chat did you see that".split()
    return [{"start": 1 + i * 0.4, "end": 1.35 + i * 0.4, "word": w} for i, w in enumerate(words)]


def test_end_to_end(tmp_path=None):
    tmp = Path(tmp_path or ROOT / "tests" / "_out")
    tmp.mkdir(parents=True, exist_ok=True)
    video, chat = _make_stream(tmp)
    transcribe.transcribe = _fake_transcribe  # no model download in tests

    out = tmp / "clips"
    # --no-ai-audio: synthetic tones are not real laughter, so test the heuristics
    # (and stay offline). The YAMNet path is exercised on real recordings.
    rc = cli.main([str(video), "--chat", str(chat), "-n", "3", "-o", str(out), "--no-ai-audio",
                   "--cache", str(tmp / "cache"), "--preset", "ultrafast"])
    assert rc == 0
    report = json.loads(next(out.glob("*/moments.json")).read_text())
    peaks = sorted(m["peak"] for m in report["moments"])
    for target in (SCREAM_T, LAUGH_T, CHAT_ONLY_T):
        assert any(abs(p - target) < 8 for p in peaks), (target, peaks)
    for m in report["moments"]:
        assert m["start"] < m["peak"] < m["end"]
        assert 15 * 0.8 <= m["duration"] <= 60
        probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                                "stream=width,height", "-of", "csv=p=0", m["file"]],
                               capture_output=True, text=True, check=True).stdout.strip()
        assert probe == "1080,1920", probe
    scream = min(report["moments"], key=lambda m: abs(m["peak"] - SCREAM_T))
    laugh = min(report["moments"], key=lambda m: abs(m["peak"] - LAUGH_T))
    assert "shouting/screaming" in scream["reasons"], scream["reasons"]
    assert "laughter" in laugh["reasons"], laugh["reasons"]
    print(json.dumps(report["moments"], indent=1)[:3000])


if __name__ == "__main__":
    test_end_to_end()
    print("OK")
