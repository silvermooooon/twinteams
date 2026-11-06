# English narration and subtitles

The film uses **Daniel**, the installed macOS British-English synthetic voice,
at 148 words per minute. It is a local system narrator, not a recorded person or a
cloned voice. No credentials, paid voice service or downloaded media were used.

`narration.json` is the editable source. Each chapter has an explicit time window,
and each line is one spoken cue and matching subtitle. The story is a fictional
card-activation example. Production integration and the 10% / 50% / 95% work-share
progression are described as future and illustrative, not measured results.

From the video project directory, regenerate on macOS with `say`, FFmpeg and
FFprobe installed:

```sh
python3 scripts/generate-narration.py
```

An installed alternative voice or rate can be chosen with `--voice NAME` and
`--rate NUMBER`. `say -v '?'` lists local voices. Keep chapter windows in sync with
the film timeline when changing duration. The generator refuses narration that
does not fit; shorten the wording or adjust the rate, then regenerate.

Generated files live in `public/voiceover/`:

- Numbered MP3 clips: measured speech for each cue, mono 48 kHz / 160 kbit/s.
- `manifest.json`: absolute starts and durations for Remotion audio and subtitles.
- `duet.en.srt`: external English subtitles matching the narration manifest.
- `timing-report.json`: engine, rate, word count and chapter fit measurements.
- `ambience.mp3`: optional original procedural tonal bed, with a -38 LUFS target.

FFprobe measures each finished clip. Pauses are then distributed within its
chapter, preserving space at scene boundaries. FFmpeg `loudnorm` targets -18 LUFS
and -2 dB true peak. There are no network dependencies at playback or render time.

To use a different voice provider later, regenerate the clips and their measured
manifest and SRT; do not change subtitle timings independently of the audio.

The quiet optional ambience is generated from sine waves and a slow amplitude
envelope. It contains no recording, licensed sample, borrowed tune or external
material. Regenerate it with `python3 scripts/generate-ambience.py`. Its fades
follow the film duration in `narration.json`; speech remains the foreground.
