import type { CSSProperties, ReactNode } from "react";
import { AbsoluteFill, Img, interpolate, staticFile, useCurrentFrame } from "remotion";
import { theme } from "./theme";

const layout = [
  {
    kicker: "01 / THE FIRST HANDOFF",
    title: "AI Native，从一件小事开始",
    subtitle: "把一条真人消息交给自己的 Duet，连同要做的事和要交付的结果。",
  },
  {
    kicker: "02 / PERSONAL AGENT",
    title: "始终是同一个 Duet",
    subtitle: "一个前台入口，持续理解你的工作。眼前的对话和长期事项，各有记忆。",
  },
  {
    kicker: "03 / WORKING TOGETHER",
    title: "真人在群聊，Duet 在主题协作",
    subtitle: "消息可以交给自己的 Duet。关联主题承接跨岗位的异步工作。",
  },
  {
    kicker: "04 / AGENT LOOP",
    title: "规则决定下一棒交给谁",
    subtitle: "Agent Loop 读取事件和结果，按 policy 调度。评审和修复由 Duet 承担。",
  },
  {
    kicker: "05 / LOOPLAB",
    title: "用实际回放验收结果",
    subtitle: "Agent 调查和编排用例。独立浏览器执行固定版本的流程，交付可检查的证据。",
  },
  {
    kicker: "06 / MESSAGE CENTER",
    title: "一条消息，如何变成行动",
    subtitle: "资讯先进入共享信息层，再由个人 Duet 判断它与你的工作有什么关系。",
  },
  {
    kicker: "07 / AN AI-NATIVE ORGANISATION",
    title: "把做顺的交接，变成规则",
    subtitle: "每个岗位从一小段委托开始。人定方向，确定性的外圈把工作接起来。",
  },
];

const charcoal = "#202124";
const night = "#19191b";
const nightPanel = "#242426";
const nightLine = "#3d3d40";
const nightMuted = "#b9b9b7";
const accent = "#f24a50";
const redWash = "#fff2f2";
const white = theme.white;
const line = theme.line;
const red = theme.red;
const muted = "#595955";
const green = "#246747";
const edge = { extrapolateLeft: "clamp", extrapolateRight: "clamp" } as const;

type BeatProps = { progress: number; at?: number; children: ReactNode };
type CardProps = { children: ReactNode; style?: CSSProperties; dark?: boolean; emphasized?: boolean };

const row: CSSProperties = { display: "flex", alignItems: "center" };
const grid: CSSProperties = { display: "grid", alignItems: "stretch" };
const copy: CSSProperties = { fontSize: 34, fontWeight: 500, lineHeight: 1.36, margin: 0 };
const subcopy: CSSProperties = { fontSize: 27, lineHeight: 1.43, color: muted, margin: 0 };
const fine: CSSProperties = { fontSize: 23, lineHeight: 1.4, color: muted, margin: 0 };
const eyebrow: CSSProperties = { fontFamily: "Inter, Noto Sans SC, sans-serif", fontSize: 22, fontWeight: 700, letterSpacing: 1.7 };
const cardTitle: CSSProperties = { fontSize: 43, fontWeight: 700, letterSpacing: -1.2, lineHeight: 1.15, margin: 0 };
const triple: CSSProperties = { ...grid, gridTemplateColumns: "1fr 76px 1fr 76px 1fr", height: 440 };
const smallTitle: CSSProperties = { fontSize: 33, fontWeight: 700, lineHeight: 1.2, margin: 0 };

function Beat({ progress, at = 0, children }: BeatProps) {
  return (
    <div
      style={{
        height: "100%",
        opacity: interpolate(progress, [at, at + 0.047], [0.34, 1], edge),
        translate: `0 ${interpolate(progress, [at, at + 0.047], [13, 0], edge)}px`,
      }}
    >
      {children}
    </div>
  );
}

function Card({ children, style, dark = false, emphasized = false }: CardProps) {
  return (
    <div
      style={{
        boxSizing: "border-box",
        height: "100%",
        minWidth: 0,
        padding: 30,
        background: dark ? nightPanel : emphasized ? redWash : white,
        border: `1px solid ${emphasized ? (dark ? accent : "#edb2b5") : dark ? nightLine : line}`,
        borderRadius: 14,
        boxShadow: dark ? undefined : "0 14px 36px rgba(38, 34, 31, 0.045)",
        ...style,
      }}
    >
      {children}
    </div>
  );
}

