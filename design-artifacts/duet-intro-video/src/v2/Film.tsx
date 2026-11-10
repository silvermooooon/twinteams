import type { ComponentType } from "react";
import { Audio } from "@remotion/media";
import {
  AbsoluteFill,
  Sequence,
  Track,
  interpolate,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import zh from "../../public/voiceover-v2/zh-CN/manifest.json";
import en from "../../public/voiceover-v2/en/manifest.json";
import { type Cue, type Language, type SceneProps, C } from "./Shared";
import { S00Architecture } from "./ArchitectureScene";
import {
  S01Delegation,
  S02Bob,
  S03Personal,
  S04MessageCenter,
  S05Research,
  S06Topics,
} from "./PersonalScenes";
import {
  S07Memory,
  S08Loop,
  S09Repair,
  S10Testing,
  S11Progress,
  S12WholeLoop,
} from "./DeliveryScenes";

export const v2Metadata = {
  zh: { duration: zh.duration, fps: zh.fps },
  en: { duration: en.duration, fps: en.fps },
};
type Props = { language: Language; subtitles: boolean; ambience: boolean };
type ChapterData = {
  id: number;
  title: string;
  start: number;
  duration: number;
};

function Captions({ cues }: { cues: Cue[] }) {
  const frame = useCurrentFrame();
  const cue = cues.find(
    (c) => frame >= c.start && frame < c.start + c.duration,
  );
  if (!cue) return null;
  return (
    <div
      style={{
        position: "absolute",
        left: 120,
        right: 120,
        bottom: 44,
        display: "flex",
        justifyContent: "center",
        fontFamily: 'Inter, "Noto Sans SC", sans-serif',
        pointerEvents: "none",
      }}
    >
      <div
        style={{
          maxWidth: 1620,
          padding: "15px 28px",
          background: C.ink,
          color: "white",
          borderRadius: 8,
          fontSize: 37,
          lineHeight: 1.3,
          textAlign: "center",
          whiteSpace: "pre-line",
        }}
      >
        {cue.text}
      </div>
    </div>
  );
}

function Chapter({
  data,
  Visual,
  language,
  cues,
  subtitles,
}: {
  data: ChapterData;
  Visual: ComponentType<SceneProps>;
  language: Language;
  cues: Cue[];
  subtitles: boolean;
}) {
  const local = cues
    .filter((c) => c.scene === data.id)
    .map((c) => ({ ...c, start: c.start - data.start }));
  return (
    <>
      <Visual language={language} cues={local} />
      <Track name="Narration">
        {local.map((cue) => (
          <Audio
            key={cue.audio}
            name={cue.text.replace(/\n/g, " ")}
            src={staticFile(cue.audio)}
            from={cue.start}
            durationInFrames={cue.duration}
          />
        ))}
      </Track>
      {subtitles && <Captions cues={local} />}
    </>
  );
}

function Score() {
  const { fps, durationInFrames } = useVideoConfig();
  return (
    <Audio
      name="Ambient score"
      src={staticFile("voiceover/ambience.mp3")}
      loop
      durationInFrames={durationInFrames}
      volume={(frame) =>
        interpolate(
          frame,
          [0, fps * 2, durationInFrames - fps * 3, durationInFrames],
          [0, 0.5, 0.5, 0],
          { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
        )
      }
    />
  );
}
function Progress() {
  const frame = useCurrentFrame();
  const { durationInFrames } = useVideoConfig();
  return (
    <div
      style={{
        position: "absolute",
        bottom: 0,
        left: 0,
        height: 4,
        width: `${(100 * frame) / durationInFrames}%`,
        background: C.red,
      }}
    />
  );
}

function NarrationCredit({ language }: { language: Language }) {
  const frame = useCurrentFrame();
  const { fps, durationInFrames } = useVideoConfig();
  if (frame >= 6 * fps && frame < durationInFrames - 6 * fps) return null;
  return (
    <div
      style={{
        position: "absolute",
        right: 100,
        bottom: 180,
        color: C.muted,
        fontFamily: 'Inter, "Noto Sans SC", sans-serif',
        fontSize: 22,
        pointerEvents: "none",
      }}
    >
      {language === "zh-CN"
        ? "AI 生成配音 · OpenAI TTS"
        : "AI-generated narration · OpenAI TTS"}
    </div>
  );
}

export function DuetExplainedFilm({ language, subtitles, ambience }: Props) {
  const data = language === "zh-CN" ? zh : en;
  const introduction = data.chapters[0];
  const chapters = data.chapters.slice(1);
  const common = { language, cues: data.cues, subtitles };
  return (
    <AbsoluteFill style={{ background: C.paper }}>
      <Track name="Story">
        <Sequence
          name="00 · Architecture and responsibilities"
          from={introduction.start}
          durationInFrames={introduction.duration}
        >
          <Chapter data={introduction} Visual={S00Architecture} {...common} />
        </Sequence>
        <Sequence
          name="01 · Personal delegation"
          from={chapters[0].start}
          durationInFrames={chapters[0].duration}
        >
          <Chapter data={chapters[0]} Visual={S01Delegation} {...common} />
        </Sequence>
        <Sequence
          name="02 · Bob’s report"
          from={chapters[1].start}
          durationInFrames={chapters[1].duration}
        >
          <Chapter data={chapters[1]} Visual={S02Bob} {...common} />
        </Sequence>
        <Sequence
          name="03 · One personal Duet"
          from={chapters[2].start}
          durationInFrames={chapters[2].duration}
        >
          <Chapter data={chapters[2]} Visual={S03Personal} {...common} />
        </Sequence>
        <Sequence
          name="04 · Message Center"
          from={chapters[3].start}
          durationInFrames={chapters[3].duration}
        >
          <Chapter data={chapters[3]} Visual={S04MessageCenter} {...common} />
        </Sequence>
        <Sequence
          name="05 · Nora’s research"
          from={chapters[4].start}
          durationInFrames={chapters[4].duration}
        >
          <Chapter data={chapters[4]} Visual={S05Research} {...common} />
        </Sequence>
        <Sequence
          name="06 · Shared topic"
          from={chapters[5].start}
          durationInFrames={chapters[5].duration}
        >
          <Chapter data={chapters[5]} Visual={S06Topics} {...common} />
        </Sequence>
        <Sequence
          name="07 · Memory and Tracking"
          from={chapters[6].start}
          durationInFrames={chapters[6].duration}
        >
          <Chapter data={chapters[6]} Visual={S07Memory} {...common} />
        </Sequence>
        <Sequence
          name="08 · Policy routing"
          from={chapters[7].start}
          durationInFrames={chapters[7].duration}
        >
          <Chapter data={chapters[7]} Visual={S08Loop} {...common} />
        </Sequence>
        <Sequence
          name="09 · Repair and review"
          from={chapters[8].start}
          durationInFrames={chapters[8].duration}
        >
          <Chapter data={chapters[8]} Visual={S09Repair} {...common} />
        </Sequence>
        <Sequence
          name="10 · Independent acceptance"
          from={chapters[9].start}
          durationInFrames={chapters[9].duration}
        >
          <Chapter data={chapters[9]} Visual={S10Testing} {...common} />
        </Sequence>
        <Sequence
          name="11 · Personal progress"
          from={chapters[10].start}
          durationInFrames={chapters[10].duration}
        >
          <Chapter data={chapters[10]} Visual={S11Progress} {...common} />
        </Sequence>
        <Sequence
          name="12 · The business loop"
          from={chapters[11].start}
          durationInFrames={chapters[11].duration}
        >
          <Chapter data={chapters[11]} Visual={S12WholeLoop} {...common} />
        </Sequence>
      </Track>
      {ambience && <Score />}
      <NarrationCredit language={language} />
      <Progress />
    </AbsoluteFill>
  );
}
