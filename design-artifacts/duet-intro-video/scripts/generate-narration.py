#!/usr/bin/env python3
"""Generate local English narration and measured subtitles from the editable script."""

import argparse
import json
import math
from pathlib import Path
import shutil
import subprocess
import tempfile
import textwrap


ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--voice", help="An installed macOS say voice, e.g. Daniel")
    parser.add_argument("--rate", type=int, help="Narrator words per minute")
    args = parser.parse_args()
    source = json.loads((ROOT / "script/narration.json").read_text())
    voice = args.voice or source["voice"]
    rate = args.rate or source["rate"]
    validate(source, rate)
    output = ROOT / "public/voiceover"
    output.mkdir(parents=True, exist_ok=True)
    cues, chapter_reports = [], []
    index = 0
    with tempfile.TemporaryDirectory(prefix="duet-voice-") as temporary:
        for chapter_index, chapter in enumerate(source["chapters"]):
            chapter_cues = []
            for line in chapter["lines"]:
                index += 1
                cue_id = f"{index:03d}"
                raw = Path(temporary) / f"{cue_id}.aiff"
                audio = output / f"{cue_id}.mp3"
                subprocess.run(
                    ["say", "-v", voice, "-r", str(rate), "-o", str(raw), line],
                    check=True,
                )
                subprocess.run(
                    [
                        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                        "-i", str(raw), "-af", "loudnorm=I=-18:TP=-2:LRA=7",
                        "-ar", "48000", "-ac", "1", "-b:a", "160k", str(audio),
                    ],
                    check=True,
                )
                chapter_cues.append({
                    "id": cue_id,
                    "chapter": chapter_index,
                    "start": 0,
                    "duration": audio_duration(audio),
                    "text": line,
                    "audio": f"voiceover/{cue_id}.mp3",
                })
            speech_seconds = sum(c["duration"] for c in chapter_cues)
            available = chapter["end"] - chapter["start"]
            leading_pause = 1.0
            trailing_pause = 1.2 if chapter_index == len(source["chapters"]) - 1 else 0.8
            between = len(chapter_cues) - 1
            free = available - speech_seconds - leading_pause - trailing_pause
            if free < between * 0.2:
                raise ValueError(
                    f"Chapter {chapter['id']} does not fit its {available}s window: "
                    f"{speech_seconds:.3f}s speech. Shorten its lines or increase --rate."
                )
            gap = min(1.25, free / between) if between else 0
            current = chapter["start"] + leading_pause
            for cue in chapter_cues:
                cue["start"] = round(current, 3)
                current += cue["duration"] + gap
                cues.append(cue)
            chapter_reports.append({
                "chapter": chapter_index,
                "id": chapter["id"],
                "window": [chapter["start"], chapter["end"]],
                "speech_seconds": round(speech_seconds, 3),
                "gap_seconds": round(gap, 3),
                "last_audio_end": round(current - gap, 3),
            })
            print(f"{chapter['id']}: {speech_seconds:.2f}s speech, {gap:.2f}s gaps", flush=True)
    manifest = {
        "duration": source["duration"],
        "fps": source["fps"],
        "voice": f"macOS {voice} (en_GB), {rate} words per minute",
        "cues": cues,
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    srt = []
    for i, cue in enumerate(cues, 1):
        srt.append(
            f"{i}\n{srt_time(cue['start'])} --> {srt_time(cue['start'] + cue['duration'])}\n"
            + "\n".join(textwrap.wrap(cue["text"], width=49)) + "\n"
        )
    (output / "duet.en.srt").write_text("\n".join(srt))
    report = {
        "generated_by": "scripts/generate-narration.py",
        "engine": "macOS /usr/bin/say",
        "voice": voice,
        "rate": rate,
        "audio": "MP3, mono, 48 kHz, 160 kbit/s; loudnorm target -18 LUFS / -2 dBTP",
        "word_count": sum(len(c["text"].split()) for c in cues),
        "cue_count": len(cues),
        "chapters": chapter_reports,
        "last_audio_end": round(cues[-1]["start"] + cues[-1]["duration"], 3),
    }
    (output / "timing-report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"cues": len(cues), "last_audio_end": report["last_audio_end"]}))


def validate(source, rate):
    for command in ("say", "ffmpeg", "ffprobe"):
        if not shutil.which(command):
            raise SystemExit(f"Required command not found: {command}")
    if not 90 <= rate <= 220:
        raise ValueError("Use a narration rate between 90 and 220 words per minute")
    previous_end = 0
    for chapter in source["chapters"]:
        if chapter["start"] != previous_end or chapter["end"] <= chapter["start"]:
            raise ValueError("Chapter windows must be positive and adjacent")
        if not chapter["lines"] or any(not 20 <= len(line) <= 100 for line in chapter["lines"]):
            raise ValueError("Each subtitle cue must contain 20–100 characters")
        previous_end = chapter["end"]
    if previous_end != source["duration"]:
        raise ValueError("Chapter windows must match the film duration")


def audio_duration(path):
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    )
    return math.ceil(float(json.loads(result.stdout)["format"]["duration"]) * 1000) / 1000


def srt_time(seconds):
    total = round(seconds * 1000)
    hours, rest = divmod(total, 3600000)
    minutes, rest = divmod(rest, 60000)
    secs, milliseconds = divmod(rest, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{milliseconds:03d}"


if __name__ == "__main__":
    main()
