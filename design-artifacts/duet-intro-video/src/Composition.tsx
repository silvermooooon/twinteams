import { Audio } from "@remotion/media";
import {
  AbsoluteFill,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import narration from "../public/voiceover/manifest.json";
import { FilmVisuals } from "./Film";

type FilmProps = { subtitles: boolean; ambience: boolean };

const EnglishSubtitles = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const cue = narration.cues.find(
    (item) =>
      frame >= Math.round(item.start * fps) &&
      frame < Math.round((item.start + item.duration) * fps),
  );
  if (!cue) return null;
  return (
    <div
      style={{
        position: "absolute",
        bottom: 44,
        left: 130,
        right: 130,
        display: "flex",
        justifyContent: "center",
        fontFamily: "Inter, sans-serif",
      }}
    >
      <div
        style={{
          maxWidth: 1520,
          padding: "14px 28px",
          background: "rgba(20,20,20,0.94)",
          color: "#fff",
          fontSize: 34,
          fontWeight: 400,
          lineHeight: 1.35,
          textAlign: "center",
        }}
      >
        {cue.text}
      </div>
    </div>
  );
};

export const FilmComposition = ({ subtitles, ambience }: FilmProps) => {
  const { fps } = useVideoConfig();
  return (
    <AbsoluteFill>
      <FilmVisuals />
      {narration.cues.map((cue) => (
        <Audio
          key={cue.id}
          name={`Narration ${cue.id}`}
          from={Math.round(cue.start * fps)}
          durationInFrames={Math.ceil(cue.duration * fps)}
          src={staticFile(cue.audio)}
          volume={1}
        />
      ))}
      {ambience && (
        <Audio
          name="Original ambient score"
          src={staticFile("voiceover/ambience.mp3")}
          durationInFrames={narration.duration * fps}
          volume={0.65}
        />
      )}
      {subtitles && <EnglishSubtitles />}
    </AbsoluteFill>
  );
};
