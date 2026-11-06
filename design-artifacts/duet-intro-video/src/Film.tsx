import React from "react";
import {
  AbsoluteFill,
  Easing,
  interpolate,
  Sequence,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import { chapters, theme } from "./theme";
import "./film.css";

const easing = Easing.bezier(0.16, 1, 0.3, 1);
const motion = (frame: number, from: number, to: number, a = 0, b = 1) =>
  interpolate(frame, [from, to], [a, b], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing,
  });
type Children = { children?: React.ReactNode };

export const DuetMark: React.FC<{ size?: number; light?: boolean }> = ({
  size = 48,
  light = false,
}) => (
  <svg width={size} height={size} viewBox="0 0 48 48" fill="none">
    <path
      fill={light ? theme.white : theme.ink}
      d="M22 4H8L2 10V34L8 40H22V32H10V12H22Z"
    />
    <path fill={theme.red} d="M26 8H40L46 14V38L40 44H26V36H38V16H26Z" />
  </svg>
);

const Person: React.FC<{ size?: number; color?: string }> = ({
  size = 60,
  color = theme.ink,
}) => (
  <svg width={size} height={size} viewBox="0 0 64 64" fill="none">
    <circle cx="32" cy="19" r="11" stroke={color} strokeWidth="3" />
    <path d="M12 58V49C12 38 52 38 52 49V58" stroke={color} strokeWidth="3" />
  </svg>
);
const Tick: React.FC<{ size?: number; color?: string }> = ({
  size = 30,
  color = theme.red,
}) => (
  <svg width={size} height={size} viewBox="0 0 32 32" fill="none">
    <path d="M6 17L13 24L27 8" stroke={color} strokeWidth="3" />
  </svg>
);
const FileIcon = () => (
  <svg width="42" height="48" viewBox="0 0 42 48" fill="none">
    <path d="M5 2H26L37 13V46H5V2Z" stroke="currentColor" strokeWidth="2" />
    <path
      d="M26 2V13H37M12 24H29M12 31H29M12 38H23"
      stroke="currentColor"
      strokeWidth="2"
    />
  </svg>
);

const Reveal: React.FC<
  Children & { delay?: number; style?: React.CSSProperties }
> = ({ children, delay = 0, style }) => {
  const frame = useCurrentFrame();
  return (
    <div
      style={{
        opacity: motion(frame, delay, delay + 25),
        translate: `0 ${motion(frame, delay, delay + 35, 25, 0)}px`,
        ...style,
      }}
    >
      {children}
    </div>
  );
};

const Beat: React.FC<
  Children & { from: number; duration: number; name: string }
> = ({ children, from, duration, name }) => {
  const { fps } = useVideoConfig();
  return (
    <Sequence
      name={name}
      from={Math.round(from * fps)}
      durationInFrames={Math.round(duration * fps)}
    >
      <AbsoluteFill className="film-scene">{children}</AbsoluteFill>
    </Sequence>
  );
};
const Heading: React.FC<
  Children & {
    kicker?: string;
    x?: number;
    y?: number;
    width?: number;
    size?: number;
  }
> = ({ children, kicker, x = 110, y = 150, width = 1700, size = 91 }) => (
  <div style={{ position: "absolute", left: x, top: y, width }}>
    {kicker && (
      <div className="film-eyebrow" style={{ marginBottom: 22 }}>
        {kicker}
      </div>
    )}
    <h1 className="film-title" style={{ fontSize: size }}>
      {children}
    </h1>
  </div>
);
const Note: React.FC<Children & { x?: number; y?: number; red?: boolean }> = ({
  children,
  x = 110,
  y = 866,
  red = false,
}) => (
  <div
    className="film-small"
    style={{
      position: "absolute",
      left: x,
      top: y,
      color: red ? theme.red : theme.muted,
    }}
  >
    {children}
  </div>
);
const Tag: React.FC<Children & { red?: boolean }> = ({
  children,
  red = false,
}) => (
  <span
    className="film-label"
    style={{
      display: "inline-flex",
      gap: 10,
      alignItems: "center",
      padding: "12px 18px",
      background: red ? theme.paleRed : "#F0EFEB",
      color: red ? theme.red : theme.muted,
    }}
  >
    {children}
  </span>
);

const Panel: React.FC<
  Children & {
    x: number;
    y: number;
    width: number;
    height?: number;
    style?: React.CSSProperties;
  }
> = ({ children, x, y, width, height, style }) => (
  <div
    className="film-panel"
    style={{
      position: "absolute",
      left: x,
      top: y,
      width,
      height,
      padding: 34,
      ...style,
    }}
  >
    {children}
  </div>
);

const vertices = [
  [170, 70],
  [480, 70],
  [700, 225],
  [700, 430],
  [480, 585],
  [170, 585],
  [-50, 430],
  [-50, 225],
];
const ringPath = "M170 70H480L700 225V430L480 585H170L-50 430V225Z";
const roles = [
  "Research",
  "Business analysis",
  "Business owner",
  "Development",
  "Acceptance",
  "Release",
  "Operations",
  "Customer insight",
];

