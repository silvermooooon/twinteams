#!/usr/bin/env python3
"""Create an original, quiet tonal bed; no external recordings or sample packs."""

import json
from pathlib import Path
import subprocess


root = Path(__file__).resolve().parents[1]
duration = json.loads((root / "script/narration.json").read_text())["duration"]
output = root / "public/voiceover/ambience.mp3"
# A slowly breathing open chord leaves the speech frequency range uncluttered.
tones = (
    "0.035*(sin(2*PI*146.832*t)+0.6*sin(2*PI*220*t)"
    "+0.4*sin(2*PI*293.665*t)+0.25*sin(2*PI*440*t))"
    "*(0.65+0.15*sin(2*PI*0.037*t))"
)
subprocess.run(
    [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", f"aevalsrc={tones}:s=48000:d={duration}",
        "-af", f"loudnorm=I=-38:TP=-9:LRA=5,afade=t=in:d=3,afade=t=out:st={duration-5}:d=5",
        "-ar", "48000", "-ac", "1", "-b:a", "128k", str(output),
    ],
    check=True,
)
print(output)
