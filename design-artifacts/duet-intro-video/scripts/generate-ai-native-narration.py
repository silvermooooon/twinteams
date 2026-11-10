#!/usr/bin/env python3
"""Render the AI Native script with macOS Mandarin speech and measured timing."""

import json
import math
from pathlib import Path
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
FPS = 30
VOICE = "Tingting"
RATE = "170"


def main():
    script = (ROOT / "script/ai-native.zh-CN.md").read_text()
    scenes = [
        [line[2:] for line in block.splitlines() if line.startswith("- ")]
        for block in script.split("\n## ")[1:]
    ]
    output = ROOT / "public/voiceover-ai-native"
    output.mkdir(exist_ok=True)
    chapters, cues = [], []
    frame = 0
    with tempfile.TemporaryDirectory(prefix="duet-ai-native-") as temp:
        raw = Path(temp) / "speech.aiff"
        for scene, lines in enumerate(scenes):
            start = frame
            frame += 23
            for line in lines:
                asset = f"{len(cues) + 1:03d}.mp3"
                duration = synthesize(line, raw, output / asset)
                cues.append({"start": frame, "duration": duration, "text": line,
                             "audio": f"voiceover-ai-native/{asset}"})
                frame += duration + 11
            frame += 25 if scene < len(scenes) - 1 else 57
            chapters.append({"start": start, "duration": frame - start})
    manifest = {"fps": FPS, "duration": frame, "chapters": chapters, "cues": cues}
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    )
    print(f"{len(cues)} cues, {len(chapters)} scenes, {frame / FPS:.2f} seconds")


def synthesize(line, raw, output):
    subprocess.run(["say", "-v", VOICE, "-r", RATE, "-o", str(raw), line], check=True)
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(raw),
        "-af", "loudnorm=I=-18:TP=-2:LRA=7", "-ar", "48000", "-ac", "1",
        "-b:a", "160k", str(output),
    ], check=True)
    duration = subprocess.check_output([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(output),
    ], text=True)
    return math.ceil(float(duration) * FPS)


if __name__ == "__main__":
    main()
