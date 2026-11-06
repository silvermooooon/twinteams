# Chinese review version verification

Reviewed on 7 November 2026. The English version remains the final release;
the Chinese version is for the owner's personal review.

- Chinese file: `out/duet-business-loop-zh-review-1080p.mp4`
- SHA-256: `0bfee5e956edea554fee14366d3c066bf4f96f668fa8db9e41ab263778d4fc03`
- Measured format: 1920 × 1080, 30 fps, 7,200 frames, 240.0 seconds,
  H.264 video and AAC audio.
- All 49 Chinese cues match their source, measured audio and SRT. Cue IDs,
  chapters and start times match the English version exactly. Speech totals
  181.896 seconds; the last cue ends at 237.888 seconds. No cue overlap or chapter
  overflow was found.
- Narration uses the local Tingting Mandarin synthetic voice at rate 170.
  Encoded audio peaks at −2.3 dB. The first, middle and last spoken windows
  measure −18.1, −18.4 and −18.6 dB mean volume, above the ambient-only threshold.

An independent reviewer inspected frames decoded from the Chinese MP4 at frames
70, 3615, 5865 and 7090. Chinese glyphs are legible, subtitles fit their reserved
area, and no clipping or obstruction of the diagrams or closing brand was found.
Scene graphics and their English labels are shared with the final release.

The translation preserves the fictional scenario, approved collaboration,
human decisions, future production integration and illustrative contribution
percentages. It does not present the 10% / 50% / 95% progression as measured data.

The English final MP4 is unchanged: SHA-256 remains
`9865f5b1ca31b5a17d5cb12e97e4f9f9a8ba6ae924fe11bc1b0cb70fed1f7729`, matching
the original [English verification](QA.md).

These are sampled visual, translation, timing and encoded-audio checks, not
speech recognition or a full manual listening review. Machine-readable export
results are in `out/zh-review-verification.json`.
