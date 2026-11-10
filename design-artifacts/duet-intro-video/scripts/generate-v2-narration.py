#!/usr/bin/env python3
"""Generate the bilingual v2 voice tracks, measured frame timings and captions."""

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT.parent / "ai-native-vision" / "v2"
ASSET_DIRECTORY = "voiceover-v2"
FPS = 30
OPENING_FRAMES = FPS
GAP_FRAMES = 11
CLOSING_FRAMES = 45
DEFAULT_MODEL = "gpt-4o-mini-tts"
DEFAULT_VOICE = "marin"
CACHE = ROOT / "out" / "v2" / "tts-cache"
SETTINGS = {
    "zh-CN": (DEFAULT_VOICE, 1.0, "旁白", 38),
    "en": (DEFAULT_VOICE, 1.0, "Narration", 45),
}
INSTRUCTIONS = {
    "zh-CN": (
        "用标准普通话，为一部介绍企业个人 AI 协作的视频配音。声音自然、清晰、温和专业，"
        "像向同事解释一个容易理解的设计，语速适中而连贯，不要播音腔或夸张表演。"
        "保持同一位讲述者的稳定音色。严格朗读输入文字，不增添开场白、解释或结束语。"
        "英文产品名自然读出：Duet 按英文 duet 发音，Agent Loop 和 LoopLab 按英文词组发音；"
        "PR 逐个字母读出，GitHub、Tracking、Dream 按英文发音。"
    ),
    "en": (
        "Narrate a clear, thoughtful explainer about personal AI collaboration at work. "
        "Use a warm, professional conversational voice with a neutral English accent, "
        "steady timbre and a measured but flowing pace. Avoid an announcer voice, "
        "overacting, exaggerated pauses or dramatic emphasis. Read the input exactly; "
        "do not add introductions, explanations or closing remarks. "
        "Pronounce Duet as the ordinary English word duet, Agent Loop and LoopLab "
        "as their English words, PR as individual letters, and GitHub naturally."
    ),
}
ENGLISH_CONNECTORS = {
    "and", "as", "but", "for", "if", "or", "so", "that", "then",
    "when", "where", "whether", "which", "while", "with",
}
ENGLISH_OPEN_PHRASES = {"a", "an", "and", "are", "in", "is", "of", "or", "our", "the", "their", "to", "uses", "was"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--language", choices=SETTINGS, help="Generate only one language")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--voice", default=DEFAULT_VOICE)
    parser.add_argument("--speed", type=float, default=1.0)
    parser.add_argument("--workers", type=int, choices=range(1, 5), default=4)
    parser.add_argument("--sample", type=int, metavar="COUNT",
                        help="Generate the first COUNT real cues in out/v2/tts-samples only")
    parser.add_argument("--intro-only", action="store_true",
                        help="Regenerate scene 00, retaining matching OpenAI story narration")
    args = parser.parse_args()
    validate_options(parser, args)
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        parser.error("OPENAI_API_KEY is required in the process environment")
    try:
        for language in (args.language,) if args.language else SETTINGS:
            narration = narration_settings(language, args.model, args.voice, args.speed)
            if args.sample:
                generate_samples(language, narration, api_key, args.sample, args.workers)
            else:
                generate(language, narration, api_key, args.intro_only, args.workers)
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Narration generation failed: {error}\n")


def validate_options(parser, args):
    if not math.isfinite(args.speed) or not 0.25 <= args.speed <= 4:
        parser.error("--speed must be between 0.25 and 4")
    if args.sample is not None and args.sample < 1:
        parser.error("--sample must be positive")
    if args.sample and args.intro_only:
        parser.error("--sample and --intro-only are separate operations")


def narration_settings(language, model=DEFAULT_MODEL, voice=DEFAULT_VOICE, speed=1.0):
    return {"provider": "openai", "model": model, "voice": voice, "speed": speed,
            "language": language, "instructions": INSTRUCTIONS[language]}


