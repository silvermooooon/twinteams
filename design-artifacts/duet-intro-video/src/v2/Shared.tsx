import type { CSSProperties, ReactNode } from "react";
import {
  AbsoluteFill,
  interpolate,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";

export type Language = "zh-CN" | "en";
export type Cue = {
  scene: number;
  start: number;
  duration: number;
  text: string;
  audio: string;
};
export type SceneProps = { language: Language; cues: Cue[] };
export const C = {
  paper: "#f7f7f4",
  white: "#ffffff",
  ink: "#202124",
  muted: "#6c706f",
  line: "#dcdfda",
  red: "#ce1423",
  wash: "#fff0f0",
  green: "#36755d",
  dark: "#202124",
};
export const row: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 24,
};
export const col: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 22,
};
export const tr = (language: Language, zh: string, en: string) =>
  language === "zh-CN" ? zh : en;

export function useSteps(
  cues: Cue[],
  language: Language,
  phrases: Array<[string, string]>,
) {
  const frame = useCurrentFrame();
  const moments = [
    0,
    ...phrases.map((pair) => {
      const phrase = pair[language === "zh-CN" ? 0 : 1];
      const needle = phrase.replace(/\s+/g, "").toLowerCase();
      const cue = cues.find((c) =>
        c.text.replace(/\s+/g, "").toLowerCase().includes(needle),
      );
      if (!cue) throw new Error(`Missing narration trigger: ${phrase}`);
      const spoken = cue.text.replace(/\s+/g, "").toLowerCase();
      return (
        cue.start +
        Math.round((cue.duration * spoken.indexOf(needle)) / spoken.length)
      );
    }),
  ];
  let step = 0;
  moments.forEach((at, i) => {
    if (frame >= at) step = i;
  });
  return {
    step,
    elapsed: frame - moments[step],
    at: (index: number) => moments[index],
  };
}

export function Frame({
  number,
  title,
  strap,
  children,
  dark = false,
}: {
  number: number;
  title: string;
  strap: string;
  children: ReactNode;
  dark?: boolean;
}) {
  return (
    <AbsoluteFill
      style={{
        background: dark ? C.dark : C.paper,
        color: dark ? C.white : C.ink,
        fontFamily: 'Inter, "Noto Sans SC", sans-serif',
        overflow: "hidden",
      }}
    >
      <div
        style={{
          position: "absolute",
          left: 100,
          right: 100,
          top: 48,
          ...row,
          justifyContent: "space-between",
        }}
      >
        <div
          style={{
            ...row,
            gap: 14,
            fontSize: 26,
            fontWeight: 700,
            letterSpacing: 3,
          }}
        >
          <span
            style={{
              display: "inline-block",
              width: 12,
              height: 28,
              background: C.red,
            }}
          />
          DUET{" "}
          <span
            style={{
              fontWeight: 400,
              color: dark ? "#bfc0bd" : C.muted,
              letterSpacing: 1,
            }}
          >
            {" "}
            / AI NATIVE
          </span>
        </div>
        <div
          style={{
            fontSize: 24,
            color: dark ? "#bfc0bd" : C.muted,
            fontVariantNumeric: "tabular-nums",
          }}
        >
          {" "}
          {number === 0
            ? "ARCHITECTURE"
            : `${String(number).padStart(2, "0")} / 12`}
        </div>
      </div>
      <div style={{ position: "absolute", left: 100, right: 100, top: 108 }}>
        <h1
          style={{
            fontSize: 70,
            lineHeight: 1.08,
            fontWeight: 650,
            letterSpacing: -2,
            margin: 0,
          }}
        >
          {title}
        </h1>
        <div
          style={{
            fontSize: 28,
            marginTop: 18,
            color: dark ? "#bfc0bd" : C.muted,
            lineHeight: 1.3,
          }}
        >
          {strap}
        </div>
      </div>
      <div
        style={{
          position: "absolute",
          left: 100,
          top: 260,
          width: 1720,
          height: 610,
        }}
      >
        {children}
      </div>
      <div
        style={{
          position: "absolute",
          left: 100,
          right: 100,
          bottom: 174,
          borderTop: `1px solid ${dark ? "#494b4d" : C.line}`,
        }}
      />
    </AbsoluteFill>
  );
}

