# Duet — AI Native, explained through work

The current films first introduce the architecture and each component's role.
The central Agent Loop ring contains Message Center and LoopLab. Personal Duets
sit around the outside, each linked outward to its own employee (Owner). An
animated event flow connects the responsibilities before the examples begin.

The twelve story scenes then follow an everyday report request and a fictional
project from an external update through research, product definition, development,
review and acceptance. The opening and closing use the same architecture map.
Both languages have independent narration timing across thirteen chapters.
The design and [bilingual scripts](../ai-native-vision/v2/README.md) are the editorial sources.

| Language | Composition | Duration | Final MP4 | Subtitles |
| --- | --- | --- | --- | --- |
| 中文 | `DuetExplainedZh` | 11:31.133 | [1080p film](out/v2/duet-ai-native-zh-CN-1080p.mp4) | [SRT](public/voiceover-v2/zh-CN/duet-ai-native-v2.srt) |
| English | `DuetExplainedEn` | 11:10.333 | [1080p film](out/v2/duet-ai-native-en-1080p.mp4) | [SRT](public/voiceover-v2/en/duet-ai-native-v2.srt) |

Both deliverables are H.264/AAC at 1920 × 1080 and 30 fps, with visible subtitles
and a quiet ambient score. Each person delegates to their own Duet. The topic
board carries agent collaboration; sending as the employee follows the explicit
instruction in Bob's report task.

## Preview and export

Use Node 22.14+ and npm, from this directory:

```sh
npm ci
npm run dev
```

Open the printed Studio URL and select `DuetExplainedZh` or `DuetExplainedEn`.
The compositions use local fonts and audio. Remotion uses its bundled browser.

```sh
npm run lint
npm run render:v2:zh
npm run render:v2:en
```

The two final MP4s linked above are checked into Git alongside the source, fonts,
measured narration and subtitles. Other renders, preview pages, logs and visual QA
frames under `out/` remain ignored. On GitHub, use **View raw** or **Download raw
file** to open or download a film if the file page does not show a player.

## Editing the current films

| Change | Source |
| --- | --- |
| Narration and screen direction | `../ai-native-vision/v2/video-script.zh-CN.md`, `video-script.en.md` |
| Architecture introduction and shared map | `src/v2/ArchitectureScene.tsx` |
| Scenes 1–6: delegation, messages and topics | `src/v2/PersonalScenes.tsx` |
| Scenes 7–12: memory, routing and acceptance | `src/v2/DeliveryScenes.tsx` |
| Visual primitives and narration cue matching | `src/v2/Shared.tsx` |
| Scene timeline, subtitles and sound | `src/v2/Film.tsx` |
| Composition registrations and controls | `src/Root.tsx` |
| Speech and measured subtitle generation | `scripts/generate-v2-narration.py` |

Both languages use AI-generated narration from the
[OpenAI Speech API](https://developers.openai.com/api/docs/guides/text-to-speech):
`gpt-4o-mini-tts`, voice `marin`, with separate Mandarin and English delivery
instructions. The opening and closing identify the narration as AI-generated.
Set `OPENAI_API_KEY` in the process environment, then generate with Python,
FFmpeg and FFprobe:

```sh
npm run narration:v2
```

To preview the voice before replacing the recorded narration:

```sh
python3 scripts/generate-v2-narration.py --sample 2
```

This writes two clips per language under ignored `out/v2/tts-samples/`.

To update only the architecture introduction while preserving the recorded story:

```sh
python3 scripts/generate-v2-narration.py --intro-only
```

The generator extracts each scene's full narration, creates MP3 clips, measures
their durations and writes a frame-based manifest, Caption JSON and SRT together.
It caches speech responses under ignored `out/v2/tts-cache/` so completed requests
can be reused. Each cue records the request and audio hashes; the manifest records
the model, voice and delivery settings. Credentials are never included in assets.
Regenerate these outputs from the source script. Existing clips render on other
platforms without a speech service. Visual stages follow spoken phrases, including
an estimated position when multiple stages occur inside one measured audio clip.
The two languages have independent timing so neither voice is squeezed into the
other's speaking slots.

The Speech API model remains available until January 6, 2027, according to the
[official deprecation notice](https://developers.openai.com/api/docs/deprecations).
The checked-in audio and MP4s render and play without API access.

## Verification

```sh
python3 scripts/verify-v2-delivery.py --language zh-CN --video out/v2/duet-ai-native-zh-CN-1080p.mp4
python3 scripts/verify-v2-delivery.py --language en --video out/v2/duet-ai-native-en-1080p.mp4
```

Verification checks the complete narration against the editorial script, caption
timing, measured audio lengths, OpenAI request receipts and audio hashes, chapter
boundaries, encoded frames and an audible speech window in every chapter.
Visual review covers all twelve scenes in both languages,
intermediate delegation and topic states, subtitles, page assertions, the architecture
introduction and the closing loop. These checks complement visual playback review;
they do not transcribe speech.

## Earlier compositions and assets

`DuetBusinessLoop`, `DuetBusinessLoopZhReview` and `DuetAiNativeZh` remain available
with their original scripts and measured audio. They belong to earlier editions.
The current films are under `src/v2/` and `public/voiceover-v2/`.

Inter is bundled from `@fontsource/inter`; see its [license](public/fonts/LICENSE.txt).
Noto Sans SC comes from the official Google Fonts distribution; see its
[license](public/fonts/NotoSansSC-LICENSE.txt). The ambient score is generated by
`scripts/generate-ambience.py`. All diagrams and illustrative interface views are
authored in React. Remotion retains its own
[licensing terms](https://github.com/remotion-dev/remotion/blob/main/LICENSE.md).
