#!/usr/bin/env python3
"""Verify generated narration, subtitles, and an optional encoded film."""

import argparse
from fractions import Fraction
import json
from pathlib import Path
import re
import subprocess


ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", type=Path)
    parser.add_argument("--width", type=int, default=1920)
    parser.add_argument("--height", type=int, default=1080)
    parser.add_argument("--language", choices=("en", "zh-CN"), default="en")
    args = parser.parse_args()
    suffix = "" if args.language == "en" else ".zh-CN"
    directory = ROOT / "public" / ("voiceover" if args.language == "en" else "voiceover-zh-CN")
    source = json.loads((ROOT / f"script/narration{suffix}.json").read_text())
    manifest = json.loads((directory / "manifest.json").read_text())
    report = verify_narration(source, manifest, directory / f"duet.{args.language}.srt", args.language)
    if args.video:
        report["video"] = verify_video(args.video, source, manifest, args.width, args.height)
    else:
        report["video"] = "not checked; pass --video to validate an encoded file"
    print(json.dumps(report, indent=2))


def verify_narration(source, manifest, srt_path, language):
    assert manifest["duration"] == source["duration"]
    assert manifest["fps"] == source["fps"]
    expected = [(i, line) for i, c in enumerate(source["chapters"]) for line in c["lines"]]
    cues = manifest["cues"]
    assert [(c["chapter"], c["text"]) for c in cues] == expected, "Narration differs from source"
    assert len({c["id"] for c in cues}) == len(cues), "Duplicate cue IDs"
    if language == "zh-CN":
        english = json.loads((ROOT / "public/voiceover/manifest.json").read_text())
        english_source = json.loads((ROOT / "script/narration.json").read_text())
        assert (source["duration"], source["fps"]) == (english["duration"], english["fps"]), "Review timeline differs from English final"
        assert [(c["id"], c["start"], c["end"]) for c in source["chapters"]] == [
            (c["id"], c["start"], c["end"]) for c in english_source["chapters"]
        ], "Review chapter windows differ from English final"
        assert [(c["id"], c["chapter"], c["start"]) for c in cues] == [
            (c["id"], c["chapter"], c["start"]) for c in english["cues"]
        ], "Review cue IDs, chapters or start times differ from English final"
    srt = srt_path.read_text().strip().split("\n\n")
    assert len(srt) == len(cues), "Subtitle count differs from narration"
    previous_end = 0
    speech_seconds = 0
    for index, (cue, block) in enumerate(zip(cues, srt), 1):
        chapter = source["chapters"][cue["chapter"]]
        end = cue["start"] + cue["duration"]
        assert chapter["start"] <= cue["start"] < end <= chapter["end"], f"Cue {cue['id']} crosses chapter"
        assert cue["start"] >= previous_end - 0.001, f"Cue {cue['id']} overlaps previous speech"
        media = probe(ROOT / "public" / cue["audio"])
        assert any(s["codec_type"] == "audio" for s in media["streams"]), f"No audio in {cue['audio']}"
        measured = float(media["format"]["duration"])
        assert measured > 0 and abs(measured - cue["duration"]) <= 0.002, f"Stale measured duration: {cue['id']}"
        lines = block.splitlines()
        assert int(lines[0]) == index, "Subtitle numbering differs"
        start_text, end_text = lines[1].split(" --> ")
        assert abs(srt_seconds(start_text) - cue["start"]) <= 0.001
        assert abs(srt_seconds(end_text) - end) <= 0.001
        separator = "" if language == "zh-CN" else " "
        assert separator.join(" ".join(lines[2:]).split()) == separator.join(cue["text"].split()), f"Subtitle wording differs: {cue['id']}"
        previous_end = end
        speech_seconds += measured
    return {
        "language": language,
        "narration": "verified against source, measured audio and SRT",
        "cue_count": len(cues),
        "speech_seconds": round(speech_seconds, 3),
        "last_cue_end": round(previous_end, 3),
        "duration_seconds": source["duration"],
        "fps": source["fps"],
    }


def verify_video(path, source, manifest, width, height):
    media = probe(path)
    video = next(s for s in media["streams"] if s["codec_type"] == "video")
    audio = next(s for s in media["streams"] if s["codec_type"] == "audio")
    assert (video["width"], video["height"]) == (width, height), "Wrong video dimensions"
    assert Fraction(video["avg_frame_rate"]) == source["fps"], "Wrong frame rate"
    expected_frames = source["duration"] * source["fps"]
    assert int(video["nb_frames"]) == expected_frames, "Wrong encoded frame count"
    assert abs(float(media["format"]["duration"]) - source["duration"]) < 0.1, "Wrong encoded duration"
    levels = audio_levels(path)
    assert levels["peak_db"] > -50, "Encoded audio is silent or nearly silent"
    cues = manifest["cues"]
    spoken_windows = []
    for cue in [cues[0], cues[len(cues) // 2], cues[-1]]:
        measured = audio_levels(path, cue["start"], cue["duration"])
        # The quiet score alone cannot satisfy the narrated-window threshold.
        assert measured["mean_db"] > -30, f"Narration missing or too quiet near cue {cue['id']}"
        spoken_windows.append({"cue": cue["id"], "start": cue["start"], **measured})
    return {
        "path": str(path.resolve()),
        "width": width,
        "height": height,
        "frames": expected_frames,
        "video_codec": video["codec_name"],
        "audio_codec": audio["codec_name"],
        "duration_seconds": float(media["format"]["duration"]),
        "peak_audio_db": levels["peak_db"],
        "spoken_windows": spoken_windows,
        "visual_playback_review": "separate manual inspection required",
    }


def audio_levels(path, start=None, duration=None):
    command = ["ffmpeg", "-hide_banner"]
    if start is not None:
        command += ["-ss", str(start)]
    command += ["-i", str(path)]
    if duration is not None:
        command += ["-t", str(duration)]
    command += ["-vn", "-af", "volumedetect", "-f", "null", "-"]
    output = subprocess.run(command, capture_output=True, text=True, check=True).stderr
    mean = re.search(r"mean_volume: (-?(?:[\d.]+|inf)) dB", output)
    peak = re.search(r"max_volume: (-?(?:[\d.]+|inf)) dB", output)
    assert mean and peak, "Could not measure encoded audio levels"
    return {"mean_db": float(mean[1]), "peak_db": float(peak[1])}


def probe(path):
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    )
    return json.loads(result.stdout)


def srt_seconds(value):
    hours, minutes, seconds, milliseconds = map(int, re.split(r"[:,]", value))
    return hours * 3600 + minutes * 60 + seconds + milliseconds / 1000


if __name__ == "__main__":
    main()
