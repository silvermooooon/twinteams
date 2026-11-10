import { Composition } from "remotion";
import { AiNativeFilm, aiNativeMetadata } from "./AiNativeFilm";
import { FilmComposition } from "./Composition";
import "./fonts";

export const RemotionRoot: React.FC = () => {
  return (
    <>
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