function Label({ children, dark = false, hot = false }: { children: ReactNode; dark?: boolean; hot?: boolean }) {
  return <div style={{ ...eyebrow, color: hot ? (dark ? accent : red) : dark ? nightMuted : muted }}>{children}</div>;
}

function Arrow({ dark = false, hot = false, reverse = false }: { dark?: boolean; hot?: boolean; reverse?: boolean }) {
  return (
    <svg viewBox="0 0 62 30" width={62} height={30} aria-hidden="true" style={{ flexShrink: 0, scale: reverse ? "-1 1" : undefined }}>
      <path d="M2 15H55M46 7L55 15L46 23" stroke={hot ? (dark ? accent : red) : dark ? "#858589" : "#a19e99"} strokeWidth={2.5} fill="none" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function Between({ progress, at, hot }: { progress: number; at: number; hot?: boolean }) {
  return (
    <Beat progress={progress} at={at}>
      <div style={{ ...row, justifyContent: "center", height: "100%" }}>
        <Arrow hot={hot} />
      </div>
    </Beat>
  );
}

function Mark({ size = 46, dark = false }: { size?: number; dark?: boolean }) {
  if (dark) {
    return (
      <svg viewBox="0 0 48 48" width={size} height={size} aria-hidden="true" style={{ flexShrink: 0 }}>
        <path fill={white} d="M22 4H8L2 10V34L8 40H22V32H10V12H22Z" />
        <path fill="#D71920" d="M26 8H40L46 14V38L40 44H26V36H38V16H26Z" />
      </svg>
    );
  }
  return <Img src={staticFile("brand/duet-symbol.svg")} style={{ width: size, height: size, objectFit: "contain", flexShrink: 0 }} />;
}

function Avatar({ name, redTone = false }: { name: string; redTone?: boolean }) {
  return (
    <div style={{ ...row, justifyContent: "center", width: 51, height: 51, flexShrink: 0, borderRadius: "50%", color: redTone ? red : charcoal, background: redTone ? "#ffe0e1" : "#edece8", fontFamily: "Inter, sans-serif", fontSize: 26, fontWeight: 700 }}>
      {name}
    </div>
  );
}

function FileIcon({ size = 36, dark = false }: { size?: number; dark?: boolean }) {
  return (
    <svg viewBox="0 0 36 36" width={size} height={size} aria-hidden="true" style={{ flexShrink: 0 }}>
      <path d="M9 4h12l7 7v21H9zM21 4v8h7M13 18h11M13 23h11M13 28h8" fill="none" stroke={dark ? accent : red} strokeWidth={2.4} strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function Quote({ children }: { children: ReactNode }) {
  return <div style={{ padding: "17px 19px", borderLeft: `4px solid ${red}`, background: "#f5f3f0", borderRadius: "0 6px 6px 0", ...fine }}>{children}</div>;
}

function Bob({ progress }: { progress: number }) {
  return (
    <div style={triple}>
      <Beat progress={progress}>
        <Card>
          <Label>01 · 人的请求</Label>
          <div style={{ ...row, marginTop: 34, gap: 15 }}>
            <Avatar name="B" />
            <div>
              <div style={smallTitle}>Bob</div>
              <div style={{ ...fine, fontSize: 20 }}>Direct message</div>
            </div>
          </div>
          <p style={{ ...copy, marginTop: 32, padding: "22px 23px", background: "#f1efec", borderRadius: 10 }}>能帮我做一份周报表，再发给我吗？</p>
        </Card>
      </Beat>
      <Between progress={progress} at={0.14} hot />
      <Beat progress={progress} at={0.14}>
        <Card emphasized>
          <Label hot>02 · 交给自己的 Duet</Label>
          <div style={{ ...row, gap: 14, margin: "26px 0 24px" }}>
            <Mark size={43} />
            <div style={smallTitle}>My Duet</div>
          </div>
          <Quote>Bob · 能帮我做一份周报表，再发给我吗？</Quote>
          <p style={{ ...copy, marginTop: 22 }}>做好报表，然后发给鲍勃。</p>
        </Card>
      </Beat>
      <Between progress={progress} at={0.35} hot />
      <Beat progress={progress} at={0.35}>
        <Card>
          <Label>03 · 回到原私聊</Label>
          <div style={{ ...row, margin: "30px 0 25px", gap: 15 }}>
            <Avatar name="B" />
            <div>
              <div style={smallTitle}>Bob</div>
              <div style={{ ...fine, fontSize: 20 }}>Direct message</div>
            </div>
          </div>
          <div style={{ ...row, gap: 14, alignItems: "flex-start" }}>
            <Avatar name="Y" />
            <div style={{ flex: 1 }}>
              <div style={{ ...fine, color: charcoal, fontWeight: 700, marginBottom: 8 }}>You</div>
              <div style={{ ...row, gap: 12, border: `1px solid ${line}`, borderRadius: 8, padding: "13px 15px" }}>
                <FileIcon />
                <div style={{ ...copy, fontSize: 29 }}>周报表.xlsx</div>
              </div>
            </div>
          </div>
          <p style={{ ...subcopy, marginTop: 22 }}>按这次任务的明确指令发送</p>
        </Card>
      </Beat>
    </div>
  );
}

function Memory({ progress }: { progress: number }) {
  return (
    <div style={{ ...grid, gridTemplateColumns: "0.76fr 1.72fr", gap: 31, height: 497 }}>
      <Beat progress={progress}>
        <Card emphasized style={{ padding: 38 }}>
          <Label hot>唯一的前台入口</Label>
          <div style={{ ...row, gap: 19, margin: "54px 0 25px" }}>
            <Mark size={74} />
            <h2 style={{ ...cardTitle, fontSize: 52 }}>My Duet</h2>
          </div>
          <p style={{ ...copy, fontSize: 38 }}>那条监管政策，<br />推进到哪了？</p>
          <div style={{ height: 2, width: 54, margin: "30px 0 20px", background: red }} />
          <p style={subcopy}>按你的职责和习惯工作</p>
        </Card>
      </Beat>
      <div style={{ ...grid, gridTemplateRows: "repeat(3, 1fr)", gap: 16 }}>
        <Beat progress={progress} at={0.18}>
          <Card style={{ ...row, padding: "22px 30px", justifyContent: "space-between" }}>
            <div>
              <Label hot>01 / NOW</Label>
              <h2 style={{ ...smallTitle, marginTop: 8 }}>短期记忆</h2>
            </div>
            <div style={{ ...row, gap: 16, color: muted, fontSize: 28, fontWeight: 600 }}>
              <span>滚动摘要</span><span style={{ color: red }}>＋</span><span>最近几条问答原文</span>
            </div>
          </Card>
        </Beat>
        <Beat progress={progress} at={0.38}>
          <Card style={{ ...row, padding: "22px 30px", justifyContent: "space-between" }}>
            <div>
              <Label hot>02 / DREAM</Label>
              <h2 style={{ ...smallTitle, marginTop: 8 }}>Git 记忆</h2>
            </div>
            <div style={{ textAlign: "right" }}>
              <div style={{ ...copy, fontSize: 29 }}>可见群聊与私聊 → 增量提炼 → Git</div>
              <p style={{ ...fine, marginTop: 5 }}>每 4 小时 · 保留来源与版本</p>
            </div>
          </Card>
        </Beat>
        <Beat progress={progress} at={0.76}>
          <Card style={{ ...row, padding: "22px 30px", justifyContent: "space-between" }}>
            <div>
              <Label hot>03 / FOLLOW THROUGH</Label>
              <h2 style={{ ...smallTitle, marginTop: 8 }}>Tracking</h2>
            </div>
            <div style={{ ...row, gap: 14, fontSize: 25, fontWeight: 650 }}>
              {["首节点 · 索引", "后续进展", "交付与验收"].map((label, index) => (
                <div key={label} style={{ ...row, gap: 14 }}>
                  {index > 0 && <span style={{ color: red }}>→</span>}
                  <span style={{ padding: "9px 13px", background: index === 0 ? redWash : "#f3f2ef", borderRadius: 6 }}>{label}</span>
                </div>
              ))}
            </div>
          </Card>
        </Beat>
      </div>
    </div>
  );
}

function Topic({ progress }: { progress: number }) {
  return (
    <div style={{ ...grid, gridTemplateColumns: "0.91fr 84px 1.27fr", height: 500 }}>
      <Beat progress={progress}>
        <Card>
          <Label>01 · 真人群聊</Label>
          <h2 style={{ ...cardTitle, margin: "20px 0 25px" }}>Group chat</h2>
          <div style={{ ...row, gap: 17, alignItems: "flex-start" }}>
            <Avatar name="M" />
            <div>
              <div style={{ ...fine, color: charcoal, fontWeight: 700 }}>Maya</div>
              <p style={{ ...copy, fontSize: 28, marginTop: 7 }}>请根据新政策梳理产品要改的地方。</p>
            </div>
          </div>
          <Beat progress={progress} at={0.26}>
            <div style={{ ...row, gap: 13, marginTop: 34, padding: "18px 20px", border: "1px solid #edb2b5", borderRadius: 8, background: redWash }}>
              <Mark size={35} />
              <span style={{ ...smallTitle, fontSize: 27 }}>Ask my Duet</span>
            </div>
            <p style={{ ...fine, marginTop: 13 }}>引用真人消息，交给自己的 Duet</p>
          </Beat>
        </Card>
      </Beat>
      <Between progress={progress} at={0.47} hot />
      <Beat progress={progress} at={0.47}>
        <Card emphasized>
          <Label hot>02 · 绑定的 Agent 主题</Label>
          <div style={{ ...row, justifyContent: "space-between", gap: 15, margin: "19px 0 22px" }}>
            <h2 style={cardTitle}>Linked topic</h2>
            <div style={{ ...fine, color: muted }}>Topic owner · My Duet</div>
          </div>
          <Beat progress={progress} at={0.70}>
            <div style={{ border: `1px solid ${line}`, padding: "20px 22px", borderRadius: 8, background: white }}>
              <div style={{ ...row, gap: 12 }}>
                <Mark size={35} />
                <div style={{ ...fine, color: charcoal, fontWeight: 700 }}>My Duet</div>
              </div>
              <p style={{ ...copy, fontSize: 28, marginTop: 12 }}><span style={{ color: red, fontWeight: 700 }}>@Avery’s Duet</span> 请核对新政策涉及的功能改动。</p>
            </div>
            <div style={{ ...row, flexWrap: "wrap", gap: 13, marginTop: 22, fontSize: 24, fontWeight: 650 }}>
              {["共享历史", "共享文件", "各自的 Tracking"].map((item) => <span key={item} style={{ padding: "7px 12px", borderRadius: 6, background: white, border: `1px solid ${line}` }}>{item}</span>)}
            </div>
            <p style={{ ...fine, marginTop: 15 }}>发起 Duet 管理主题，Duet 在主题内异步接手。</p>
          </Beat>
        </Card>
      </Beat>
    </div>
  );
}

const pipeline = [
  { label: "01 / GITHUB", title: "PR 创建", details: "GitHub 事件进入外圈", at: 0 },
  { label: "02 / POLICY", title: "Agent Loop", details: "按仓库规则找人 · 无需模型", at: 0.20 },
  { label: "03 / REVIEW", title: "评审者的 Duet", details: "理解改动，给出评审结论", at: 0.27 },
  { label: "04 / RESULT", title: "请求修改", details: "按规则把评审意见写回 PR", at: 0.48 },
  { label: "05 / FIX", title: "作者的 Duet", details: "完成修复，追加提交", at: 0.72 },
];

function Policy({ progress }: { progress: number }) {
  return (
    <>
      <div style={{ ...grid, gridTemplateColumns: "repeat(5, minmax(0, 1fr))", gap: 33, height: 266 }}>
        {pipeline.map(({ label, title, details, at }, index) => (
          <div key={label} style={{ position: "relative" }}>
            <Beat progress={progress} at={at}>
              <Card dark emphasized={index === 1} style={{ padding: "27px 24px" }}>
                <Label dark hot={index === 1}>{label}</Label>
                <h2 style={{ ...cardTitle, fontSize: 35, marginTop: 30 }}>{title}</h2>
                <p style={{ ...subcopy, color: nightMuted, fontSize: 26, marginTop: 15 }}>{details}</p>
              </Card>
            </Beat>
            {index < pipeline.length - 1 && (
              <div style={{ position: "absolute", width: 32, right: -33, top: 116, overflow: "hidden" }}>
                <svg width={32} height={24} aria-hidden="true">
                  <path d="M4 12h22m-7-6 7 6-7 6" stroke={progress >= pipeline[index + 1].at ? accent : "#77777b"} fill="none" strokeWidth={2.5} strokeLinecap="round" strokeLinejoin="round" />
                </svg>
              </div>
            )}
          </div>
        ))}
      </div>
      <Beat progress={progress} at={0.76}>
        <div style={{ ...row, marginTop: 31, justifyContent: "space-between", padding: "19px 24px", borderTop: `1px solid ${nightLine}`, borderBottom: `1px solid ${nightLine}` }}>
          <div style={{ ...row, gap: 12 }}>
            <Arrow dark hot reverse />
            <span style={{ ...copy, fontSize: 30 }}>新提交重新触发评审或验收</span>
          </div>
          <div style={{ ...fine, color: nightMuted }}>PR 回写由仓库集成执行</div>
        </div>
      </Beat>
    </>
  );
}

function Test({ progress }: { progress: number }) {
  return (
    <>
      <div style={{ ...grid, gridTemplateColumns: "0.85fr 76px 1.35fr", height: 338 }}>
        <Beat progress={progress}>
          <Card>
            <Label hot>01 / COMPOSE</Label>
            <h2 style={{ ...cardTitle, margin: "18px 0" }}>调查与编排</h2>
            <p style={{ ...subcopy, marginBottom: 22 }}>LoopLab Agent 调查页面，组合可复用测试节点。</p>
            <div style={{ ...row, gap: 12, fontSize: 26, fontWeight: 650 }}>
              <FileIcon size={32} />
              <span>需求 → 流程版本 → 断言</span>
            </div>
          </Card>
        </Beat>
      <Between progress={progress} at={0.22} hot />
      <Beat progress={progress} at={0.22}>
          <Card emphasized style={{ padding: 0, overflow: "hidden" }}>
            <div style={{ ...row, justifyContent: "space-between", padding: "19px 26px", borderBottom: `1px solid ${line}`, background: "#f5f3f0" }}>
              <Label hot>02 / REPLAY</Label>
              <span style={{ ...fine, fontSize: 20 }}>固定版本 · 独立浏览器 · 无模型回放</span>
            </div>
            <div style={{ padding: "23px 26px" }}>
              <h2 style={{ ...cardTitle, fontSize: 39, marginBottom: 16 }}>运行并记录证据</h2>
              <div style={{ ...grid, gridTemplateColumns: "1fr 1fr 1fr", gap: 12 }}>
                {["页面断言", "步骤记录", "截图"].map((item, i) => (
                  <div key={item} style={{ background: white, border: `1px solid ${line}`, borderRadius: 7, padding: "15px 13px" }}>
                    <div style={{ ...eyebrow, fontSize: 17, color: red }}>0{i + 1}</div>
                    <div style={{ ...copy, fontSize: 27, marginTop: 4 }}>{item}</div>
                  </div>
                ))}
              </div>
            </div>
          </Card>
        </Beat>
      </div>
      <Beat progress={progress} at={0.45}>
        <div style={{ ...row, marginTop: 16, minHeight: 40, gap: 18, justifyContent: "center", fontSize: 24, fontWeight: 650 }}>
          <span>可读报告 → Message Center</span>
          <span style={{ color: red }}>·</span>
          <span>结构化事件 → Agent Loop</span>
        </div>
      </Beat>
      <Beat progress={progress} at={0.70}>
        <div style={{ ...grid, gridTemplateColumns: "1fr 1fr", gap: 16, marginTop: 13 }}>
          <div style={{ ...row, border: "1px solid #edb2b5", padding: "15px 22px", borderRadius: 8, gap: 13, background: redWash }}>
            <span style={{ ...smallTitle, color: red }}>失败</span><Arrow hot /><span style={{ ...copy, fontSize: 28 }}>带着证据回到负责人的 Duet</span>
          </div>
          <div style={{ ...row, border: `1px solid ${line}`, padding: "15px 22px", borderRadius: 8, gap: 13, background: white }}>
            <span style={{ ...smallTitle, color: green }}>通过</span><Arrow /><span style={{ ...copy, fontSize: 28 }}>由 Agent Loop 按规则推进</span>
          </div>
        </div>
      </Beat>
    </>
  );
}

function MessageCenter({ progress }: { progress: number }) {
  return (
    <div style={triple}>
      <Beat progress={progress}>
        <Card>
          <Label>01 · 采集与整理</Label>
          <h2 style={{ ...cardTitle, margin: "24px 0 20px", fontSize: 38 }}>Message Center</h2>
          <div style={{ border: `1px solid ${line}`, padding: 20, background: "#f3f2ef", borderRadius: 8 }}>
            <div style={{ ...row, gap: 11, marginBottom: 11 }}>
              <FileIcon size={31} />
              <div style={{ ...smallTitle, fontSize: 30 }}>监管政策变化</div>
            </div>
            <p style={fine}>来源 · 原文 · 摘要 · 分类</p>
          </div>
          <p style={{ ...subcopy, marginTop: 21 }}>爬虫采集，形成可读、可订阅的资讯。</p>
        </Card>
      </Beat>
      <Between progress={progress} at={0.29} hot />
      <Beat progress={progress} at={0.29}>
        <Card emphasized>
          <Label hot>02 · 个人推荐</Label>
          <div style={{ ...row, gap: 12, margin: "23px 0 20px" }}>
            <Mark size={44} />
            <h2 style={{ ...cardTitle, fontSize: 38 }}>My Duet</h2>
          </div>
          <Quote>这条变化可能影响你正在推进的开户功能。</Quote>
          <p style={{ ...subcopy, marginTop: 20 }}>结合职责、项目和 Tracking 判断相关性。</p>
        </Card>
      </Beat>
      <Between progress={progress} at={0.51} hot />
      <Beat progress={progress} at={0.51}>
        <Card>
          <Label>03 · 负责人决定</Label>
          <h2 style={{ ...cardTitle, margin: "24px 0 20px", fontSize: 38 }}>是否启动调研？</h2>
          <p style={{ ...copy, fontSize: 31 }}>收集市场信息，整理报告，必要时写成 Story。</p>
          <Beat progress={progress} at={0.74}>
            <div style={{ ...row, gap: 10, marginTop: 24, flexWrap: "wrap" }}>
              {["报告 / Story", "群聊与主题", "按需验收"].map((item) => <span key={item} style={{ ...fine, fontSize: 19, padding: "6px 8px", border: `1px solid ${line}`, borderRadius: 5 }}>{item}</span>)}
            </div>
          </Beat>
        </Card>
      </Beat>
    </div>
  );
}

function Orbit({ progress }: { progress: number }) {
  const nodes = [
    { name: "Duet", detail: "理解和执行", left: 91, at: 0.25 },
    { name: "Message Center", detail: "汇聚与共享", left: 359, at: 0.32 },
    { name: "LoopLab", detail: "验收与证据", left: 627, at: 0.40 },
  ];
  return (
    <div style={{ ...grid, gridTemplateColumns: "1.68fr 1fr", gap: 42, height: 510 }}>
      <div style={{ position: "relative", height: 470 }}>
        <svg width="100%" height={470} viewBox="0 0 950 470" style={{ position: "absolute", inset: 0 }} aria-hidden="true">
          <rect x="19" y="65" width="910" height="361" rx="180" stroke="#49494d" strokeWidth="2" fill="none" />
          <rect x="19" y="65" width="910" height="361" rx="180" stroke={accent} strokeWidth="3" fill="none" pathLength="1" strokeDasharray="1" strokeDashoffset={interpolate(progress, [0.03, 0.66], [1, 0], edge)} />
          <path d="M115 390l20 1-6-19M833 99l-19-1 6 19" stroke={accent} strokeWidth="3" fill="none" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
        <div style={{ position: "absolute", top: 38, left: "28%", right: "28%", display: "flex", justifyContent: "center" }}>
          <div style={{ padding: "10px 22px", background: night, whiteSpace: "nowrap", ...smallTitle, fontSize: 31, color: accent }}>Agent Loop · Policy</div>
        </div>
        {nodes.map(({ name, detail, left, at }) => (
          <div key={name} style={{ position: "absolute", top: 173, left: `${left / 9.5}%`, width: "24.8%", height: 147 }}>
            <Beat progress={progress} at={at}>
              <Card dark style={{ padding: "28px 8px", textAlign: "center" }}>
                <div style={{ ...smallTitle, fontSize: name === "Message Center" ? 27 : 34, marginBottom: 11 }}>{name}</div>
                <div style={{ ...fine, color: nightMuted }}>{detail}</div>
              </Card>
            </Beat>
          </div>
        ))}
        <div style={{ ...fine, position: "absolute", top: 395, width: "100%", textAlign: "center", color: nightMuted }}>
          <span style={{ padding: "7px 17px", background: night }}>事件 → 工作 → 结果 → 下一步</span>
        </div>
      </div>
      <Beat progress={progress} at={0.53}>
        <Card dark emphasized style={{ marginTop: 50, height: 376, padding: "32px 35px" }}>
          <Label dark hot>HUMAN JUDGMENT</Label>
          <h2 style={{ ...cardTitle, marginTop: 29, marginBottom: 23 }}>人的判断</h2>
          <p style={{ ...copy, lineHeight: 1.5 }}>定目标与优先级<br />做取舍与承诺</p>
          <div style={{ width: 46, height: 2, background: accent, margin: "26px 0 19px" }} />
          <p style={{ ...subcopy, color: nightMuted }}>团队逐条接通可验收的业务闭环。</p>
        </Card>
      </Beat>
    </div>
  );
}

const content = [Bob, Memory, Topic, Policy, Test, MessageCenter, Orbit];

export function AiNativeScene({ scene, durationInFrames }: { scene: number; durationInFrames: number }) {
  const frame = useCurrentFrame();
  const progress = frame / Math.max(1, durationInFrames - 1);
  const dark = scene === 3 || scene === 6;
  const chapter = layout[scene];
  const Content = content[scene];
  return (
    <AbsoluteFill style={{ overflow: "hidden", background: dark ? night : theme.paper, color: dark ? white : charcoal, fontFamily: '"Noto Sans SC", Inter, sans-serif' }}>
      <div style={{ position: "absolute", top: 55, left: 123, right: 123 }}>
        <div style={{ ...row, justifyContent: "space-between", height: 46 }}>
          <div style={{ ...row, gap: 15 }}>
            <span style={{ width: 30, height: 3, background: dark ? accent : red }} />
            <Label dark={dark}>{chapter.kicker}</Label>
          </div>
          <div style={{ ...row, gap: 11, fontFamily: "Inter, sans-serif", fontSize: 24, fontWeight: 650 }}>
            <Mark size={35} dark={dark} /> Duet
          </div>
        </div>
        <h1 style={{ fontSize: 75, lineHeight: 1.09, letterSpacing: -2.7, margin: "23px 0 14px", fontWeight: 750 }}>{chapter.title}</h1>
        <p style={{ margin: 0, color: dark ? nightMuted : muted, fontSize: 33, fontWeight: 500, lineHeight: 1.35 }}>{chapter.subtitle}</p>
      </div>
      <div style={{ position: "absolute", top: 306, left: 123, right: 123 }}>
        <Content progress={progress} />
      </div>
      <div style={{ position: "absolute", top: 861, left: 123, right: 123, height: 2, background: dark ? nightLine : line }}>
        <div style={{ height: "100%", width: `${((scene + progress) / content.length) * 100}%`, background: dark ? accent : red }} />
      </div>
    </AbsoluteFill>
  );
}