export function Card({
  children,
  style,
  accent = false,
  dark = false,
}: {
  children: ReactNode;
  style?: CSSProperties;
  accent?: boolean;
  dark?: boolean;
}) {
  return (
    <div
      style={{
        boxSizing: "border-box",
        padding: 32,
        border: `1.5px solid ${accent ? C.red : dark ? "#4d5052" : C.line}`,
        borderRadius: 18,
        background: dark ? "#2c2e30" : accent ? C.wash : C.white,
        minWidth: 0,
        color: dark ? C.white : C.ink,
        ...style,
      }}
    >
      {children}
    </div>
  );
}
export function Label({ children }: { children: ReactNode }) {
  return (
    <div
      style={{
        fontSize: 23,
        fontWeight: 650,
        letterSpacing: 1.2,
        color: C.muted,
        textTransform: "uppercase",
        marginBottom: 18,
      }}
    >
      {children}
    </div>
  );
}
export function Text({
  children,
  size = 34,
  muted = false,
  style,
}: {
  children: ReactNode;
  size?: number;
  muted?: boolean;
  style?: CSSProperties;
}) {
  return (
    <div
      style={{
        fontSize: size,
        lineHeight: 1.35,
        fontWeight: 450,
        color: muted ? C.muted : undefined,
        ...style,
      }}
    >
      {children}
    </div>
  );
}
export function Person({
  name,
  role,
  agent = false,
  active = false,
  style,
}: {
  name: string;
  role?: string;
  agent?: boolean;
  active?: boolean;
  style?: CSSProperties;
}) {
  return (
    <div style={{ ...row, gap: 18, ...style }}>
      <div
        style={{
          width: 70,
          height: 70,
          borderRadius: agent ? 18 : "50%",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          background: agent ? C.red : active ? C.ink : "#eceeea",
          color: agent || active ? C.white : C.ink,
          fontSize: agent ? 30 : 32,
          fontWeight: 650,
          flexShrink: 0,
        }}
      >
        {agent ? "D" : name[0]}
      </div>
      <div>
        <div style={{ fontSize: 31, fontWeight: 600, lineHeight: 1.2 }}>
          {name}
        </div>
        {role && (
          <div style={{ fontSize: 24, color: C.muted, marginTop: 6 }}>
            {role}
          </div>
        )}
      </div>
    </div>
  );
}
export function File({
  title,
  detail,
  active = false,
  style,
}: {
  title: string;
  detail?: string;
  active?: boolean;
  style?: CSSProperties;
}) {
  return (
    <div
      style={{
        ...row,
        border: `1px solid ${active ? C.red : C.line}`,
        borderRadius: 12,
        background: active ? C.wash : C.white,
        padding: "22px 25px",
        ...style,
      }}
    >
      <svg width="40" height="50" viewBox="0 0 40 50" style={{ flexShrink: 0 }}>
        <path
          d="M7 3H25L35 13V46H7ZM25 3V14H35M13 24H28M13 32H28M13 40H23"
          fill="none"
          stroke={C.red}
          strokeWidth="2.5"
        />
      </svg>
      <div>
        <div style={{ fontSize: 29, fontWeight: 550, lineHeight: 1.25 }}>
          {title}
        </div>
        {detail && (
          <div style={{ fontSize: 23, marginTop: 6, color: C.muted }}>
            {detail}
          </div>
        )}
      </div>
    </div>
  );
}
export function Pill({
  children,
  active = false,
}: {
  children: ReactNode;
  active?: boolean;
}) {
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        padding: "10px 18px",
        borderRadius: 24,
        background: active ? C.red : "#eceeea",
        color: active ? C.white : C.muted,
        fontSize: 24,
        fontWeight: 550,
        lineHeight: 1.2,
      }}
    >
      {children}
    </span>
  );
}
export function Arrow({
  reverse = false,
  vertical = false,
  color = C.red,
}: {
  reverse?: boolean;
  vertical?: boolean;
  color?: string;
}) {
  return (
    <svg
      width={vertical ? 36 : 74}
      height={vertical ? 74 : 36}
      viewBox="0 0 74 36"
      style={{
        flexShrink: 0,
        rotate: vertical ? "90deg" : undefined,
        scale: reverse ? "-1 1" : undefined,
      }}
    >
      <path
        d="M4 18H66M55 7L66 18L55 29"
        fill="none"
        stroke={color}
        strokeWidth="3"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
export function Reveal({
  at,
  children,
  style,
}: {
  at: number;
  children: ReactNode;
  style?: CSSProperties;
}) {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  return (
    <div
      style={{
        opacity: interpolate(frame, [at, at + fps * 0.5], [0, 1], {
          extrapolateLeft: "clamp",
          extrapolateRight: "clamp",
        }),
        translate: `0 ${interpolate(frame, [at, at + fps * 0.5], [18, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" })}px`,
        ...style,
      }}
    >
      {children}
    </div>
  );
}
export function Stage({
  elapsed,
  children,
  style,
}: {
  elapsed: number;
  children: ReactNode;
  style?: CSSProperties;
}) {
  const { fps } = useVideoConfig();
  return (
    <div
      style={{
        opacity: interpolate(elapsed, [0, fps * 0.35], [0, 1], {
          extrapolateLeft: "clamp",
          extrapolateRight: "clamp",
        }),
        translate: `0 ${interpolate(elapsed, [0, fps * 0.4], [12, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" })}px`,
        height: "100%",
        ...style,
      }}
    >
      {children}
    </div>
  );
}
