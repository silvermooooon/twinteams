import { Audio } from "@remotion/media";
import {
  AbsoluteFill,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import englishNarration from "../public/voiceover/manifest.json";
import chineseNarration from "../public/voiceover-zh-CN/manifest.json";
import { FilmVisuals } from "./Film";

type FilmProps = {
  subtitles: boolean;
  ambience: boolean;
  language: "en" | "zh-CN";
};

const FilmSubtitles = ({ language }: Pick<FilmProps, "language">) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const narration = language === "zh-CN" ? chineseNarration : englishNarration;
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
        fontFamily:
          language === "zh-CN"
            ? '"Noto Sans SC", sans-serif'
            : "Inter, sans-serif",
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

export const FilmComposition = ({
  subtitles,
  ambience,
  language,
}: FilmProps) => {
  const { fps } = useVideoConfig();
  const narration = language === "zh-CN" ? chineseNarration : englishNarration;
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
      {subtitles && <FilmSubtitles language={language} />}
    </AbsoluteFill>
  );
};
