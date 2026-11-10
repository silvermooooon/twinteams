import type { CSSProperties } from "react";
import { interpolate, useCurrentFrame, useVideoConfig } from "remotion";
import {
  C,
  Frame,
  type Language,
  type SceneProps,
  tr,
  useSteps,
} from "./Shared";

type Focus =
  | "overview"
  | "loop"
  | "message"
  | "lab"
  | "duet"
  | "owner"
  | "topic";
type Flow = "information" | "policy" | "acceptance";
type Point = readonly [number, number];

const PEOPLE = [
  { name: "Nora", owner: [155, 65], duet: [332, 150] },
  { name: "Maya", owner: [965, 65], duet: [788, 150] },
  { name: "Alex", owner: [1040, 330], duet: [823, 330] },
  { name: "Sam", owner: [965, 595], duet: [788, 510] },
  { name: "", owner: [155, 595], duet: [332, 510] },
  { name: "", owner: [80, 330], duet: [297, 330] },
] as const;

const POLICY_ARC: Point[] = Array.from({ length: 25 }, (_, i) => {
  const angle = ((-135 + i * (90 / 24)) * Math.PI) / 180;
  return [560 + 205 * Math.cos(angle), 330 + 205 * Math.sin(angle)];
});
const ROUTES: Record<Flow, Point[]> = {
  information: [
    [410, 290],
    [332, 150],
    [155, 65],
    [332, 150],
  ],
  policy: [[332, 150], ...POLICY_ARC, [788, 150]],
  acceptance: [
    [765, 330],
    [710, 418],
    [560, 418],
    [560, 465],
    [560, 535],
  ],
};

function EventPulse({ flow, elapsed }: { flow: Flow; elapsed: number }) {
  const { fps } = useVideoConfig();
  const points = ROUTES[flow];
  const lengths = points
    .slice(1)
    .map((point, i) =>
      Math.hypot(point[0] - points[i][0], point[1] - points[i][1]),
    );
  const total = lengths.reduce((sum, length) => sum + length, 0);
  let distance = ((elapsed % (6 * fps)) / (6 * fps)) * total;
  let segment = 0;
  while (segment < lengths.length - 1 && distance > lengths[segment]) {
    distance -= lengths[segment];
    segment++;
  }
  const portion = distance / lengths[segment];
  const x =
    points[segment][0] +
    (points[segment + 1][0] - points[segment][0]) * portion;
  const y =
    points[segment][1] +
    (points[segment + 1][1] - points[segment][1]) * portion;
  const color = flow === "information" ? C.green : C.red;
  return (
    <g>
      <polyline
        points={points.map((point) => point.join(",")).join(" ")}
        fill="none"
        stroke={color}
        strokeWidth={3}
        strokeDasharray={flow === "information" ? "7 9" : undefined}
        opacity={0.65}
      />
      <circle cx={x} cy={y} r={15} fill={color} opacity={0.14} />
      <circle cx={x} cy={y} r={6} fill={color} />
    </g>
  );
}

