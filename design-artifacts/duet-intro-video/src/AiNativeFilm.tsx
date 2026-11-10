import { Audio } from "@remotion/media";
import {
  AbsoluteFill,
  interpolate,
  Sequence,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import narration from "../public/voiceover-ai-native/manifest.json";
import { AiNativeScene } from "./AiNativeScenes";

export const aiNativeMetadata = {
  fps: narration.fps,
  durationInFrames: narration.duration,
};

type Props = {
  subtitles: boolean;
  ambience: boolean;
};

function Captions() {
  const frame = useCurrentFrame();
  const cue = narration.cues.find(
    (line) => frame >= line.start && frame < line.start + line.duration,
  );
  if (!cue) return null;

  return (
    <div
      style={{
        position: "absolute",
        bottom: 48,
        left: 140,
        right: 140,
        display: "flex",
        justifyContent: "center",
        pointerEvents: "none",
      }}
    >
      <div
        style={{
          maxWidth: 1480,
          padding: "10px 25px 13px",
          borderRadius: 8,
          background: "#202124",
          color: "#ffffff",
          fontFamily: 'Inter, "Noto Sans SC", sans-serif',
          fontSize: 38,
          lineHeight: 1.27,
          textAlign: "center",
          textWrap: "balance",
        }}
      >
        {cue.text}
      </div>
    </div>
  );
}

export function AiNativeFilm({ subtitles, ambience }: Props) {
  const { fps, durationInFrames } = useVideoConfig();
  return (
    <AbsoluteFill>
      {narration.chapters.map(({ start, duration }, scene) => (
        <Sequence
          key={start}
          name={`Chapter ${scene + 1}`}
          from={start}
          durationInFrames={duration}
          premountFor={fps}
        >
          <AiNativeScene scene={scene} durationInFrames={duration} />
        </Sequence>
      ))}
      {narration.cues.map((cue) => (
        <Audio
          key={cue.audio}
          name={cue.text}
          from={cue.start}
          durationInFrames={cue.duration}
          src={staticFile(cue.audio)}
        />
      ))}
      {ambience && (
        <Audio
          name="Ambient score"
          src={staticFile("voiceover/ambience.mp3")}
          durationInFrames={durationInFrames}
          loop
          volume={(frame) =>
            interpolate(
              frame,
              [0, fps, durationInFrames - 2 * fps, durationInFrames],
              [0, 0.68, 0.68, 0],
              {
                extrapolateLeft: "clamp",
                extrapolateRight: "clamp",
              },
            )
          }
        />
      )}
      {subtitles && <Captions />}
    </AbsoluteFill>
  );
}