const AngularLoop: React.FC<{
  x?: number;
  y?: number;
  scale?: number;
  duets?: boolean;
  labels?: boolean;
  compact?: boolean;
  phase?: number;
  center?: React.ReactNode;
}> = ({
  x = 480,
  y = 270,
  scale = 1,
  duets = false,
  labels = true,
  compact = false,
  phase = 0,
  center,
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const travel = ((frame / fps) * 0.21 + phase) % 8;
  const index = Math.floor(travel),
    next = (index + 1) % 8,
    u = travel - index;
  const token = [
    vertices[index][0] + (vertices[next][0] - vertices[index][0]) * u,
    vertices[index][1] + (vertices[next][1] - vertices[index][1]) * u,
  ];
  return (
    <div
      style={{
        position: "absolute",
        left: x,
        top: y,
        width: 900,
        height: 700,
        scale,
        transformOrigin: "0 0",
      }}
    >
      <svg
        width="1000"
        height="720"
        viewBox="-180 -30 1030 760"
        style={{ overflow: "visible" }}
      >
        <path d={ringPath} fill="none" stroke={theme.line} strokeWidth="2" />
        <path
          d={ringPath}
          fill="none"
          stroke={theme.red}
          strokeWidth="4"
          pathLength="1"
          strokeDasharray="1"
          strokeDashoffset={1 - motion(frame, 0, 120)}
          opacity=".85"
        />
        <path
          d="M192 113H464L655 247V408L464 542H192L-5 408V247Z"
          fill="none"
          stroke={theme.line}
          strokeWidth="1"
        />
        {vertices.map(([vx, vy], i) => (
          <g key={roles[i]} opacity={motion(frame, i * 5, i * 5 + 20)}>
            <rect
              x={vx - 26}
              y={vy - 26}
              width="52"
              height="52"
              fill={i === index ? theme.red : theme.paper}
              stroke={i === index ? theme.red : theme.line}
              strokeWidth="2"
            />
            <text
              x={vx}
              y={vy + 8}
              textAnchor="middle"
              fill={i === index ? "white" : theme.ink}
              fontSize="22"
              fontFamily={theme.font}
            >
              {String(i + 1).padStart(2, "0")}
            </text>
            {labels && (
              <text
                x={vx}
                y={vy + (i < 2 ? -49 : i === 2 || i === 7 ? -50 : 65)}
                textAnchor="middle"
                fill={theme.ink}
                fontSize={compact ? 22 : 25}
                fontFamily={theme.font}
              >
                {roles[i]}
              </text>
            )}
            {duets && (
              <g transform={`translate(${vx + 30} ${vy - 43})`}>
                <rect width="28" height="28" fill={theme.white} />
                <path d="M12 4H5L2 7V19L5 22H12V18H6V8H12Z" fill={theme.ink} />
                <path
                  d="M16 6H23L26 9V21L23 24H16V20H22V10H16Z"
                  fill={theme.red}
                />
              </g>
            )}
          </g>
        ))}
        <rect
          x={token[0] - 7}
          y={token[1] - 7}
          width="14"
          height="14"
          fill={theme.red}
          stroke={theme.paper}
          strokeWidth="3"
        />
      </svg>
      {center && (
        <div
          style={{
            position: "absolute",
            left: 302,
            top: 270,
            width: 400,
            textAlign: "center",
          }}
        >
          {center}
        </div>
      )}
    </div>
  );
};

const BusinessLoop = () => {
  const f = useCurrentFrame();
  return (
    <>
      <AbsoluteFill
        className="film-scene"
        style={{
          opacity: interpolate(f, [180, 195], [1, 0], {
            extrapolateLeft: "clamp",
            extrapolateRight: "clamp",
          }),
        }}
      >
        <Heading
          kicker="DUET / THE AI-NATIVE BUSINESS LOOP"
          y={220}
          width={900}
          size={104}
        >
          A business
          <br />
          is a living
          <br />
          <span style={{ color: theme.red }}>loop.</span>
        </Heading>
        <AngularLoop
          x={955}
          y={205}
          scale={0.8}
          labels={false}
          center={
            <>
              <div
                style={{ fontSize: 25, letterSpacing: 4, color: theme.muted }}
              >
                SIGNAL
              </div>
              <div
                style={{
                  height: 60,
                  width: 2,
                  background: theme.line,
                  margin: "20px auto",
                }}
              />
              <div
                style={{ fontSize: 25, letterSpacing: 4, color: theme.muted }}
              >
                OUTCOME
              </div>
            </>
          }
        />
        <Note>People. Decisions. Delivery. Learning.</Note>
      </AbsoluteFill>
      <Beat from={6} duration={10} name="The roles that move work">
        <Heading kicker="01 / THE BUSINESS LOOP" size={70} y={130}>
          People move work forward.
        </Heading>
        <AngularLoop x={446} y={236} scale={0.91} />
        <Note x={1390} y={820}>
          One continuous
          <br />
          flow of responsibility.
        </Note>
      </Beat>
      <Beat from={15.5} duration={9.5} name="The outcome becomes a signal">
        <Heading kicker="01 / THE BUSINESS LOOP" size={70} y={130}>
          Every outcome starts the next question.
        </Heading>
        <AngularLoop
          x={450}
          y={230}
          scale={0.91}
          phase={5.5}
          center={
            <>
              <div className="film-eyebrow">THE NEXT CYCLE</div>
              <div
                style={{
                  fontSize: 48,
                  letterSpacing: -2,
                  marginTop: 15,
                  lineHeight: 1.15,
                }}
              >
                What can
                <br />
                work better?
              </div>
            </>
          }
        />
      </Beat>
    </>
  );
};

const FlowLine: React.FC<{
  d: string;
  delay?: number;
  reverse?: boolean;
  muted?: boolean;
}> = ({ d, delay = 0, reverse = false, muted = false }) => {
  const f = useCurrentFrame();
  return (
    <svg
      width="1920"
      height="940"
      style={{ position: "absolute", inset: 0, pointerEvents: "none" }}
    >
      <path d={d} stroke={theme.line} strokeWidth="2" fill="none" />
      <path
        d={d}
        stroke={muted ? theme.ink : theme.red}
        strokeWidth="3"
        fill="none"
        pathLength="1"
        strokeDasharray="1"
        strokeDashoffset={1 - motion(f, delay, delay + 55)}
      />
      <path
        d={d}
        stroke={muted ? theme.ink : theme.red}
        strokeWidth="9"
        fill="none"
        pathLength="100"
        strokeDasharray="2 98"
        strokeDashoffset={
          ((reverse ? 1 : -1) * (Math.max(0, f - delay) / 2)) % 100
        }
        opacity={motion(f, delay + 20, delay + 40)}
      />
    </svg>
  );
};

const PersonDuet: React.FC<{
  x: number;
  y: number;
  name: string;
  role: string;
  width?: number;
  active?: boolean;
}> = ({ x, y, name, role, width = 440, active = false }) => (
  <Panel
    x={x}
    y={y}
    width={width}
    height={260}
    style={{ borderTop: `4px solid ${active ? theme.red : theme.ink}` }}
  >
    <div
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
      }}
    >
      <Person />
      <div style={{ width: 80, height: 1, background: theme.line }} />
      <DuetMark size={65} />
    </div>
    <div
      style={{
        display: "flex",
        justifyContent: "space-between",
        marginTop: 24,
        fontSize: 31,
        fontWeight: 600,
      }}
    >
      <span>{name}</span>
      <span>Duet</span>
    </div>
    <div className="film-small" style={{ marginTop: 20, color: theme.muted }}>
      {role}
    </div>
  </Panel>
);

