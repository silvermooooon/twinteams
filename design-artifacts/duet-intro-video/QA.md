# Film delivery verification

Reviewed on 7 November 2026. No blocking issue was found in the checks below.

## Final export

- File: `out/duet-business-loop-1080p.mp4`
- SHA-256: `9865f5b1ca31b5a17d5cb12e97e4f9f9a8ba6ae924fe11bc1b0cb70fed1f7729`
- Measured: 1920 × 1080, 30 fps, 7,200 frames, 240.0 seconds, H.264 video and AAC audio.
- All 49 narration cues match the script and SRT. Measured speech totals 205.872
  seconds; the last cue ends at 238.8 seconds.
- The encoded audio peaks at −2.2 dB. First, middle and last spoken windows
  (cues 001, 025 and 049) measure −19.0, −18.7 and −18.6 dB mean volume,
  respectively. These pass the check that distinguishes narration from the quiet
  ambience alone.
- The delivery coordinator regenerated the 960 × 540 preview from this corrected
  final export and verified its media format and narration checks.

The machine-readable final results are in `out/final-verification.json`.

## Visual inspection

An independent reviewer inspected frames decoded from the final MP4 at frames
750, 1650, 4500, 5805, 5865, 6200, 6449 and 6450. The delivery coordinator also
inspected decoded frames 0, 3600, 6795 and 7199.

- Opening, chapter cuts and final branding contain visible text; the sampled
  transition frames have no blank scene.
- Worker cards fit their copy. The Message Center diagram and closing loop fit
  within the frame.
- Sampled subtitles are legible and do not obscure the main content.
- The growth sequence shows the 50% and 95% stages alongside the corresponding
  spoken cue. Its illustrative qualifier remains visible. The final 5% human
  review stage explicitly retains key human approvals.
- The final frame retains the Duet wordmark and closing message.

This is sampled visual inspection plus encoded-media and narration-timing checks,
not a claim that every frame received manual review or that speech recognition
verified pronunciation. It does not test the live Duet business workflow.

## Editorial boundaries

The story and people are fictional. Production integration is presented as a
future step, and contribution percentages as an ambition rather than a measured
result. The film retains Duet branding. Its English narration uses the installed
macOS Daniel synthetic voice; subtitle and audio sources are included for future
editing and regeneration.
