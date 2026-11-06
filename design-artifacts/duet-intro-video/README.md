# Duet — The AI-Native Business Loop

A four-minute, English-narrated product film with English subtitles. React and
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

Inspect the first, middle and last frames, plus every scene transition:

```sh
npx remotion still src/index.ts DuetBusinessLoop out/frame-0000.png --frame=0
npx remotion still src/index.ts DuetBusinessLoop out/frame-3600.png --frame=3600
npx remotion still src/index.ts DuetBusinessLoop out/frame-7199.png --frame=7199
```

`out/` is ignored by Git. Preserve source, narration, fonts and the lockfile;
share the exported MP4 separately.

## Change the film

| Change | Source |
| --- | --- |
| Spoken wording and matching subtitle text | `script/narration.json` |
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
British-English synthetic voice is used; no cloned voice or paid voice service.
Choose an installed replacement with `--voice NAME`, or speaking speed with
`--rate NUMBER`. The checked-in audio renders on other platforms. See
[audio provenance](script/AUDIO.md). Do not hand-edit generated clips, the
manifest or SRT; regenerate their measured timing together.

| Time | Chapter |
| --- | --- |
| 0:00–0:25 | The business loop |
| 0:25–0:55 | A Duet for every person |
| 0:55–1:35 | From signal to decision |
| 1:35–2:05 | Message Center |
| 2:05–2:55 | Connected delivery |
| 2:55–3:35 | Learning through experience |
| 3:35–4:00 | An AI-native organisation |

For a duration change, update chapter windows in both `src/theme.ts` and
`script/narration.json`, scene beat timings in `src/Film.tsx`, and composition
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
```

The verifier checks source/cue/SRT consistency, measured audio, encoded dimensions,
fps, duration and non-silent audio. It also checks the first, middle and last
spoken windows are louder than the quiet ambient score; this detects missing
narration without claiming speech recognition. It does not judge visual quality. Inspect text,
subtitles, clipping, transitions, first/last frames and percentage labels in the
exported preview. Watch with sound to check pronunciation and scene alignment.

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
  [license](public/fonts/LICENSE.txt). No paid visual assets were downloaded.
- Prepared using the [official Remotion Agent Skills](https://www.remotion.dev/docs/ai/skills).
  Remotion retains its own [licensing terms](https://github.com/remotion-dev/remotion/blob/main/LICENSE.md).
- Visual reference: [HSBC creative principles](https://create.hsbc/external/newsfeed/why-creative-direction-matters.html).
  The film is branded Duet and does not claim HSBC deployment or endorsement.