const FileSheet: React.FC<{
  x: number;
  y: number;
  width?: number;
  title?: string;
  version?: string;
  children: React.ReactNode;
  label?: string;
}> = ({
  x,
  y,
  width = 720,
  title = "activation-story.md",
  version = "v1.0",
  children,
  label = "WORKSPACE / SHARED FOR REVIEW",
}) => (
  <Panel x={x} y={y} width={width} style={{ padding: 0 }}>
    <div
      style={{
        padding: "25px 34px",
        borderBottom: `1px solid ${theme.line}`,
        display: "flex",
        alignItems: "center",
        gap: 20,
      }}
    >
      <FileIcon />
      <div style={{ fontSize: 26, fontWeight: 600, flex: 1 }}>{title}</div>
      <span className="film-mono" style={{ color: theme.muted }}>
        {version}
      </span>
    </div>
    <div style={{ padding: 36 }}>
      <div
        className="film-label"
        style={{ fontSize: 17, color: theme.muted, marginBottom: 27 }}
      >
        {label}
      </div>
      {children}
    </div>
  </Panel>
);

const ReviewRow: React.FC<
  Children & { checked?: boolean; muted?: boolean }
> = ({ children, checked = false, muted = false }) => (
  <div
    style={{
      display: "flex",
      alignItems: "flex-start",
      gap: 17,
      padding: "17px 0",
      borderTop: `1px solid ${theme.line}`,
      fontSize: 27,
      lineHeight: 1.35,
      color: muted ? theme.muted : theme.ink,
    }}
  >
    <div style={{ width: 30, flexShrink: 0, paddingTop: 1 }}>
      {checked ? (
        <Tick size={28} />
      ) : (
        <div
          style={{
            marginTop: 7,
            width: 12,
            height: 12,
            border: `2px solid ${theme.muted}`,
          }}
        />
      )}
    </div>
    <div>{children}</div>
  </div>
);

const PersonalLoop = () => (
  <>
    <Beat from={0} duration={9.5} name="A partner beside every person">
      <Heading kicker="02 / PERSONAL PARTNERSHIP" width={900} y={180}>
        Every person.
        <br />
        Their own <span style={{ color: theme.red }}>Duet.</span>
      </Heading>
      <Reveal delay={10}>
        <PersonDuet
          x={1110}
          y={250}
          name="Alex"
          role="Market research"
          width={590}
          active
        />
      </Reveal>
      <FlowLine d="M1110 590H1030V295H1080" delay={40} />
      <FlowLine d="M1700 295H1770V590H1110" delay={60} reverse />
      <Reveal
        delay={60}
        style={{ position: "absolute", left: 1180, top: 650, width: 480 }}
      >
        <div className="film-label" style={{ color: theme.red }}>
          A personal learning loop
        </div>
        <div className="film-body" style={{ marginTop: 20 }}>
          Your context.
          <br />
          Your feedback.
          <br />
          Your decisions.
        </div>
      </Reveal>
      <Note>One person. One working relationship.</Note>
    </Beat>
    <Beat from={9} duration={12.5} name="Understand the real problem">
      <Heading kicker="ILLUSTRATIVE BANKING SCENARIO" y={137} size={73}>
        A signal worth understanding.
      </Heading>
      <Reveal>
        <Panel x={110} y={330} width={650} height={365}>
          <div className="film-eyebrow">CUSTOMER FEEDBACK</div>
          <div
            style={{
              fontSize: 48,
              lineHeight: 1.24,
              letterSpacing: -2,
              marginTop: 37,
            }}
          >
            “I activated my card.
            <br />
            What happens next?”
          </div>
          <div
            className="film-small"
            style={{ marginTop: 31, color: theme.muted }}
          >
            A question, not yet a requirement.
          </div>
        </Panel>
      </Reveal>
      <FlowLine d="M760 510H1000" delay={40} />
      <Reveal delay={60}>
        <Panel x={1000} y={330} width={810} height={450}>
          <div
            style={{
              display: "flex",
              gap: 18,
              alignItems: "center",
              fontSize: 29,
              fontWeight: 600,
            }}
          >
            <DuetMark size={44} />
            Alex’s Duet
          </div>
          <div style={{ fontSize: 35, lineHeight: 1.4, marginTop: 30 }}>
            Let’s understand where the
            <br />
            uncertainty begins.
          </div>
          <div style={{ marginTop: 27 }}>
            <ReviewRow checked>Organise the evidence</ReviewRow>
            <ReviewRow checked>Identify what needs clarification</ReviewRow>
          </div>
        </Panel>
      </Reveal>
      <Note>Researcher and Duet shape the question together.</Note>
    </Beat>
    <Beat from={21} duration={9} name="Every role gains a partner">
      <Heading kicker="02 / PERSONAL PARTNERSHIP" y={155} size={82}>
        The same idea, across every role.
      </Heading>
      <FlowLine d="M550 490H740M1180 490H1370" delay={15} />
      <Reveal delay={0}>
        <PersonDuet x={110} y={355} name="Alex" role="Research" active />
      </Reveal>
      <Reveal delay={25}>
        <PersonDuet
          x={740}
          y={355}
          name="Maya"
          role="Business analysis"
          active
        />
      </Reveal>
      <Reveal delay={50}>
        <PersonDuet x={1370} y={355} name="Jordan" role="Development" active />
      </Reveal>
      <Reveal
        delay={65}
        style={{
          position: "absolute",
          left: 360,
          top: 730,
          width: 1200,
          textAlign: "center",
        }}
      >
        <div style={{ fontSize: 38, letterSpacing: -1 }}>
          Each Duet represents its own person.
        </div>
      </Reveal>
    </Beat>
  </>
);