/** The same spatial model appears in the introduction and closing overview. */
export function ArchitectureDiagram({
  language,
  stage = 7,
  elapsed = 60,
  focus = "overview",
  flow,
  style,
}: {
  language: Language;
  stage?: number;
  elapsed?: number;
  focus?: Focus;
  flow?: Flow;
  style?: CSSProperties;
}) {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const entrance = interpolate(elapsed, [0, 0.7 * fps], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const ringVisible = stage >= 1;
  const messageVisible = stage >= 2;
  const labVisible = stage >= 3;
  const duetsVisible = stage >= 4;
  const ownersVisible = stage >= 5;
  const ringActive = focus === "loop" || stage === 1;
  const messageActive = focus === "message" || stage === 2;
  const labActive = focus === "lab" || stage === 3;
  const duetsActive = focus === "duet" || stage === 4;
  const ownersActive = focus === "owner" || stage === 5;
  const roleOpacity = (visible: boolean, threshold: number) =>
    visible ? (stage === threshold ? entrance : 1) : 0.14;
  return (
    <svg
      viewBox="0 0 1120 660"
      aria-label={tr(
        language,
        "Agent Loop 圆环内是消息中心与 LoopLab，圆环外围是各自连接 Owner 的 Duet",
        "Message Center and LoopLab inside Agent Loop; personal Duets and their Owners outside",
      )}
      style={{
        display: "block",
        width: 1120,
        height: 610,
        overflow: "visible",
        ...style,
      }}
    >
      {PEOPLE.map(({ name, owner, duet }, index) => (
        <line
          key={`owner-link-${index}`}
          x1={duet[0]}
          y1={duet[1]}
          x2={owner[0]}
          y2={owner[1]}
          stroke={ownersActive && index === 0 ? C.red : C.line}
          strokeWidth={ownersActive && index === 0 ? 4 : 2.5}
          opacity={roleOpacity(ownersVisible, 5)}
        >
          <title>
            {name || tr(language, "员工", "Employee")} →{" "}
            {tr(language, "自己的 Duet", "their own Duet")}
          </title>
        </line>
      ))}
      <circle
        cx={560}
        cy={330}
        r={205}
        fill={C.white}
        stroke={C.line}
        strokeWidth={2.5}
      />
      <circle
        cx={560}
        cy={330}
        r={205}
        fill="none"
        stroke={ringActive ? C.red : "#a9aeaa"}
        strokeWidth={ringActive ? 4.5 : 3}
        pathLength={1}
        strokeDasharray={1}
        strokeDashoffset={stage === 1 ? 1 - entrance : 0}
        opacity={ringVisible ? 1 : 0}
        style={{ rotate: "-90deg", transformOrigin: "560px 330px" }}
      />
      {ringVisible && !flow && (
        <circle
          cx={560 + 205 * Math.cos(frame / (6 * fps))}
          cy={330 + 205 * Math.sin(frame / (6 * fps))}
          r={5}
          fill={C.red}
          opacity={0.8}
        />
      )}
      {flow && <EventPulse flow={flow} elapsed={elapsed} />}
      <g opacity={roleOpacity(ringVisible, 1)}>
        <text
          x={560}
          y={183}
          textAnchor="middle"
          fill={ringActive ? C.red : C.ink}
          fontSize={36}
          fontWeight={700}
        >
          Agent Loop
        </text>
        <text x={560} y={215} textAnchor="middle" fill={C.muted} fontSize={21}>
          {tr(language, "Policy · 事件与任务", "Policy · events & tasks")}
        </text>
      </g>
      <g opacity={roleOpacity(messageVisible, 2)}>
        <rect
          x={410}
          y={247}
          width={300}
          height={94}
          rx={15}
          fill={messageActive ? C.wash : C.paper}
          stroke={messageActive ? C.red : C.line}
          strokeWidth={messageActive ? 3 : 1.5}
        />
        <text
          x={560}
          y={287}
          textAnchor="middle"
          fill={C.ink}
          fontSize={30}
          fontWeight={650}
        >
          Message Center
        </text>
        <text x={560} y={319} textAnchor="middle" fill={C.muted} fontSize={22}>
          {tr(language, "信息接入与发现", "Information & discovery")}
        </text>
      </g>
      <g opacity={roleOpacity(labVisible, 3)}>
        <rect
          x={410}
          y={371}
          width={300}
          height={94}
          rx={15}
          fill={labActive ? C.wash : C.paper}
          stroke={labActive ? C.red : C.line}
          strokeWidth={labActive ? 3 : 1.5}
        />
        <text
          x={560}
          y={411}
          textAnchor="middle"
          fill={C.ink}
          fontSize={32}
          fontWeight={650}
        >
          LoopLab
        </text>
        <text x={560} y={443} textAnchor="middle" fill={C.muted} fontSize={22}>
          {tr(language, "测试与验收证据", "Tests & evidence")}
        </text>
      </g>
      {PEOPLE.map(({ name, owner, duet }, index) => (
        <g key={`person-${index}`}>
          <g opacity={roleOpacity(duetsVisible, 4)}>
            <rect
              x={duet[0] - 56}
              y={duet[1] - 31}
              width={112}
              height={62}
              rx={17}
              fill={duetsActive ? C.wash : C.white}
              stroke={
                duetsActive ||
                (flow === "information" && index === 0) ||
                (flow === "policy" && index < 2)
                  ? C.red
                  : "#bfc5be"
              }
              strokeWidth={
                duetsActive ||
                (flow === "information" && index === 0) ||
                (flow === "policy" && index < 2)
                  ? 3
                  : 2
              }
            />
            <text
              x={duet[0]}
              y={duet[1] + 10}
              textAnchor="middle"
              fill={C.ink}
              fontSize={29}
              fontWeight={650}
            >
              Duet
            </text>
          </g>
          <g opacity={roleOpacity(ownersVisible, 5)}>
            <circle
              cx={owner[0]}
              cy={owner[1]}
              r={26}
              fill={ownersActive ? C.red : C.ink}
            />
            <circle cx={owner[0]} cy={owner[1] - 7} r={6} fill={C.white} />
            <path
              d={`M ${owner[0] - 11} ${owner[1] + 13} C ${owner[0] - 11} ${owner[1] - 2}, ${owner[0] + 11} ${owner[1] - 2}, ${owner[0] + 11} ${owner[1] + 13}`}
              fill={C.white}
            />
            <text
              x={owner[0]}
              y={owner[1] + 49}
              textAnchor="middle"
              fill={C.ink}
              fontSize={23}
              fontWeight={550}
            >
              {name ? `${name} · Owner` : "Owner"}
            </text>
          </g>
        </g>
      ))}
    </svg>
  );
}

export function S00Architecture({ language, cues }: SceneProps) {
  const { step, elapsed } = useSteps(cues, language, [
    ["中间的圆环", "At the center"],
    ["消息中心汇集", "Message Center gathers"],
    ["LoopLab 负责测试", "LoopLab runs tests"],
    ["圆环外围", "Around the ring"],
    ["每个 Duet 再向外连接", "Each Duet connects outward"],
    ["需要跨岗位协作时", "Across roles"],
    ["现在看信息怎样流动", "Now follow one flow"],
    ["任务结果回到", "A task result returns"],
    ["需要验收时", "When acceptance is needed"],
    ["人做判断", "People judge"],
    ["Duet 做事", "Duets do the work"],
    ["消息中心提供信息", "Message Center supplies information"],
    ["LoopLab 提供验收", "LoopLab supplies acceptance evidence"],
    ["Agent Loop 连接各个环节", "Agent Loop connects the steps"],
    ["接下来", "Next, we will see"],
  ]);
  const { fps } = useVideoConfig();
  const focus: Focus =
    step === 1 || step === 8 || step === 14
      ? "loop"
      : step === 2 || step === 7 || step === 12
        ? "message"
        : step === 3 || step === 9 || step === 13
          ? "lab"
          : step === 4 || step === 11
            ? "duet"
            : step === 5 || step === 10
              ? "owner"
              : step === 6
                ? "topic"
                : "overview";
  const flow: Flow | undefined =
    step === 7
      ? "information"
      : step === 8
        ? "policy"
        : step === 9
          ? "acceptance"
          : undefined;
  const cards = [
    [
      "全局地图",
      "The whole map",
      "先认识位置，再看工作怎样流转。",
      "Meet the components before following the work.",
      "共享能力在中间，\n每个人的 Duet 在外围。",
      "Shared services at the center.\nPersonal Duets around them.",
    ],
    [
      "Agent Loop",
      "Agent Loop",
      "按 policy 接起事件与任务。",
      "Connect events and tasks through policy.",
      "接收事件 → 分发任务 → 承接结果\n确定性调度，不调用大模型。",
      "Receive → route → continue\nDeterministic routing; no LLM.",
    ],
    [
      "消息中心",
      "Message Center",
      "让信息进入公司，也让信息被找到。",
      "Bring information in, and make it discoverable.",
      "新闻 · 业务通知 · 工作产出\n可阅读、搜索和订阅。",
      "News · notifications · work outputs\nReadable, searchable, subscribable.",
    ],
    [
      "LoopLab",
      "LoopLab",
      "用测试与证据完成验收。",
      "Test the work and produce evidence.",
      "执行测试，记录结果。\n验收产出可以触发下一步。",
      "Run tests. Record the results.\nAcceptance can trigger the next step.",
    ],
    [
      "个人 Duet",
      "Personal Duet",
      "理解任务，承担自己 Owner 的工作。",
      "Understand and carry out its Owner’s work.",
      "调用工具 · 完成任务\n用记忆与 Tracking 持续跟进。",
      "Use tools · complete tasks\nFollow through with memory and Tracking.",
    ],
    [
      "Owner · 员工",
      "Owner · the employee",
      "决定目标和取舍。",
      "Set goals and make judgments.",
      "每人连接自己的 Duet。\n任务从这段关系开始。",
      "Each person has their own Duet.\nDelegation begins with this relationship.",
    ],
    [
      "共享主题",
      "Shared topic",
      "跨岗位协作，在主题中留下工作。",
      "Coordinate across roles in a shared topic.",
      "任务、资料和结果持续汇集。\nDuet 带着各自职责异步参与。",
      "Tasks, materials and results accumulate.\nEach Duet contributes asynchronously.",
    ],
    [
      "从信息到行动",
      "From information to action",
      "Duet 发现相关信息，向自己的 Owner 提议。",
      "Duet finds relevant information and proposes an action.",
      "Owner 决定推进，\n自己的 Duet 开始处理。",
      "The Owner decides to proceed.\nTheir Duet begins the work.",
    ],
    [
      "从结果到下一步",
      "From result to next step",
      "完成结果回到 Agent Loop。",
      "A completed result returns to Agent Loop.",
      "Policy 识别下一步，\n交给下一位负责人的 Duet。",
      "Policy identifies the next step\nand passes it to the next person’s Duet.",
    ],
    [
      "验收也进入循环",
      "Acceptance joins the loop",
      "LoopLab 产出可用的验收结果。",
      "LoopLab returns acceptance results with evidence.",
      "测试结果回到 Agent Loop，\n继续推动工作流转。",
      "Test results return to Agent Loop\nand keep the workflow moving.",
    ],
  ];
  const card = cards[Math.min(step, 9)];
  const summary = [
    ["owner", "Owner", "判断与取舍", "Goals and judgment"],
    ["duet", "Duet", "理解并完成工作", "Understand and do"],
    ["message", "Message Center", "提供可用的信息", "Information"],
    ["lab", "LoopLab", "验收结果与证据", "Acceptance evidence"],
    ["loop", "Agent Loop", "按 policy 接起闭环", "Policy connects the steps"],
  ];
  return (
    <Frame
      number={0}
      title={tr(
        language,
        "先看懂这张架构图",
        "Meet the architecture before the story",
      )}
      strap={tr(
        language,
        "共享服务在中间；每个人的 Duet 围在外面，连接自己的 Owner。",
        "Shared services at the center. Personal Duets around them, each connected to its own Owner.",
      )}
    >
      <ArchitectureDiagram
        language={language}
        stage={Math.min(step, 7)}
        elapsed={elapsed}
        focus={focus}
        flow={flow}
        style={{
          position: "absolute",
          left: 0,
          top: -15,
          width: 1120,
          height: 630,
        }}
      />
      <div
        style={{
          position: "absolute",
          left: 1160,
          top: 24,
          width: 560,
          minHeight: 512,
          border: `1px solid ${C.line}`,
          borderRadius: 22,
          padding: "32px 34px",
          boxSizing: "border-box",
          background: C.white,
        }}
      >
        <div
          style={{
            color: C.red,
            fontSize: 22,
            fontWeight: 650,
            letterSpacing: 1.5,
            marginBottom: 22,
          }}
        >
          {tr(
            language,
            step >= 10 ? "记住这五个职责" : "位置与职责",
            step >= 10 ? "FIVE RESPONSIBILITIES" : "POSITION & PURPOSE",
          )}
        </div>
        {step >= 10 ? (
          <>
            {summary.map(([key, label, zh, en]) => (
              <div
                key={key}
                style={{
                  display: "flex",
                  flexDirection: "column",
                  gap: 3,
                  margin: "0 -12px 5px",
                  padding: "6px 12px",
                  borderRadius: 10,
                  background: focus === key ? C.wash : "transparent",
                  borderLeft: `3px solid ${focus === key ? C.red : "transparent"}`,
                }}
              >
                <div style={{ fontSize: 26, fontWeight: 650 }}>{label}</div>
                <div style={{ fontSize: 22, lineHeight: 1.25, color: C.muted }}>
                  {tr(language, zh, en)}
                </div>
              </div>
            ))}
            {step === 15 && (
              <div
                style={{
                  marginTop: 16,
                  color: C.red,
                  fontSize: 25,
                  fontWeight: 600,
                }}
              >
                {tr(
                  language,
                  "从每个人的一次委托开始 →",
                  "Begin with one person’s delegation →",
                )}
              </div>
            )}
          </>
        ) : (
          <div
            style={{
              opacity: interpolate(elapsed, [0, 0.45 * fps], [0.65, 1], {
                extrapolateLeft: "clamp",
                extrapolateRight: "clamp",
              }),
            }}
          >
            <h2
              style={{
                margin: "0 0 22px",
                fontSize: 46,
                lineHeight: 1.1,
                letterSpacing: -1.3,
                fontWeight: 650,
              }}
            >
              {tr(language, card[0], card[1])}
            </h2>
            <div style={{ fontSize: 31, lineHeight: 1.4, marginBottom: 28 }}>
              {tr(language, card[2], card[3])}
            </div>
            <div
              style={{
                borderTop: `1px solid ${C.line}`,
                paddingTop: 24,
                color: C.muted,
                fontSize: 25,
                lineHeight: 1.5,
                whiteSpace: "pre-line",
              }}
            >
              {tr(language, card[4], card[5])}
            </div>
            {step === 6 && (
              <div
                style={{
                  marginTop: 20,
                  padding: "14px 18px",
                  background: C.paper,
                  borderRadius: 12,
                  fontSize: 23,
                  lineHeight: 1.5,
                }}
              >
                <div>
                  <b>Nora’s Duet</b> ·{" "}
                  {tr(language, "分享研究", "shares research")}
                </div>
                <div>
                  <b>Maya’s Duet</b> ·{" "}
                  {tr(language, "补充需求", "adds requirements")}
                </div>
              </div>
            )}
            {flow && (
              <div
                style={{
                  marginTop: 24,
                  fontSize: 23,
                  color: flow === "information" ? C.green : C.red,
                }}
              >
                {flow === "information"
                  ? tr(
                      language,
                      "信息访问 · 个人判断",
                      "Information access · personal judgment",
                    )
                  : tr(
                      language,
                      "事件与任务 · Policy 流转",
                      "Events and tasks · policy handoffs",
                    )}
              </div>
            )}
          </div>
        )}
      </div>
    </Frame>
  );
}