def generate(language, narration, api_key, intro_only=False, workers=4):
    _, _, heading, line_limit = SETTINGS[language]
    scenes = read_scenes(language, heading)
    output = ROOT / "public" / ASSET_DIRECTORY / language
    output.parent.mkdir(parents=True, exist_ok=True)
    retained = read_retained_story(output, scenes, language, line_limit, narration) if intro_only else {}
    chapters, cues = [], []
    frame, story_cue_count = 0, 0
    with tempfile.TemporaryDirectory(prefix=f".{language}-", dir=output.parent) as temporary:
        staging = Path(temporary)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for scene, title, text in scenes:
                if scene in retained:
                    chapter, chapter_cues = retained[scene]
                    offset = frame - chapter["start"]
                    chapters.append({**chapter, "start": frame})
                    cues.extend({**cue, "start": cue["start"] + offset} for cue in chapter_cues)
                    frame += chapter["duration"]
                    print(f"{language} {scene:02d}: preserved {len(chapter_cues)} cues, "
                          f'{chapter["duration"] / FPS:.2f}s', flush=True)
                    continue
                captions = checked_captions(text, language, line_limit)
                assets = []
                for index in range(len(captions)):
                    if scene == 0:
                        assets.append(f"intro-{index + 1:03d}.mp3")
                    else:
                        story_cue_count += 1
                        assets.append(f"{story_cue_count:03d}.mp3")
                futures = [pool.submit(synthesize, spoken, narration, staging / asset, api_key)
                           for (spoken, _), asset in zip(captions, assets)]
                start = frame
                frame += OPENING_FRAMES
                for index, ((_, caption), asset, future) in enumerate(zip(captions, assets, futures)):
                    duration, provenance = future.result()
                    if index:
                        frame += GAP_FRAMES
                    cues.append({"scene": scene, "start": frame, "duration": duration,
                                 "text": caption, "audio": f"{ASSET_DIRECTORY}/{language}/{asset}",
                                 "provenance": provenance})
                    frame += duration
                frame += CLOSING_FRAMES
                chapters.append({"id": scene, "title": title, "start": start, "duration": frame - start})
                print(f"{language} {scene:02d}: {len(captions)} OpenAI cues, "
                      f"{(frame - start) / FPS:.2f}s", flush=True)
        write_metadata(staging, frame, chapters, cues, narration)
        output.mkdir(exist_ok=True)
        for asset in staging.iterdir():
            asset.replace(output / asset.name)
        expected = {Path(cue["audio"]).name for cue in cues}
        for obsolete in (*output.glob("[0-9][0-9][0-9].mp3"), *output.glob("intro-[0-9][0-9][0-9].mp3")):
            if obsolete.name not in expected:
                obsolete.unlink()
    print(f'{language}: {narration["model"]}, {narration["voice"]}, '
          f'{len(cues)} cues, {frame / FPS:.2f}s', flush=True)


def generate_samples(language, narration, api_key, count, workers):
    _, _, heading, line_limit = SETTINGS[language]
    scene, _, text = read_scenes(language, heading)[0]
    captions = checked_captions(text, language, line_limit)[:count]
    output = ROOT / "out" / "v2" / "tts-samples" / language
    output.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(synthesize, spoken, narration, output / f"intro-{index:03d}.mp3", api_key)
                   for index, (spoken, _) in enumerate(captions, 1)]
        samples = []
        for index, ((spoken, _), future) in enumerate(zip(captions, futures), 1):
            frames, provenance = future.result()
            samples.append({"scene": scene, "text": spoken, "duration": frames,
                            "audio": f"intro-{index:03d}.mp3", "provenance": provenance})
    (output / "samples.json").write_text(
        json.dumps({"narration": narration, "samples": samples}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    print(f"{language}: {len(samples)} samples saved to {output.relative_to(ROOT)}", flush=True)


def checked_captions(text, language, line_limit):
    captions = split_narration(text, language, line_limit)
    if re.sub(r"\s+", "", "".join(spoken for spoken, _ in captions)) != re.sub(r"\s+", "", text):
        raise ValueError(f"{language}: caption splits changed the narration")
    return captions


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


def read_retained_story(output, scenes, language, line_limit, narration):
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("narration") != narration:
        raise ValueError(f"{language}: narration provider/settings changed; regenerate all narration")
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


def synthesize(text, narration, output, api_key):
    payload = speech_payload(text, narration)
    digest = payload_hash(payload)
    raw, receipt = request_speech(payload, digest, api_key)
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
        raise ValueError("The generated narration clip contains no audio")
    provenance = {"payload_sha256": digest, "audio_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
                  **receipt}
    return math.ceil(duration * FPS), provenance


def speech_payload(text, narration):
    return {"model": narration["model"], "voice": narration["voice"], "input": text,
            "instructions": narration["instructions"], "response_format": "wav",
            "speed": narration["speed"]}


def payload_hash(payload):
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def request_speech(payload, digest, api_key):
    CACHE.mkdir(parents=True, exist_ok=True)
    audio = CACHE / f"{digest}.wav"
    metadata = CACHE / f"{digest}.json"
    if audio.is_file() and metadata.is_file():
        return audio, json.loads(metadata.read_text(encoding="utf-8"))
    endpoint = (os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/") + "/audio/speech"
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = Request(endpoint, data=body, headers={"Authorization": f"Bearer {api_key}",
                                                  "Content-Type": "application/json"}, method="POST")
    for attempt in range(3):
        try:
            with urlopen(request, timeout=120) as response:
                content = response.read()
                request_id = response.headers.get("x-request-id", "")
            break
        except HTTPError as error:
            if error.code not in (429, 500, 502, 503, 504) or attempt == 2:
                raise RuntimeError(f"OpenAI speech request failed with HTTP {error.code}") from None
        except ValueError:
            raise RuntimeError("Invalid OpenAI speech request configuration") from None
        except (URLError, TimeoutError, ConnectionError):
            if attempt == 2:
                raise RuntimeError("OpenAI speech request failed after three connection attempts") from None
        time.sleep(2 ** (attempt + 1))
    if not request_id or not content:
        raise RuntimeError("OpenAI speech response is missing audio or its request ID")
    receipt = {"request_id": request_id, "source_sha256": hashlib.sha256(content).hexdigest()}
    with tempfile.NamedTemporaryFile(dir=CACHE, suffix=".wav", delete=False) as temporary:
        temporary.write(content)
        temporary_path = Path(temporary.name)
    temporary_path.replace(audio)
    metadata.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    return audio, receipt


def write_metadata(output, duration, chapters, cues, narration):
    manifest = {"fps": FPS, "duration": duration, "narration": narration, "chapters": chapters, "cues": cues}
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