const StoryLoop = () => (
  <>
    <Beat from={0} duration={11} name="Move from evidence to intent">
      <Heading kicker="03 / FROM SIGNAL TO DECISION" width={800} y={180}>
        Make the need
        <br />
        <span style={{ color: theme.red }}>clear.</span>
      </Heading>
      <div style={{ position: "absolute", left: 115, top: 500, width: 650 }}>
        <div className="film-body">
          Clarify the outcome.
          <br />
          Challenge assumptions.
          <br />
          Keep the evidence close.
        </div>
      </div>
      <Reveal delay={10}>
        <Panel x={1000} y={210} width={740} height={180}>
          <div className="film-label" style={{ color: theme.muted }}>
            THE SIGNAL
          </div>
          <div style={{ fontSize: 37, marginTop: 20 }}>
            “What happens after activation?”
          </div>
        </Panel>
      </Reveal>
      <FlowLine d="M1370 390V460" delay={60} />
      <Reveal delay={80}>
        <Panel
          x={1000}
          y={460}
          width={740}
          height={300}
          style={{ borderLeft: `5px solid ${theme.red}` }}
        >
          <Tag red>Intended outcome</Tag>
          <div
            style={{
              fontSize: 42,
              lineHeight: 1.2,
              letterSpacing: -1.5,
              marginTop: 30,
            }}
          >
            Customers know their
            <br />
            next step with confidence.
          </div>
        </Panel>
      </Reveal>
    </Beat>
    <Beat from={10.5} duration={14} name="Create an explicit user story">
      <Heading
        kicker="03 / FROM SIGNAL TO DECISION"
        width={750}
        y={180}
        size={86}
      >
        A story takes
        <br />
        shape through
        <br />
        <span style={{ color: theme.red }}>conversation.</span>
      </Heading>
      <Panel x={110} y={570} width={690} height={205}>
        <div className="film-label" style={{ color: theme.muted }}>
          BA → DUET
        </div>
        <div style={{ fontSize: 31, lineHeight: 1.35, marginTop: 20 }}>
          “Include the next action and
          <br />a route to help if it is unclear.”
        </div>
      </Panel>
      <Reveal delay={10}>
        <FileSheet x={970} y={160} width={820} version="v0.2">
          <div
            style={{
              fontSize: 39,
              fontWeight: 600,
              letterSpacing: -1,
              marginBottom: 28,
            }}
          >
            A clear next step
          </div>
          <div style={{ fontSize: 29, lineHeight: 1.5, marginBottom: 30 }}>
            As a newly activated cardholder,
            <br />I want to understand what to do next.
          </div>
          <div
            className="film-label"
            style={{ color: theme.red, marginBottom: 17 }}
          >
            Acceptance criteria
          </div>
          <ReviewRow checked>Show confirmation of activation</ReviewRow>
          <ReviewRow checked>Explain the next available action</ReviewRow>
          <ReviewRow checked>Offer a clear route to help</ReviewRow>
        </FileSheet>
      </Reveal>
    </Beat>
    <Beat from={24} duration={16} name="Keep the important decision human">
      <Heading kicker="03 / FROM SIGNAL TO DECISION" y={135} size={76}>
        People decide what moves forward.
      </Heading>
      <Reveal>
        <Panel x={110} y={330} width={760} height={440}>
          <div className="film-label" style={{ color: theme.muted }}>
            BUSINESS REVIEW
          </div>
          <div style={{ fontSize: 41, marginTop: 26, marginBottom: 25 }}>
            Value. Scope. Resources.
          </div>
          <ReviewRow checked>Improve clarity for the customer</ReviewRow>
          <ReviewRow checked>Limit the first change to activation</ReviewRow>
          <ReviewRow checked>Confirm delivery ownership</ReviewRow>
        </Panel>
      </Reveal>
      <FlowLine d="M870 545H1060" delay={40} />
      <Reveal delay={60}>
        <Panel x={1060} y={330} width={750} height={440}>
          <div style={{ display: "flex", alignItems: "center", gap: 22 }}>
            <Person size={55} />
            <div>
              <div className="film-label">BUSINESS OWNER</div>
              <div
                className="film-small"
                style={{ color: theme.muted, marginTop: 8 }}
              >
                Review and authorise
              </div>
            </div>
          </div>
          <div style={{ fontSize: 43, marginTop: 40, marginBottom: 33 }}>
            Proceed with this scope.
          </div>
          <div
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 15,
              background: theme.red,
              color: "white",
              fontSize: 28,
              padding: "20px 30px",
            }}
          >
            <Tick color="white" size={28} />
            Scope confirmed
          </div>
        </Panel>
      </Reveal>
      <Note>Duet prepares the decision. The owner makes it.</Note>
    </Beat>
  </>
);

