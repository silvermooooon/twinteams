# Duet — The AI-Native Business Loop

A four-minute product film. **The final release uses English narration and English
subtitles.** A separate Mandarin-narrated version with Simplified Chinese subtitles
is for the owner's personal review. React and
TypeScript are rendered by Remotion at **1920 × 1080, 30 fps, 7,200 frames**.
This standalone media project does not connect to the running product, its
database, or private conversations.

One fictional card-activation story follows people, their Duets, and Message
Center around a recurring business loop. Red, white, graphite and angular geometry
follow the approved HSBC-inspired direction, retaining Duet's name and mark.

## Preview and render

Use Node **22.14+** and npm. Run from this directory:

```sh
npm ci
npm run dev -- --port=3100
```

Open the printed Studio URL and select **DuetBusinessLoop**. Motion is driven by
Remotion frames. Fonts and audio are local; Remotion may download its bundled
rendering browser on the first export. `remotion.config.ts` selects Remotion's
bundled Chrome-for-testing for compatibility with this macOS render environment;
it does not depend on a system Chrome path.

```sh
npm run lint
npm run render:preview
npm run render:final
```

The outputs are `out/duet-preview-540p.mp4` and
`out/duet-business-loop-1080p.mp4`. Render settings live in `package.json`.

For the Chinese review version, select **DuetBusinessLoopZhReview** in Studio or run:

```sh
python3 scripts/generate-narration.py --language zh-CN
npm run render:review:zh
```

Both versions share the same four-minute visuals, English scene labels and cue
start times, so review comments refer to the same moments. Only the review
narration and subtitles are Chinese. Generating or rendering the review version
does not overwrite the English audio, subtitles or final MP4.

Inspect the first, middle and last frames, plus every scene transition:

```sh
npx remotion still src/index.ts DuetBusinessLoop out/frame-0000.png --frame=0
npx remotion still src/index.ts DuetBusinessLoop out/frame-3600.png --frame=3600
npx remotion still src/index.ts DuetBusinessLoop out/frame-7199.png --frame=7199
```

`out/` is ignored by Git. Preserve source, narration, fonts and the lockfile;
share the exported MP4 separately.

## AI Native design film

**DuetAiNativeZh** is a separate Mandarin film based on the
[AI Native core design](../ai-native-vision/Duet-AI-Native-Core-Design.zh-CN.docx).
The [editable script](script/ai-native.zh-CN.md) contains narration, matching
Simplified Chinese captions and direction for each scene. The checked-in audio
and measured cue timings render without a speech service. To change the script,
regenerate them on macOS with Tingting, FFmpeg and FFprobe installed.

```sh
python3 scripts/generate-ai-native-narration.py
npx remotion render src/index.ts DuetAiNativeZh out/duet-ai-native-zh-CN.mp4 --codec=h264 --crf=18 --concurrency=4
```

## Change the film

| Change | Source |
| --- | --- |
| English final wording and subtitle text | `script/narration.json` |
| Chinese review wording and subtitle text | `script/narration.zh-CN.json` |
| Scene copy, diagrams, cards and animation | `src/Film.tsx` |
| Colours, typography and chapter timing | `src/theme.ts`, `src/film.css` |
| Dimensions, frame rate and total frames | `src/Root.tsx` |
| Subtitle and ambience switches | `subtitles` / `ambience` default props in `src/Root.tsx` |
| Voice generation and measured cue placement | `scripts/generate-narration.py` |
| Original, quiet ambient score | `scripts/generate-ambience.py` |
| Bundled font loading | `src/fonts.ts` |

After editing spoken wording, regenerate the audio and subtitles:

```sh
python3 scripts/generate-narration.py
```

Generation requires macOS `say`, FFmpeg and FFprobe. The installed **Daniel**
British-English synthetic voice is used for the final, and **Tingting** Mandarin
for the review version; no cloned voice or paid voice service.
Choose an installed replacement with `--voice NAME`, or speaking speed with
`--rate NUMBER`. The checked-in audio renders on other platforms. See
[audio provenance](script/AUDIO.md). Do not hand-edit generated clips, the
manifest or SRT; regenerate their measured timing together.

Chinese assets are separate in `public/voiceover-zh-CN/`, including
`duet.zh-CN.srt`. After changing English cue timing, regenerate the English assets
first, then the Chinese review assets. Chinese speech must fit the corresponding
English cue slots; the generator rejects overlaps rather than truncating speech.

| Time | Chapter |
| --- | --- |
| 0:00–0:25 | The business loop |
| 0:25–0:55 | A Duet for every person |
| 0:55–1:35 | From signal to decision |
| 1:35–2:05 | Message Center |
| 2:05–2:55 | Connected delivery |
| 2:55–3:35 | Learning through experience |
| 3:35–4:00 | An AI-native organisation |

For a duration change, update chapter windows in `src/theme.ts` and
both narration source files, scene beat timings in `src/Film.tsx`, and composition
`durationInFrames` in `src/Root.tsx`. At 30 fps, one second is 30 frames.
Regenerate narration and, if the length changes, run
`python3 scripts/generate-ambience.py` to regenerate the score too. Inspect
transitions. The narrator generator rejects speech that cannot fit rather than
silently cutting it off. Keep the subtitle area clear.

## Verification

```sh
python3 scripts/verify-delivery.py
python3 scripts/verify-delivery.py --video out/duet-preview-540p.mp4 --width 960 --height 540
python3 scripts/verify-delivery.py --video out/duet-business-loop-1080p.mp4
python3 scripts/verify-delivery.py --language zh-CN --video out/duet-business-loop-zh-review-1080p.mp4
```

The verifier checks source/cue/SRT consistency, measured audio, encoded dimensions,
fps, duration and non-silent audio. It also checks the first, middle and last
spoken windows are louder than the quiet ambient score; this detects missing
narration without claiming speech recognition. It does not judge visual quality. Inspect text,
subtitles, clipping, transitions, first/last frames and percentage labels in the
exported preview. Watch with sound to check pronunciation and scene alignment.
Chinese checks also require matching English cue IDs, chapter windows and start
times, while measuring the Chinese clips and subtitles independently.

## Scope and sources

- The card-activation story is fictional; it contains no real bank customer data.
- Owner assistance, files, approved Duet collaboration, Tracking, Memory and Message
  Center reflect product capabilities. Production integration is a future step.
- The 10% / 50% / 95% work-share progression is an illustrative ambition for suitable
  work, not measured performance. People keep key decisions. Memory is retained
  context and experience, not model retraining.
- Diagrams and UI illustrations are authored for this film. The Duet mark comes
  from `frontend/public/assets/duet-symbol.svg` in the parent product repository.
- Inter fonts are bundled from `@fontsource/inter`; see their included
  [license](public/fonts/LICENSE.txt). Chinese subtitles use bundled Noto Sans SC
  from the official [Google Fonts repository](https://github.com/google/fonts/tree/main/ofl/notosanssc);
  see its [license](public/fonts/NotoSansSC-LICENSE.txt). No system Chinese font is
  required, and no paid visual assets were downloaded.
- Prepared using the [official Remotion Agent Skills](https://www.remotion.dev/docs/ai/skills).
  Remotion retains its own [licensing terms](https://github.com/remotion-dev/remotion/blob/main/LICENSE.md).
- Visual reference: [HSBC creative principles](https://create.hsbc/external/newsfeed/why-creative-direction-matters.html).
  The film is branded Duet and does not claim HSBC deployment or endorsement.
