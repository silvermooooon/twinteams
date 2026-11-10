#!/usr/bin/env python3
"""Verify the v2 narration assets and an optional encoded film."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from fractions import Fraction
import json
import math
from pathlib import Path
import re
import runpy
import subprocess


ROOT = Path(__file__).resolve().parents[1]
NARRATION = runpy.run_path(str(ROOT / "scripts" / "generate-v2-narration.py"))
FPS = NARRATION["FPS"]
LANGUAGES = NARRATION["SETTINGS"]
ASSETS = ROOT / "public" / NARRATION["ASSET_DIRECTORY"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", type=Path, help="Verify an encoded 1080p film")
    parser.add_argument("--language", choices=LANGUAGES, help="Select one language; required with --video")
    args = parser.parse_args()
    if args.video and not args.language:
        parser.error("--video requires --language")

    reports = {}
    try:
        for language in (args.language,) if args.language else LANGUAGES:
            manifest = json.loads((ASSETS / language / "manifest.json").read_text(encoding="utf-8"))
            report = verify_narration(language, manifest)
            if args.video:
                report["video"] = verify_video(args.video, manifest)
            reports[language] = report
    except (KeyError, OSError, ValueError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Verification failed: {error}\n")
    print(json.dumps(reports, ensure_ascii=False, indent=2))


def verify_narration(language, manifest):
    directory = ASSETS / language
    _, _, heading, line_limit = LANGUAGES[language]
    source = NARRATION["read_scenes"](language, heading)
    chapters, cues = manifest["chapters"], manifest["cues"]
    require(manifest["fps"] == FPS, "Wrong narration frame rate")
    require(len(chapters) == len(source), "Chapter count differs from Markdown")
    require([cue["scene"] for cue in cues] == sorted(cue["scene"] for cue in cues),
            "Narration cues are not in chapter order")
    position, count = 0, 0
    separator = "" if language == "zh-CN" else " "
    for chapter, (scene, title, narration) in zip(chapters, source):
        label = f"{language} chapter {scene}"
        require((chapter["id"], chapter["title"], chapter["start"]) == (scene, title, position),
                f"{label}: chapter metadata or boundary differs from source")
        chapter_cues = [cue for cue in cues if cue["scene"] == scene]
        count += len(chapter_cues)
        actual = separator.join(" ".join(cue["text"] for cue in chapter_cues).split())
        require(actual == separator.join(narration.split()), f"{label}: narration differs from Markdown")
        end = position + NARRATION["OPENING_FRAMES"]
        for index, cue in enumerate(chapter_cues):
            start = end + (NARRATION["GAP_FRAMES"] if index else 0)
            require(type(cue["start"]) is int and cue["start"] == start,
                    f"{label}, cue {index + 1}: invalid start or overlapping cue")
            require(type(cue["duration"]) is int and cue["duration"] > 0,
                    f"{label}, cue {index + 1}: invalid frame duration")
            end = start + cue["duration"]
        position += chapter["duration"]
        require(position == end + NARRATION["CLOSING_FRAMES"], f"{label}: incorrect chapter end")
    require(count == len(cues), "A cue refers to a missing chapter")
    require(type(manifest["duration"]) is int and position == manifest["duration"],
            "Total frame count differs from chapter boundaries")

    paths, expected_captions, expected_srt = [], [], []
    intro_count, story_count = 0, 0
    for index, cue in enumerate(cues, start=1):
        if cue["scene"] == 0:
            intro_count += 1
            filename = f"intro-{intro_count:03d}.mp3"
        else:
            story_count += 1
            filename = f"{story_count:03d}.mp3"
        relative = f'{NARRATION["ASSET_DIRECTORY"]}/{language}/{filename}'
        require(cue["audio"] == relative, f"Cue {index} references the wrong MP3")
        paths.append(ROOT / "public" / relative)
        lines = cue["text"].splitlines()
        require(1 <= len(lines) <= 2 and all(0 < len(line) <= line_limit for line in lines),
                f"Cue {index} exceeds subtitle line limits")
        start_ms = round(cue["start"] * 1000 / FPS)
        end_ms = round((cue["start"] + cue["duration"]) * 1000 / FPS)
        text = cue["text"]
        expected_captions.append({
            "text": text, "startMs": start_ms, "endMs": end_ms, "timestampMs": None, "confidence": None,
        })
        srt_time = NARRATION["srt_time"]
        expected_srt.append(f"{index}\n{srt_time(start_ms)} --> {srt_time(end_ms)}\n{text}")
    captions = json.loads((directory / "captions.json").read_text(encoding="utf-8"))
    require(captions == expected_captions, "Caption JSON differs from cue text or timing")
    srt = (directory / "duet-ai-native-v2.srt").read_text(encoding="utf-8")
    require(srt.strip() == "\n\n".join(expected_srt), "SRT differs from cue text or timing")
    require(set(directory.glob("*.mp3")) == set(paths), "Referenced and available MP3 sets differ")

    with ThreadPoolExecutor(max_workers=4) as pool:
        measured = list(pool.map(mp3_seconds, paths))
    for index, (cue, seconds) in enumerate(zip(cues, measured), start=1):
        require(math.ceil(seconds * FPS) == cue["duration"], f"Cue {index}: MP3 duration differs from manifest")
    return {
        "chapters": len(chapters),
        "cues": len(cues),
        "fps": FPS,
        "frames": position,
        "duration_seconds": round(position / FPS, 3),
        "measured_speech_seconds": round(sum(measured), 3),
    }


def mp3_seconds(path):
    info = probe(path, "stream=codec_name,sample_rate,channels:format=duration")
    require(info["streams"] == [{"codec_name": "mp3", "sample_rate": "48000", "channels": 1}],
            f"{path.name}: expected one mono, 48 kHz MP3 stream")
    seconds = float(info["format"]["duration"])
    require(seconds > 0, f"{path.name}: empty audio")
    return seconds


def verify_video(path, manifest):
    info = probe(path, "stream=codec_name,codec_type,width,height,avg_frame_rate,nb_frames:format=duration")
    video = [stream for stream in info["streams"] if stream["codec_type"] == "video"]
    audio = [stream for stream in info["streams"] if stream["codec_type"] == "audio"]
    require(len(video) == len(audio) == 1, "Expected one video and one audio stream")
    video, audio = video[0], audio[0]
    require((video["codec_name"], audio["codec_name"]) == ("h264", "aac"), "Expected H.264 and AAC")
    require((video["width"], video["height"]) == (1920, 1080), "Expected 1920 × 1080 dimensions")
    require(Fraction(video["avg_frame_rate"]) == FPS, "Wrong encoded frame rate")
    frames = int(video["nb_frames"])
    require(frames == manifest["duration"], "Wrong encoded frame count")
    seconds = float(info["format"]["duration"])
    # AAC padding and MP4 timescale rounding can extend the container by less than two frames.
    require(abs(seconds - frames / FPS) <= 2 / FPS, "Wrong encoded duration")

    windows = []
    for chapter in manifest["chapters"]:
        chapter_id = chapter["id"]
        cue = max((cue for cue in manifest["cues"] if cue["scene"] == chapter_id),
                  key=lambda cue: cue["duration"])
        window_frames = min(cue["duration"], 4 * FPS)
        start = (cue["start"] + (cue["duration"] - window_frames) // 2) / FPS
        mean_db = measure_narration(path, start, window_frames / FPS)
        # The quiet ambient score alone is below this threshold.
        require(mean_db > -30, f"Chapter {chapter_id}: narration missing or too quiet")
        windows.append({"chapter": chapter_id, "start_seconds": round(start, 3), "mean_db": mean_db})
    return {
        "path": str(path.resolve()),
        "resolution": "1920x1080",
        "fps": FPS,
        "frames": frames,
        "duration_seconds": seconds,
        "video_codec": video["codec_name"],
        "audio_codec": audio["codec_name"],
        "narration_windows": windows,
    }


def measure_narration(path, start, duration):
    command = [
        "ffmpeg", "-hide_banner", "-nostats", "-ss", f"{start:.6f}", "-i", str(path),
        "-t", f"{duration:.6f}", "-map", "0:a:0", "-af", "volumedetect", "-f", "null", "-",
    ]
    output = subprocess.run(command, capture_output=True, text=True, check=True).stderr
    match = re.search(r"mean_volume: (-?(?:[\d.]+|inf)) dB", output)
    require(match is not None, f"Could not measure narration at {start:.3f}s")
    return float(match[1])


def probe(path, entries):
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", entries, "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    )
    return json.loads(result.stdout)


def require(condition, message):
    if not condition:
        raise ValueError(message)


if __name__ == "__main__":
    main()