const MessageCenterLoop = () => (
  <>
    <Beat from={0} duration={11} name="Publish once with a clear destination">
      <Heading kicker="04 / MESSAGE CENTER" y={140} size={78}>
        Give every handoff a destination.
      </Heading>
      <Reveal delay={0}>
        <Panel x={110} y={365} width={445} height={300}>
          <DuetMark size={65} />
          <div style={{ fontSize: 33, fontWeight: 600, marginTop: 23 }}>
            Maya’s Duet
          </div>
          <div
            className="film-small"
            style={{ color: theme.muted, marginTop: 16 }}
          >
            Publishes the agreed story
          </div>
        </Panel>
      </Reveal>
      <FlowLine d="M555 505H735M1175 505H1355" delay={30} />
      <Reveal delay={40}>
        <Panel
          x={735}
          y={330}
          width={440}
          height={370}
          style={{
            background: theme.ink,
            color: "white",
            borderColor: theme.ink,
          }}
        >
          <div style={{ fontSize: 22, letterSpacing: 2.5, color: "#D6D6D2" }}>
            SHARED INFORMATION
          </div>
          <div
            style={{
              fontSize: 49,
              letterSpacing: -2,
              lineHeight: 1.1,
              marginTop: 35,
            }}
          >
            Message
            <br />
            Center
          </div>
          <div
            style={{
              height: 4,
              width: 65,
              background: theme.red,
              marginTop: 37,
            }}
          />
          <div style={{ fontSize: 25, marginTop: 25, color: "#D6D6D2" }}>
            Publish · Deliver · Respond
          </div>
        </Panel>
      </Reveal>
      <Reveal delay={70}>
        <Panel x={1355} y={365} width={455} height={300}>
          <div className="film-label" style={{ color: theme.muted }}>
            SELECTED RECIPIENT
          </div>
          <div style={{ fontSize: 36, marginTop: 32, fontWeight: 600 }}>
            Scrum Master’s
            <br />
            Duet
          </div>
          <div
            className="film-small"
            style={{ color: theme.muted, marginTop: 22 }}
          >
            Receives the handoff
          </div>
        </Panel>
      </Reveal>
      <Note>Sources, people and Duets can contribute to the shared flow.</Note>
    </Beat>
    <Beat from={10.5} duration={10.5} name="A durable visible handoff">
      <Heading
        kicker="04 / MESSAGE CENTER"
        x={110}
        y={160}
        width={650}
        size={85}
      >
        Context travels
        <br />
        with the work.
      </Heading>
      <div style={{ position: "absolute", left: 110, top: 490, width: 650 }}>
        <div className="film-body">
          A source you can trace.
          <br />A file you can review.
          <br />A recipient you can identify.
        </div>
      </div>
      <Reveal>
        <Panel x={890} y={160} width={920} height={650} style={{ padding: 0 }}>
          <div
            style={{
              padding: "26px 36px",
              background: theme.ink,
              color: "white",
              fontSize: 23,
              display: "flex",
              justifyContent: "space-between",
            }}
          >
            <span>Message Center</span>
            <span style={{ color: "#CCCCCA" }}>Publication</span>
          </div>
          <div style={{ padding: 36 }}>
            <div className="film-eyebrow">PRODUCT REQUIREMENT</div>
            <div
              style={{
                fontSize: 43,
                fontWeight: 600,
                letterSpacing: -1.5,
                margin: "24px 0",
              }}
            >
              A clearer activation journey
            </div>
            <div
              style={{
                display: "flex",
                gap: 18,
                padding: "23px 0",
                borderTop: `1px solid ${theme.line}`,
                borderBottom: `1px solid ${theme.line}`,
              }}
            >
              <FileIcon />
              <div>
                <div style={{ fontSize: 29 }}>activation-story.md</div>
                <div
                  className="film-small"
                  style={{ color: theme.muted, marginTop: 7 }}
                >
                  Approved snapshot · v1.0
                </div>
              </div>
            </div>
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "180px 1fr",
                rowGap: 22,
                marginTop: 27,
                fontSize: 27,
              }}
            >
              <span style={{ color: theme.muted }}>Published by</span>
              <span>Maya’s Duet</span>
              <span style={{ color: theme.muted }}>Audience</span>
              <span>Named delivery owner</span>
              <span style={{ color: theme.muted }}>Delivery</span>
              <span style={{ display: "flex", gap: 12, alignItems: "center" }}>
                <Tick size={25} />
                Received by Duet
              </span>
            </div>
          </div>
        </Panel>
      </Reveal>
    </Beat>
    <Beat from={20.5} duration={9.5} name="The conversation comes back">
      <Heading kicker="04 / MESSAGE CENTER" y={150} size={78}>
        The flow works both ways.
      </Heading>
      <Panel x={110} y={360} width={700} height={330}>
        <Tag>Delivery owner’s Duet</Tag>
        <div style={{ fontSize: 36, lineHeight: 1.3, marginTop: 32 }}>
          “Which customer journey
          <br />
          should we prioritise first?”
        </div>
      </Panel>
      <Panel x={1110} y={360} width={700} height={330}>
        <Tag red>BA’S DUET + OWNER</Tag>
        <div style={{ fontSize: 36, lineHeight: 1.3, marginTop: 32 }}>
          “Start with the first
          <br />
          activation confirmation.”
        </div>
      </Panel>
      <FlowLine d="M810 435H1110" delay={25} />
      <FlowLine d="M1110 610H810" delay={70} reverse />
      <Note>
        Publish updates through the hub. Resolve details through approved Duet
        channels.
      </Note>
    </Beat>
  </>
);

