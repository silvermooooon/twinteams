#!/usr/bin/env python3
"""Generate the bilingual v2 voice tracks, measured frame timings and captions."""

import argparse
import json
import math
from pathlib import Path
import re
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT.parent / "ai-native-vision" / "v2"
ASSET_DIRECTORY = "voiceover-v2"
FPS = 30
OPENING_FRAMES = FPS
GAP_FRAMES = 11
CLOSING_FRAMES = 45
SETTINGS = {
    "zh-CN": ("Tingting", 160, "旁白", 38),
    "en": ("Daniel", 160, "Narration", 45),
}
ENGLISH_CONNECTORS = {
    "and", "as", "but", "for", "if", "or", "so", "that", "then",
    "when", "where", "whether", "which", "while", "with",
}
ENGLISH_OPEN_PHRASES = {"a", "an", "and", "are", "in", "is", "of", "or", "our", "the", "their", "to", "uses", "was"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--language", choices=SETTINGS, help="Generate only one language")
    parser.add_argument("--rate", type=int, help="Override the macOS speech rate")
    parser.add_argument("--intro-only", action="store_true",
                        help="Regenerate scene 00 while preserving the recorded story and its timing")
    args = parser.parse_args()
    for language in (args.language,) if args.language else SETTINGS:
        generate(language, args.rate, args.intro_only)


def generate(language, requested_rate, intro_only=False):
    voice, default_rate, narration_heading, line_limit = SETTINGS[language]
    rate = requested_rate or default_rate
    scenes = read_scenes(language, narration_heading)
    output = ROOT / "public" / ASSET_DIRECTORY / language
    output.parent.mkdir(parents=True, exist_ok=True)
    retained = read_retained_story(output, scenes, language, line_limit) if intro_only else {}
    chapters, cues = [], []
    frame, story_cue_count = 0, 0
    with tempfile.TemporaryDirectory(prefix=f".{language}-", dir=output.parent) as temporary:
        staging = Path(temporary)
        raw_audio = staging / "speech.aiff"
        for scene, title, narration in scenes:
            if scene in retained:
                chapter, chapter_cues = retained[scene]
                offset = frame - chapter["start"]
                chapters.append({**chapter, "start": frame})
                cues.extend({**cue, "start": cue["start"] + offset} for cue in chapter_cues)
                frame += chapter["duration"]
                print(f"{language} {scene:02d}: preserved {len(chapter_cues)} cues, "
                      f'{chapter["duration"] / FPS:.2f}s', flush=True)
                continue
            start = frame
            frame += OPENING_FRAMES
            captions = split_narration(narration, language, line_limit)
            if re.sub(r"\s+", "", "".join(text for text, _ in captions)) != re.sub(r"\s+", "", narration):
                raise ValueError(f"{language} scene {scene}: caption splits changed the narration")
            for index, (text, caption) in enumerate(captions):
                if index:
                    frame += GAP_FRAMES
                if scene == 0:
                    asset = f"intro-{index + 1:03d}.mp3"
                else:
                    story_cue_count += 1
                    asset = f"{story_cue_count:03d}.mp3"
                duration = synthesize(text, voice, rate, raw_audio, staging / asset)
                cues.append({
                    "scene": scene,
                    "start": frame,
                    "duration": duration,
                    "text": caption,
                    "audio": f"{ASSET_DIRECTORY}/{language}/{asset}",
                })
                frame += duration
            frame += CLOSING_FRAMES
            chapters.append({"id": scene, "title": title, "start": start, "duration": frame - start})
            print(f"{language} {scene:02d}: {len(captions)} cues, {(frame - start) / FPS:.2f}s", flush=True)
        raw_audio.unlink()
        write_metadata(staging, frame, chapters, cues)
        output.mkdir(exist_ok=True)
        for asset in staging.iterdir():
            asset.replace(output / asset.name)
        expected = {Path(cue["audio"]).name for cue in cues}
        for obsolete in (*output.glob("[0-9][0-9][0-9].mp3"), *output.glob("intro-[0-9][0-9][0-9].mp3")):
            if obsolete.name not in expected:
                obsolete.unlink()
    print(f"{language}: {voice}, rate {rate}, {len(cues)} cues, {frame / FPS:.2f}s", flush=True)


def read_scenes(language, narration_heading):
    script = (SCRIPTS / f"video-script.{language}.md").read_text(encoding="utf-8")
    scenes = []
    for number, title, body in re.findall(r"(?ms)^## (\d{2}) ([^\n]+)\n(.*?)(?=^## |\Z)", script):
        section = re.search(rf"(?ms)^### {narration_heading}\s*$\n(.*?)(?=^### |\Z)", body)
        if not section or not section.group(1).strip():
            raise ValueError(f"{language} scene {number}: missing narration")
        scenes.append((int(number), title, " ".join(section.group(1).split())))
    if [scene for scene, _, _ in scenes] != list(range(13)):
        raise ValueError(f"{language}: expected architecture scene 00 followed by scenes 01–12")
    return scenes


def read_retained_story(output, scenes, language, line_limit):
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    chapters = [chapter for chapter in manifest["chapters"] if chapter["id"] > 0]
    if manifest["fps"] != FPS or [chapter["id"] for chapter in chapters] != list(range(1, 13)):
        raise ValueError(f"{language}: existing story must contain ordered scenes 01–12 at {FPS} fps")
    retained, index = {}, 0
    position = chapters[0]["start"]
    for chapter, (scene, title, narration) in zip(chapters, scenes[1:]):
        cues = [cue for cue in manifest["cues"] if cue["scene"] == scene]
        captions = split_narration(narration, language, line_limit)
        if (chapter["title"], chapter["start"]) != (title, position) or len(cues) != len(captions):
            raise ValueError(f"{language} scene {scene}: existing story differs; regenerate all narration")
        end = position + OPENING_FRAMES
        for local_index, (cue, (_, caption)) in enumerate(zip(cues, captions)):
            index += 1
            audio = f"{ASSET_DIRECTORY}/{language}/{index:03d}.mp3"
            start = end + (GAP_FRAMES if local_index else 0)
            if ((cue["text"], cue["audio"], cue["start"]) != (caption, audio, start)
                    or type(cue["duration"]) is not int or cue["duration"] <= 0
                    or not (ROOT / "public" / audio).is_file()):
                raise ValueError(f"{language} scene {scene}: existing cue differs; regenerate all narration")
            end = start + cue["duration"]
        position += chapter["duration"]
        if position != end + CLOSING_FRAMES:
            raise ValueError(f"{language} scene {scene}: existing chapter timing is inconsistent")
        retained[scene] = (chapter, cues)
    return retained


def split_narration(narration, language, limit):
    sentence_end = r'[。！？；](?:[」”])?' if language == "zh-CN" else r'[.!?](?:[”"’])?(?=\s|$)'
    sentences = re.findall(rf".+?(?:{sentence_end}|$)", narration)
    captions = []
    for sentence in sentences:
        remaining = sentence.strip()
        while remaining:
            caption = layout_caption(remaining, language, limit)
            if caption:
                captions.append((remaining, caption))
                break
            target = len(remaining) / math.ceil(len(remaining) / (2 * limit - 4))
            candidates = []
            for left, right, pause in boundaries(remaining, language):
                if len(left) < limit / 2 or len(right) < limit / 3:
                    continue
                caption = layout_caption(left, language, limit)
                if caption:
                    candidates.append((abs(len(left) - target) - pause, left, right, caption))
            if not candidates:
                raise ValueError(f"Unable to split a readable {language} caption: {remaining!r}")
            _, spoken, remaining, caption = min(candidates)
            captions.append((spoken, caption))
    return captions


def boundaries(text, language):
    if language == "en":
        for match in re.finditer(r"\s+", text):
            left, right = text[:match.start()], text[match.end():]
            if left and right:
                connective = right.split(maxsplit=1)[0].lower()
                pause = 17 if left[-1] in ",;:—" else 8 if connective in ENGLISH_CONNECTORS else 0
                last_word = left.rsplit(maxsplit=1)[-1].lower()
                if last_word in ENGLISH_OPEN_PHRASES or (last_word == "group" and connective.startswith("conversation")):
                    pause -= 18
                yield left, right, pause
        return
    for index in range(1, len(text)):
        before, after = text[index - 1], text[index]
        if re.fullmatch(r"[A-Za-z0-9'-]{2}", before + after) or after in "，。！？、；：）」”":
            continue
        left, right = text[:index].rstrip(), text[index:].lstrip()
        if left and right:
            yield left, right, 17 if before in "，、；：" else 0


def layout_caption(text, language, limit):
    if len(text) <= limit:
        return text
    if len(text) > 2 * limit + 1:
        return None
    options = [
        (abs(len(left) - len(right)) - pause / 3, left, right)
        for left, right, pause in boundaries(text, language)
        if len(left) <= limit and len(right) <= limit
    ]
    if not options:
        return None
    _, left, right = min(options)
    return f"{left}\n{right}"


def synthesize(text, voice, rate, raw, output):
    subprocess.run(["say", "-v", voice, "-r", str(rate), "-o", str(raw), text], check=True)
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(raw),
        "-af", "loudnorm=I=-18:TP=-2:LRA=7", "-ar", "48000", "-ac", "1",
        "-b:a", "160k", str(output),
    ], check=True)
    duration = float(subprocess.check_output([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(output),
    ], text=True))
    if duration <= 0:
        raise ValueError(f"Empty narration clip for {text!r}")
    return math.ceil(duration * FPS)


def write_metadata(output, duration, chapters, cues):
    manifest = {"fps": FPS, "duration": duration, "chapters": chapters, "cues": cues}
    captions = []
    subtitles = []
    for index, cue in enumerate(cues, start=1):
        start_ms = round(cue["start"] * 1000 / FPS)
        end_ms = round((cue["start"] + cue["duration"]) * 1000 / FPS)
        text = cue["text"]
        captions.append({"text": text, "startMs": start_ms, "endMs": end_ms,
                         "timestampMs": None, "confidence": None})
        subtitles.append(f"{index}\n{srt_time(start_ms)} --> {srt_time(end_ms)}\n{text}\n")
    for name, data in (("manifest.json", manifest), ("captions.json", captions)):
        (output / name).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output / "duet-ai-native-v2.srt").write_text("\n".join(subtitles), encoding="utf-8")


def srt_time(milliseconds):
    seconds, milliseconds = divmod(milliseconds, 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"


if __name__ == "__main__":
    main()
