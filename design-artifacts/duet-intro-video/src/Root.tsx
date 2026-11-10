import { Composition } from "remotion";
import { AiNativeFilm, aiNativeMetadata } from "./AiNativeFilm";
import { FilmComposition } from "./Composition";
import { DuetExplainedFilm, v2Metadata } from "./v2/Film";
import "./fonts";

export const RemotionRoot: React.FC = () => {
  return (
    <>
      <Composition
        id="DuetExplainedZh"
        component={DuetExplainedFilm}
        durationInFrames={v2Metadata.zh.duration}
        fps={30}
        width={1920}
        height={1080}
        defaultProps={{ language: "zh-CN", subtitles: true, ambience: true }}
      />
      <Composition
        id="DuetExplainedEn"
        component={DuetExplainedFilm}
        durationInFrames={v2Metadata.en.duration}
        fps={30}
        width={1920}
        height={1080}
        defaultProps={{ language: "en", subtitles: true, ambience: true }}
      />
      <Composition
        id="DuetBusinessLoop"
        component={FilmComposition}
        durationInFrames={7200}
        fps={30}
        width={1920}
        height={1080}
        defaultProps={{ subtitles: true, ambience: true, language: "en" }}
      />
      <Composition
        id="DuetBusinessLoopZhReview"
        component={FilmComposition}
        durationInFrames={7200}
        fps={30}
        width={1920}
        height={1080}
        defaultProps={{ subtitles: true, ambience: true, language: "zh-CN" }}
      />
      <Composition
        id="DuetAiNativeZh"
        component={AiNativeFilm}
        durationInFrames={aiNativeMetadata.durationInFrames}
        fps={aiNativeMetadata.fps}
        width={1920}
        height={1080}
        defaultProps={{ subtitles: true, ambience: true }}
      />
    </>
  );
};