const DeliveryLoop = () => (
  <>
    <Beat from={0} duration={13.5} name="Coordinate with the right people">
      <Heading kicker="05 / CONNECTED DELIVERY" y={140} size={79}>
        Bring the right people into the loop.
      </Heading>
      <FlowLine d="M550 500H740M1180 500H1370" delay={30} />
      <Reveal>
        <PersonDuet x={110} y={350} name="Taylor" role="Scrum Master" active />
      </Reveal>
      <Reveal delay={30}>
        <PersonDuet x={740} y={350} name="Jordan" role="Development" active />
      </Reveal>
      <Reveal delay={60}>
        <PersonDuet
          x={1370}
          y={350}
          name="Riley"
          role="Testing & acceptance"
          active
        />
      </Reveal>
      <Reveal delay={85}>
        <Panel
          x={465}
          y={685}
          width={990}
          height={130}
          style={{
            display: "flex",
            alignItems: "center",
            gap: 23,
            padding: "25px 35px",
          }}
        >
          <Tick size={36} />
          <div>
            <div style={{ fontSize: 29, fontWeight: 600 }}>
              Owner-approved collaboration
            </div>
            <div
              className="film-small"
              style={{ color: theme.muted, marginTop: 9 }}
            >
              Clarify dependencies, capacity and responsibilities.
            </div>
          </div>
        </Panel>
      </Reveal>
    </Beat>
    <Beat from={13} duration={13} name="Delegate the detailed work">
      <Heading kicker="05 / CONNECTED DELIVERY" y={145} width={800} size={83}>
        Duet coordinates.
        <br />
        <span style={{ color: theme.red }}>Workers execute.</span>
      </Heading>
      <div style={{ position: "absolute", left: 110, top: 520, width: 600 }}>
        <div className="film-body">
          Independent task context.
          <br />
          Shared authorised tools.
          <br />
          Results brought back for review.
        </div>
      </div>
      <Panel
        x={940}
        y={165}
        width={770}
        height={145}
        style={{ display: "flex", gap: 25, alignItems: "center" }}
      >
        <DuetMark size={64} />
        <div>
          <div style={{ fontSize: 34, fontWeight: 600 }}>Jordan’s Duet</div>
          <div
            className="film-small"
            style={{ color: theme.muted, marginTop: 10 }}
          >
            Keeps the person informed
          </div>
        </div>
      </Panel>
      <FlowLine
        d="M1325 310V370H1065V435M1325 370H1585V435M1065 630V700H1585V630"
        delay={30}
      />
      <Reveal delay={45}>
        <Panel x={895} y={435} width={340} height={230}>
          <div className="film-label" style={{ color: theme.red }}>
            WORKER 01
          </div>
          <div style={{ fontSize: 34, fontWeight: 600, marginTop: 25 }}>
            Draft change
          </div>
          <div
            className="film-small"
            style={{ color: theme.muted, marginTop: 18 }}
          >
            Implementation plan
          </div>
        </Panel>
      </Reveal>
      <Reveal delay={65}>
        <Panel x={1415} y={435} width={340} height={230}>
          <div className="film-label" style={{ color: theme.red }}>
            WORKER 02
          </div>
          <div style={{ fontSize: 34, fontWeight: 600, marginTop: 25 }}>
            Check the work
          </div>
          <div
            className="film-small"
            style={{ color: theme.muted, marginTop: 18 }}
          >
            Review evidence
          </div>
        </Panel>
      </Reveal>
      <Reveal delay={95} style={{ position: "absolute", left: 1115, top: 746 }}>
        <Tag red>
          <Tick size={24} />
          Results returned
        </Tag>
      </Reveal>
    </Beat>
    <Beat from={25.5} duration={12} name="Evidence supports acceptance">
      <Heading kicker="05 / CONNECTED DELIVERY" y={180} width={750} size={88}>
        Make progress
        <br />
        <span style={{ color: theme.red }}>reviewable.</span>
      </Heading>
      <Panel x={110} y={535} width={650} height={220}>
        <div className="film-label" style={{ color: theme.muted }}>
          TRACKING
        </div>
        <div style={{ fontSize: 32, lineHeight: 1.3, marginTop: 25 }}>
          Scope agreed.
          <br />
          Evidence ready.
          <br />
          Owner decision next.
        </div>
      </Panel>
      <Reveal>
        <FileSheet
          x={940}
          y={160}
          width={830}
          title="acceptance-review.md"
          version="v1.0"
          label="ILLUSTRATIVE DELIVERY EVIDENCE"
        >
          <div style={{ fontSize: 41, fontWeight: 600, marginBottom: 25 }}>
            Ready for acceptance review
          </div>
          <ReviewRow checked>Activation confirmation is clear</ReviewRow>
          <ReviewRow checked>The next step is easy to find</ReviewRow>
          <ReviewRow checked>Help remains accessible</ReviewRow>
          <div
            style={{
              marginTop: 30,
              padding: 24,
              background: theme.paleRed,
              fontSize: 28,
              lineHeight: 1.4,
            }}
          >
            Evidence supports a decision.
            <br />
            It does not replace the owner’s judgment.
          </div>
        </FileSheet>
      </Reveal>
    </Beat>
    <Beat from={37} duration={7.5} name="Human authority at release">
      <Heading kicker="ILLUSTRATIVE FUTURE WORKFLOW" y={145} size={79}>
        Important decisions stay with people.
      </Heading>
      <Reveal>
        <Panel
          x={420}
          y={330}
          width={1080}
          height={380}
          style={{ padding: 45 }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 25 }}>
            <Person size={65} />
            <div>
              <div className="film-label" style={{ color: theme.muted }}>
                RELEASE OWNER
              </div>
              <div style={{ fontSize: 34, fontWeight: 600, marginTop: 12 }}>
                Review the evidence. Decide the next step.
              </div>
            </div>
          </div>
          <div style={{ display: "flex", gap: 25, marginTop: 52 }}>
            <div
              style={{
                padding: "24px 33px",
                background: theme.red,
                color: "white",
                fontSize: 31,
                display: "flex",
                gap: 15,
                alignItems: "center",
              }}
            >
              <Tick color="white" />
              Authorise release
            </div>
            <div
              style={{
                padding: "24px 33px",
                border: `1px solid ${theme.line}`,
                fontSize: 31,
              }}
            >
              Request changes
            </div>
          </div>
        </Panel>
      </Reveal>
      <Note>
        External release and operational integrations are shown as a future
        workflow.
      </Note>
    </Beat>
    <Beat from={44} duration={6} name="Return the outcome to the loop">
      <Heading kicker="05 / CONNECTED DELIVERY" y={145} size={78}>
        Delivery creates the next signal.
      </Heading>
      <AngularLoop
        x={470}
        y={230}
        scale={0.9}
        duets
        phase={5}
        center={
          <>
            <div className="film-eyebrow">CUSTOMER FEEDBACK</div>
            <div
              style={{
                fontSize: 43,
                letterSpacing: -2,
                lineHeight: 1.2,
                marginTop: 24,
              }}
            >
              Where does
              <br />
              uncertainty remain?
            </div>
          </>
        }
      />
    </Beat>
  </>
);

const GrowthDial: React.FC = () => {
  const f = useCurrentFrame();
  const { fps } = useVideoConfig();
  const t = f / fps;
  const stage = t < 3.5 ? 0 : t < 5.5 ? 1 : 2;
  const duet = [10, 50, 95][stage];
  const human = 100 - duet;
  const p =
    stage === 0
      ? motion(f, 0, 25, 0, 0.1)
      : stage === 1
        ? motion(f, 3.5 * fps, 3.5 * fps + 25, 0.1, 0.5)
        : motion(f, 5.5 * fps, 5.5 * fps + 25, 0.5, 0.95);
  return (
    <>
      <svg
        width="670"
        height="660"
        viewBox="-30 -30 660 660"
        style={{ position: "absolute", left: 1085, top: 238 }}
      >
        <path
          d="M160 25H440L575 160V440L440 575H160L25 440V160Z"
          fill="none"
          stroke={theme.ink}
          strokeWidth="36"
        />
        <path
          d="M160 25H440L575 160V440L440 575H160L25 440V160Z"
          fill="none"
          stroke={theme.red}
          strokeWidth="36"
          pathLength="100"
          strokeDasharray={`${p * 100} 100`}
        />
        <path
          d="M169 80H423L520 177V423L423 520H177L80 423V177Z"
          fill="none"
          stroke={theme.line}
          strokeWidth="1"
        />
      </svg>
      <div
        style={{
          position: "absolute",
          left: 1230,
          top: 436,
          width: 370,
          textAlign: "center",
        }}
      >
        <div
          style={{
            fontSize: 134,
            fontWeight: 600,
            letterSpacing: -9,
            lineHeight: 1,
            color: theme.red,
          }}
        >
          {duet}%
        </div>
        <div style={{ fontSize: 29, marginTop: 20, color: theme.muted }}>
          Duet contribution
        </div>
      </div>
      <div style={{ position: "absolute", left: 110, top: 535, width: 760 }}>
        <div style={{ display: "flex", gap: 85, alignItems: "baseline" }}>
          <div>
            <div style={{ fontSize: 92, letterSpacing: -5, fontWeight: 600 }}>
              {human}%
            </div>
            <div style={{ fontSize: 25, color: theme.muted }}>
              Human {stage === 2 ? "review" : "contribution"}
            </div>
          </div>
          <div style={{ fontSize: 48, color: theme.line }}>+</div>
          <div>
            <div
              style={{
                fontSize: 92,
                letterSpacing: -5,
                fontWeight: 600,
                color: theme.red,
              }}
            >
              {duet}%
            </div>
            <div style={{ fontSize: 25, color: theme.muted }}>
              Duet contribution
            </div>
          </div>
        </div>
        <div style={{ marginTop: 43, display: "flex", gap: 14 }}>
          {["Assist", "Collaborate", "Orchestrate"].map((s, i) => (
            <span
              key={s}
              style={{
                fontSize: 23,
                padding: "13px 21px",
                background: stage === i ? theme.ink : "transparent",
                color: stage === i ? "white" : theme.muted,
                border: `1px solid ${stage === i ? theme.ink : theme.line}`,
              }}
            >
              {s}
            </span>
          ))}
        </div>
      </div>
      <div
        style={{
          position: "absolute",
          left: 94,
          top: 520,
          width: 235,
          height: 170,
          borderLeft: `3px solid ${theme.red}`,
          opacity: motion(f, 12 * fps, 12 * fps + 25),
        }}
      />
      <div
        style={{
          position: "absolute",
          left: 110,
          top: 814,
          display: "flex",
          alignItems: "center",
          gap: 14,
          fontSize: 27,
          color: theme.red,
          opacity: motion(f, 12 * fps, 12 * fps + 25),
          translate: `0 ${motion(f, 12 * fps, 12 * fps + 35, 15, 0)}px`,
        }}
      >
        <Person size={33} color={theme.red} />
        Key approvals remain human.
      </div>
      <Note y={880}>
        Illustrative ambition for suitable workflows. Human accountability
        remains.
      </Note>
    </>
  );
};

const LearningLoop = () => (
  <>
    <Beat from={0} duration={15.5} name="Experience becomes useful context">
      <Heading kicker="06 / LEARNING THROUGH EXPERIENCE" y={145} size={79}>
        The relationship gets more informed.
      </Heading>
      <Reveal>
        <Panel x={110} y={340} width={700} height={225}>
          <div className="film-label" style={{ color: theme.muted }}>
            OWNER FEEDBACK
          </div>
          <div style={{ fontSize: 35, lineHeight: 1.3, marginTop: 25 }}>
            “Keep acceptance criteria explicit.
            <br />
            Show me the decision needed.”
          </div>
        </Panel>
      </Reveal>
      <FlowLine d="M810 452H1030M1405 700V795H460V565" delay={45} />
      <Reveal delay={60}>
        <Panel x={1030} y={300} width={750} height={400}>
          <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
            <DuetMark size={43} />
            <div className="film-label">MEMORY + DREAM</div>
          </div>
          <div style={{ fontSize: 33, marginTop: 24, marginBottom: 22 }}>
            Keep what is useful and supported.
          </div>
          <ReviewRow checked>Working preferences</ReviewRow>
          <ReviewRow checked>Project context and responsibilities</ReviewRow>
          <ReviewRow checked>Verified experience, with sources</ReviewRow>
        </Panel>
      </Reveal>
      <Note y={855}>
        Experience informs future work. Owners can review, correct and remove
        memories.
      </Note>
    </Beat>
    <Beat from={15} duration={25} name="Grow the contribution over time">
      <Heading
        kicker="06 / AN EVOLVING PARTNERSHIP"
        x={110}
        y={190}
        width={950}
        size={88}
      >
        More context.
        <br />
        <span style={{ color: theme.red }}>Greater contribution.</span>
      </Heading>
      <GrowthDial />
    </Beat>
  </>
);

const ClosingLoop = () => {
  const f = useCurrentFrame();
  const { fps } = useVideoConfig();
  const t = f / fps;
  return (
    <>
      <Beat from={0} duration={12} name="Connect an organisation of Duets">
        <Heading kicker="07 / THE AI-NATIVE BUSINESS LOOP" y={145} size={66}>
          Connected people. A continuously learning business.
        </Heading>
        <AngularLoop
          x={475}
          y={275}
          scale={0.89}
          duets
          phase={4}
          center={
            <>
              <DuetMark size={65} />
              <div
                style={{
                  fontSize: 39,
                  marginTop: 18,
                  fontWeight: 600,
                  letterSpacing: -1.5,
                }}
              >
                An AI-native
                <br />
                business loop.
              </div>
            </>
          }
        />
      </Beat>
      <Beat from={11.5} duration={7.5} name="Three promises">
        <div style={{ position: "absolute", left: 110, top: 240, width: 1700 }}>
          <div style={{ fontSize: 83, letterSpacing: -4, lineHeight: 1.22 }}>
            Every person, <span style={{ color: theme.red }}>a Duet.</span>
          </div>
          <Reveal delay={35}>
            <div
              style={{
                fontSize: 83,
                letterSpacing: -4,
                lineHeight: 1.22,
                marginTop: 28,
              }}
            >
              Every handoff,{" "}
              <span style={{ color: theme.red }}>a connection.</span>
            </div>
          </Reveal>
          <Reveal delay={70}>
            <div
              style={{
                fontSize: 83,
                letterSpacing: -4,
                lineHeight: 1.22,
                marginTop: 28,
              }}
            >
              Every cycle,{" "}
              <span style={{ color: theme.red }}>a better business.</span>
            </div>
          </Reveal>
        </div>
      </Beat>
      {t >= 18.5 && (
        <AbsoluteFill
          className="film-scene"
          style={{ opacity: motion(f, 18.5 * fps, 19.2 * fps) }}
        >
          <div
            style={{
              position: "absolute",
              left: 0,
              width: 1920,
              top: 290,
              display: "flex",
              justifyContent: "center",
              alignItems: "center",
              gap: 38,
            }}
          >
            <DuetMark size={165} />
            <div style={{ fontSize: 185, fontWeight: 600, letterSpacing: -12 }}>
              Duet
            </div>
          </div>
          <div
            style={{
              position: "absolute",
              left: 260,
              top: 565,
              width: 1400,
              textAlign: "center",
              fontSize: 48,
              letterSpacing: -1.5,
            }}
          >
            A human-led, AI-native organisation.
          </div>
          <div
            style={{
              position: "absolute",
              left: 900,
              top: 690,
              width: 120,
              height: 5,
              background: theme.red,
            }}
          />
          <div
            className="film-eyebrow"
            style={{
              position: "absolute",
              left: 400,
              top: 768,
              width: 1120,
              textAlign: "center",
              fontSize: 20,
            }}
          >
            FROM PERSONAL ASSISTANCE TO CONNECTED BUSINESS
          </div>
        </AbsoluteFill>
      )}
    </>
  );
};

export const FilmVisuals: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const current = Math.max(
    0,
    chapters.findIndex((c) => frame / fps >= c.start && frame / fps < c.end),
  );
  return (
    <AbsoluteFill className="film-root">
      <Sequence name="The business loop" durationInFrames={25 * fps}>
        <BusinessLoop />
      </Sequence>
      <Sequence
        name="A Duet for every person"
        from={25 * fps}
        durationInFrames={30 * fps}
      >
        <PersonalLoop />
      </Sequence>
      <Sequence
        name="From signal to decision"
        from={55 * fps}
        durationInFrames={40 * fps}
      >
        <StoryLoop />
      </Sequence>
      <Sequence
        name="The message center"
        from={95 * fps}
        durationInFrames={30 * fps}
      >
        <MessageCenterLoop />
      </Sequence>
      <Sequence
        name="Connected delivery"
        from={125 * fps}
        durationInFrames={50 * fps}
      >
        <DeliveryLoop />
      </Sequence>
      <Sequence
        name="Learning through experience"
        from={175 * fps}
        durationInFrames={40 * fps}
      >
        <LearningLoop />
      </Sequence>
      <Sequence
        name="An AI-native organisation"
        from={215 * fps}
        durationInFrames={25 * fps}
      >
        <ClosingLoop />
      </Sequence>
      <div
        style={{
          position: "absolute",
          left: 110,
          top: 34,
          display: "flex",
          alignItems: "center",
          gap: 13,
        }}
      >
        <DuetMark size={37} />
        <span style={{ fontSize: 27, fontWeight: 700, letterSpacing: -1 }}>
          Duet
        </span>
      </div>
      <div
        style={{
          position: "absolute",
          right: 110,
          top: 47,
          fontSize: 18,
          letterSpacing: 2.5,
          textTransform: "uppercase",
          color: theme.muted,
        }}
      >
        {String(current + 1).padStart(2, "0")} / {chapters[current].name}
      </div>
      <div
        style={{
          position: "absolute",
          left: 110,
          top: 90,
          width: 1700,
          height: 1,
          background: theme.line,
        }}
      />
      <div
        style={{
          position: "absolute",
          left: 110,
          top: 90,
          width: (1700 * frame) / (240 * fps - 1),
          height: 2,
          background: theme.red,
        }}
      />
    </AbsoluteFill>
  );
};
