import type { CSSProperties, ReactNode } from "react";
import { useVideoConfig } from "remotion";
import {
  Arrow,
  Card,
  File,
  Frame,
  Person,
  Pill,
  Reveal,
  Text,
  useSteps,
} from "./Shared";
import type { SceneProps } from "./Shared";

const ink = "#202124";
const red = "#D71920";
const softRed = "#FFF0F0";
const line = "#D7D5CF";
const ash = "#F0EFEB";
const row: CSSProperties = { display: "flex", alignItems: "center", gap: 16 };
const stack: CSSProperties = {
  display: "grid",
  alignContent: "start",
  gap: 16,
};
const panel: CSSProperties = {
  ...stack,
  padding: 22,
  gap: 13,
  height: "100%",
  background: "#fff",
};
const split: CSSProperties = {
  display: "grid",
  gridTemplateColumns: "1fr 1fr",
  gap: 24,
  height: "100%",
};
const head: CSSProperties = {
  ...row,
  justifyContent: "space-between",
  minHeight: 50,
  paddingBottom: 10,
  borderBottom: `1px solid ${line}`,
};

function Label({ children }: { children: ReactNode }) {
  return (
    <div
      style={{
        fontSize: 22,
        fontWeight: 650,
        letterSpacing: 0.9,
        color: "#626663",
      }}
    >
      {children}
    </div>
  );
}

function Window({
  title,
  tag,
  children,
  accent,
}: {
  title: string;
  tag?: ReactNode;
  children: ReactNode;
  accent?: boolean;
}) {
  return (
    <Card accent={accent} style={panel}>
      <div style={head}>
        <Text size={32} style={{ fontWeight: 750 }}>
          {title}
        </Text>
        {tag}
      </div>
      {children}
    </Card>
  );
}

function Bubble({
  author,
  children,
  accent,
}: {
  author: string;
  children: ReactNode;
  accent?: boolean;
}) {
  return (
    <div
      style={{
        ...stack,
        gap: 6,
        padding: "14px 19px",
        borderLeft: `4px solid ${accent ? red : line}`,
        background: accent ? softRed : ash,
      }}
    >
      <Label>{author}</Label>
      <Text size={31}>{children}</Text>
    </div>
  );
}

function Report({
  title,
  columns,
  values,
}: {
  title: string;
  columns: string[];
  values: string[][];
}) {
  return (
    <div
      style={{
        ...stack,
        padding: 25,
        gap: 13,
        border: `1px solid ${line}`,
        background: "#fff",
      }}
    >
      <Text size={43} style={{ fontWeight: 750, marginBottom: 8 }}>
        {title}
      </Text>
      {[columns, ...values].map((entry, index) => (
        <div
          key={entry[0]}
          style={{
            display: "grid",
            gridTemplateColumns: "1fr 0.66fr",
            gap: 10,
            padding: "10px 14px",
            background: index === 0 ? ink : ash,
            color: index === 0 ? "#fff" : ink,
          }}
        >
          {entry.map((value) => (
            <span
              key={value}
              style={{ fontSize: 27, fontWeight: index === 0 ? 700 : 500 }}
            >
              {value}
            </span>
          ))}
        </div>
      ))}
    </div>
  );
}

export function S01Delegation({ language, cues }: SceneProps) {
  const zh = language === "zh-CN";
  const { fps } = useVideoConfig();
  const { step, at } = useSteps(cues, language, [
    ["每个人都可以", "Each person can begin"],
    ["个人 AI 叫作 Duet", "personal AI is called Duet"],
    ["交出的结果", "one Duet’s output"],
    ["明确的起点", "With a clear starting point"],
    ["这是我们理解", "This is how we approach"],
  ]);
  const roles = zh
    ? ["市场研究", "产品", "开发", "测试"]
    : ["Market research", "Product", "Development", "Testing"];
  const people = ["Nora", "Maya", "Alex", "Sam"];
  const items = zh
    ? ["资料", "需求", "代码", "验收结果"]
    : ["Research", "Requirements", "Code", "Test evidence"];
  const tasks = zh
    ? ["收集资料", "整理报表", "跟进进展"]
    : ["Collect information", "Prepare a report", "Follow up"];
  return (
    <Frame
      number={1}
      title={
        zh
          ? "每个人先交出一部分工作"
          : "Each person delegates part of their work"
      }
      strap={
        zh
          ? "从一件小任务，到跨岗位交接"
          : "From a small task to a handoff across roles"
      }
    >
      {step < 2 ? (
        <div
          style={{
            ...split,
            gridTemplateColumns: "1fr 0.7fr",
            gap: 40,
            alignItems: "center",
          }}
        >
          <Card style={{ ...stack, padding: 44, gap: 28, height: "auto" }}>
            <Person name="Nora" role={roles[0]} active />
            <div style={{ ...row, gap: 28, padding: "16px 0" }}>
              <Arrow color={red} />
              <Person name="Duet" agent />
            </div>
            <Text size={48} style={{ fontWeight: 750 }}>
              {zh ? "先委托一件事" : "Start with one task"}
            </Text>
          </Card>
          <div style={{ ...stack, gap: 17 }}>
            {tasks.map((task, index) => (
              <Reveal key={task} at={at(1) + Math.round(index * 0.42 * fps)}>
                <File title={task} active={index === 0} />
              </Reveal>
            ))}
          </div>
        </div>
      ) : (
        <div style={{ ...stack, gap: 38, paddingTop: 32 }}>
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(4, minmax(0, 1fr))",
              gap: 20,
            }}
          >
            {people.map((person, index) => (
              <Reveal key={person} at={at(2) + Math.round(index * 0.27 * fps)}>
                <Card style={{ ...stack, gap: 24, padding: 21, height: 232 }}>
                  <Person
                    name={person}
                    role={roles[index]}
                    active={index === 0}
                  />
                  <Person name={`${person}’s Duet`} agent />
                </Card>
              </Reveal>
            ))}
          </div>
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "1fr 74px 1fr 74px 1fr 74px 1fr",
              alignItems: "center",
              gap: 8,
            }}
          >
            {items.flatMap((item, index) => {
              const itemAt = at(3) + Math.round(index * 0.35 * fps);
              const file = (
                <Reveal key={item} at={itemAt}>
                  <File title={item} active={index === 3 && step >= 4} />
                </Reveal>
              );
              return index === 0
                ? [file]
                : [
                    <Reveal key={`arrow-${item}`} at={itemAt}>
                      <Arrow color={red} />
                    </Reveal>,
                    file,
                  ];
            })}
          </div>
          <Reveal at={at(4)} style={{ marginTop: 9, justifySelf: "start" }}>
            <Card
              accent={step >= 5}
              style={{ ...row, gap: 20, padding: "19px 24px", height: "auto" }}
            >
              <Label>{zh ? "业务闭环" : "WORKFLOW"}</Label>
              <Text size={33}>
                {zh
                  ? "明确起点 · 交付可接手 · 结果可验收"
                  : "Clear start · Usable handoffs · Verifiable outcome"}
              </Text>
            </Card>
          </Reveal>
        </div>
      )}
    </Frame>
  );
}

export function S02Bob({ language, cues }: SceneProps) {
  const zh = language === "zh-CN";
  const { fps } = useVideoConfig();
  const { step, at } = useSteps(cues, language, [
    ["我选中", "I select his message"],
    ["帮我做好", "and say, prepare this report"],
    ["整理数据", "My Duet organizes the data"],
    ["发送工具", "and uses a sending tool"],
    ["鲍勃收到", "Bob receives what he needs"],
  ]);
  const bob = zh ? "鲍勃" : "Bob";
  const request = zh
    ? "请帮我做一份本周报表，做好发给我。"
    : "Please prepare this week’s report and send it to me.";
  const report = zh ? "本周业务报表" : "Weekly operations report";
  return (
    <Frame
      number={2}
      title={zh ? "鲍勃要一份报表" : "Bob needs a report"}
      strap={
        zh
          ? "一条真人消息，一次明确委托"
          : "One human message, one explicit assignment"
      }
    >
      <div style={{ ...split, gridTemplateColumns: "0.85fr 1fr" }}>
        <Window
          title={bob}
          tag={<Pill>Direct message</Pill>}
          accent={step < 2 || step >= 4}
        >
          <Person
            name={bob}
            role={zh ? "同事" : "Colleague"}
            active={step < 2}
          />
          <Bubble author={bob} accent={step === 1}>
            {request}
          </Bubble>
          {step < 4 && (
            <Reveal at={at(1)} style={{ justifySelf: "start" }}>
              <Pill active>Ask my Duet</Pill>
            </Reveal>
          )}
          {step >= 4 && (
            <Reveal at={at(4)}>
              <div style={{ ...stack, gap: 9 }}>
                <Person
                  name={zh ? "我" : "You"}
                  role={zh ? "消息发送者" : "Message sender"}
                  active
                />
                <File title={report} detail="weekly-report.xlsx" active />
              </div>
            </Reveal>
          )}
          {step >= 5 && (
            <Reveal at={at(5)} style={{ justifySelf: "end" }}>
              <Pill active>
                {zh ? "鲍勃打开附件" : "Bob opens the attachment"}
              </Pill>
            </Reveal>
          )}
        </Window>
        {step < 4 ? (
          <Reveal at={at(1)} style={{ height: "100%" }}>
            <Window
              title="My Duet"
              tag={<Pill>Private</Pill>}
              accent={step >= 2}
            >
              <Bubble
                author={
                  zh
                    ? `引用 · ${bob} 的私聊`
                    : `Quote · ${bob}’s direct message`
                }
              >
                {request}
              </Bubble>
              <Reveal at={at(2)}>
                <Bubble
                  author={zh ? "我 · 本次委托" : "You · This task"}
                  accent
                >
                  {zh
                    ? "帮我做好这份报表，然后发给鲍勃。"
                    : "Prepare this report, then send it to Bob."}
                </Bubble>
              </Reveal>
              <Reveal at={at(3)}>
                <div style={{ ...row, flexWrap: "wrap", gap: 12 }}>
                  <Pill>{zh ? "整理数据" : "Organize data"}</Pill>
                  <Reveal at={at(3) + Math.round(0.65 * fps)}>
                    <Pill>{zh ? "生成报表" : "Build report"}</Pill>
                  </Reveal>
                </div>
              </Reveal>
              <Reveal at={at(3) + Math.round(1.2 * fps)}>
                <File title={report} detail="weekly-report.xlsx" active />
              </Reveal>
            </Window>
          </Reveal>
        ) : (
          <div style={{ ...stack, gap: 19 }}>
            <Bubble
              author={
                zh ? "本次明确委托" : "Explicit instruction for this task"
              }
              accent
            >
              {zh
                ? "帮我做好这份报表，然后发给鲍勃。"
                : "Prepare this report, then send it to Bob."}
            </Bubble>
            {step === 4 ? (
              <Reveal at={at(4)}>
                <File
                  title={report}
                  detail={
                    zh ? "已发回原对话" : "Sent to the original conversation"
                  }
                  active
                />
              </Reveal>
            ) : (
              <Reveal at={at(5)}>
                <Report
                  title={report}
                  columns={zh ? ["本周指标", "数量"] : ["This week", "Count"]}
                  values={
                    zh
                      ? [
                          ["新增申请", "42"],
                          ["已处理", "37"],
                          ["处理中", "5"],
                        ]
                      : [
                          ["New applications", "42"],
                          ["Processed", "37"],
                          ["In progress", "5"],
                        ]
                  }
                />
              </Reveal>
            )}
          </div>
        )}
      </div>
    </Frame>
  );
}

export function S03Personal({ language, cues }: SceneProps) {
  const zh = language === "zh-CN";
  const { fps } = useVideoConfig();
  const { step, at } = useSteps(cues, language, [
    ["你可以教它", "You can teach it your"],
    ["一天里", "During a day"],
    ["在后台分别处理", "It works on the tasks separately"],
    ["压缩成摘要", "As the conversation grows"],
    ["重新找回", "You can continue the immediate discussion"],
  ]);
  const tasks = zh
    ? ["整理本周报表", "查一下客户的问题", "研究一下行业变化"]
    : [
        "Prepare this week’s report",
        "Look into a customer question",
        "Research an industry change",
      ];
  const results = zh
    ? ["报表已交付", "客户问题已有答复", "调研已附原文来源"]
    : [
        "Report delivered",
        "Customer question answered",
        "Research includes its sources",
      ];
  return (
    <Frame
      number={3}
      title={zh ? "始终面对自己的 Duet" : "The same Duet stays with you"}
      strap={
        zh
          ? "一个入口，分别处理多件事情"
          : "One entry point, several separate tasks"
      }
    >
      <div style={{ ...split, gridTemplateColumns: "0.88fr 1fr" }}>
        <Window title="My Duet" tag={<Pill>Private</Pill>} accent>
          <Person
            name="Duet"
            role={zh ? "你的个人 AI" : "Your personal AI"}
            agent
            active
          />
          {step >= 4 ? (
            <Reveal at={at(4)}>
              <div style={{ ...stack, gap: 14 }}>
                <Bubble
                  author={
                    zh ? "较早对话 · 摘要" : "Earlier conversation · Summary"
                  }
                >
                  {zh
                    ? "本周报表已发给鲍勃，客户问题已有答复。"
                    : "Bob has the weekly report, and the customer question has been answered."}
                </Bubble>
                <Bubble author={zh ? "我 · 最近消息" : "You · Recent message"}>
                  {tasks[2]}
                </Bubble>
                <Bubble author="Duet" accent>
                  {results[2]}
                </Bubble>
              </div>
            </Reveal>
          ) : step >= 2 ? (
            <div style={{ ...stack, gap: 12 }}>
              {tasks.map((task, index) => (
                <Reveal key={task} at={at(2) + Math.round(index * 0.56 * fps)}>
                  <Bubble author={zh ? "我" : "You"} accent={index === 2}>
                    {task}
                  </Bubble>
                </Reveal>
              ))}
            </div>
          ) : (
            <Text size={41} style={{ fontWeight: 700, marginTop: 14 }}>
              {zh ? "持续为同一位员工工作" : "A continuing relationship"}
            </Text>
          )}
        </Window>
        {step < 2 ? (
          <Reveal at={at(1)} style={{ height: "100%" }}>
            <Window
              title={zh ? "我的做事方式" : "My working practices"}
              tag={<Pill>My Duet</Pill>}
            >
              <Bubble author={zh ? "研究标准" : "Research standard"} accent>
                {zh ? "研究结论附来源。" : "Include sources in research."}
              </Bubble>
              <Bubble author={zh ? "汇报习惯" : "Reporting preference"}>
                {zh
                  ? "优先说明对当前项目的影响。"
                  : "Explain the impact on current projects first."}
              </Bubble>
            </Window>
          </Reveal>
        ) : (
          <Reveal at={at(2)} style={{ height: "100%" }}>
            <Window title={zh ? "任务分别推进" : "Tasks progress separately"}>
              {tasks.map((task, index) => (
                <Reveal key={task} at={at(2) + Math.round(index * 0.56 * fps)}>
                  <File
                    title={step >= 3 ? results[index] : task}
                    detail={
                      step >= 3
                        ? zh
                          ? "回到 My Duet"
                          : "Returned to My Duet"
                        : zh
                          ? "处理中"
                          : "In progress"
                    }
                    active={step >= 3}
                  />
                </Reveal>
              ))}
              <Reveal at={at(5)}>
                <div style={{ ...row, gap: 14 }}>
                  <Pill active>Tracking</Pill>
                  <Text size={29}>
                    {zh ? "随时找回持续事项" : "Find ongoing work again"}
                  </Text>
                </div>
              </Reveal>
            </Window>
          </Reveal>
        )}
      </div>
    </Frame>
  );
}

export function S04MessageCenter({ language, cues }: SceneProps) {
  const zh = language === "zh-CN";
  const { fps } = useVideoConfig();
  const { step, elapsed, at } = useSteps(cues, language, [
    ["消息中心保存", "Message Center keeps the source"],
    ["员工可以浏览", "Employees can browse"],
    ["定期拉取", "check on a schedule"],
    ["公司的共同信息范围", "This brings outside changes"],
    ["一条资讯可以先留在这里", "An item can remain here"],
  ]);
  const update = zh
    ? "申请处理状态需要对客户可见"
    : "Customers need visibility into application status";
  const summary = zh
    ? "示例更新：让客户知道申请正在处理到哪一步。"
    : "Illustrative update: let customers see where an application stands.";
  const categories = zh
    ? ["客户反馈", "行业资讯", "业务通知"]
    : ["Customer feedback", "Industry news", "Business notice"];
  return (
    <Frame
      number={4}
      title={
        zh
          ? "消息中心先让变化被看见"
          : "Message Center makes a change discoverable"
      }
      strap={
        zh
          ? "从外部来源，进入公司共享信息"
          : "An external signal enters a shared information space"
      }
    >
      <div
        style={{
          ...split,
          gridTemplateColumns: step < 2 ? "1.1fr 0.8fr" : "1fr 0.72fr",
        }}
      >
        <Window
          title={
            step === 0
              ? zh
                ? "外部资讯"
                : "External update"
              : "Message Center"
          }
          tag={<Pill>{zh ? "周一" : "Monday"}</Pill>}
          accent
        >
          <div
            style={{
              ...stack,
              gap: 10,
              padding: 20,
              borderLeft: `4px solid ${red}`,
              background: softRed,
            }}
          >
            <Label>
              {step === 0
                ? zh
                  ? "爬虫发现"
                  : "Found by crawler"
                : zh
                  ? "分类 · 监管更新"
                  : "Category · Regulatory update"}
            </Label>
            <Text size={46} style={{ fontWeight: 750, lineHeight: 1.16 }}>
              {update}
            </Text>
            <Reveal at={at(1)}>
              <Text size={30}>{summary}</Text>
            </Reveal>
            <Reveal at={at(1)}>
              <div style={{ ...row, flexWrap: "wrap" }}>
                <Pill>Source</Pill>
                <Text size={27} muted>
                  {zh
                    ? "监管公告原文 · 周一发布"
                    : "Original notice · Published Monday"}
                </Text>
              </div>
            </Reveal>
          </div>
          {step >= 2 && (
            <div style={{ ...row, flexWrap: "wrap" }}>
              {["Search", "Subscribe", "Scheduled check"].map(
                (control, index) => (
                  <Reveal
                    key={control}
                    at={
                      index === 2
                        ? at(3)
                        : at(2) + Math.round(index * 0.52 * fps)
                    }
                  >
                    <Pill
                      active={
                        index ===
                        (step === 2
                          ? Number(elapsed >= fps * 0.85)
                          : step === 3
                            ? 2
                            : 1)
                      }
                    >
                      {control}
                    </Pill>
                  </Reveal>
                ),
              )}
            </div>
          )}
          {step >= 4 && (
            <Reveal at={at(4)}>
              <div style={{ ...row, paddingTop: 7 }}>
                <Person
                  name="Nora"
                  role={zh ? "市场研究" : "Market research"}
                  active
                />
                <Pill active>{zh ? "研究订阅" : "Research subscription"}</Pill>
              </div>
            </Reveal>
          )}
        </Window>
        {step < 2 ? (
          <div
            style={{ ...stack, alignContent: "center", justifyItems: "start" }}
          >
            <Reveal at={at(1)}>
              <Arrow color={red} />
            </Reveal>
            <Reveal at={at(1) + Math.round(0.24 * fps)}>
              <Card style={{ ...stack, padding: 30, height: "auto", gap: 13 }}>
                <Text size={38} style={{ fontWeight: 730 }}>
                  Message Center
                </Text>
                <Text size={32} muted>
                  {zh
                    ? "保留来源与发布时间，整理成可阅读的摘要。"
                    : "Preserves the source and date, with a readable summary."}
                </Text>
              </Card>
            </Reveal>
          </div>
        ) : (
          <Reveal at={at(2)} style={{ height: "100%" }}>
            <Window title={zh ? "共享资讯" : "Shared information"}>
              {categories.map((category, index) => (
                <Reveal
                  key={category}
                  at={at(2) + Math.round(index * 0.2 * fps)}
                >
                  <File title={category} />
                </Reveal>
              ))}
              <Reveal at={at(5)}>
                <Text size={30} muted>
                  {zh
                    ? "其他资讯仍可查阅，等需要时再行动。"
                    : "Other items stay available until someone needs to act."}
                </Text>
              </Reveal>
            </Window>
          </Reveal>
        )}
      </div>
    </Frame>
  );
}

export function S05Research({ language, cues }: SceneProps) {
  const zh = language === "zh-CN";
  const { fps } = useVideoConfig();
  const { step, at } = useSteps(cues, language, [
    ["正在关注客户申请体验", "customer application experience"],
    ["Nora 判断值得投入", "Nora decides to investigate"],
    ["交付物逐渐具体起来", "The outputs become specific"],
    ["哪些流程受影响", "Which processes are affected"],
    ["Maya 收到", "Maya receives these materials"],
  ]);
  const assignment = zh
    ? "请调研影响，整理报告，并给出需求建议。"
    : "Research the impact, prepare a report, and propose requirements.";
  const artifacts = zh
    ? ["影响清单", "研究报告", "初步需求"]
    : ["Impact checklist", "Research report", "Draft user story"];
  return (
    <Frame
      number={5}
      title={
        zh
          ? "Nora 把资讯变成调研任务"
          : "Nora turns an update into a research task"
      }
      strap={
        zh
          ? "由人决定是否投入，让下一位负责人有材料可用"
          : "Nora decides to investigate and prepares a handoff for Product"
      }
    >
      <div style={{ ...split, gridTemplateColumns: "0.85fr 1fr" }}>
        <Window
          title="My Duet · Nora"
          tag={<Pill>Private</Pill>}
          accent={step < 3}
        >
          <Person
            name="Nora"
            role={zh ? "市场研究" : "Market research"}
            active
          />
          <div style={row}>
            <Pill>Tracking</Pill>
            <Text size={32}>
              {zh ? "客户申请体验" : "Customer application experience"}
            </Text>
          </div>
          <Reveal at={at(1)}>
            <Bubble author="Nora’s Duet" accent={step === 1}>
              {zh
                ? "这条更新可能影响现有申请页面，建议增加状态展示。"
                : "This update may affect the application page. We may need to display its status."}
            </Bubble>
          </Reveal>
          <Reveal at={at(2)}>
            <Bubble
              author={zh ? "Nora · 开始调研" : "Nora · Start research"}
              accent={step >= 2}
            >
              {assignment}
            </Bubble>
          </Reveal>
        </Window>
        {step < 4 ? (
          <Reveal at={at(3)} style={{ height: "100%" }}>
            <Window
              title={
                zh ? "可交接的研究材料" : "Research materials for the handoff"
              }
              accent
            >
              {artifacts.map((artifact, index) => (
                <Reveal
                  key={artifact}
                  at={at(3) + Math.round(index * 0.36 * fps)}
                >
                  <File
                    title={artifact}
                    detail={
                      index === 1
                        ? zh
                          ? "附监管公告原文"
                          : "Includes the original regulatory notice"
                        : undefined
                    }
                    active={index === 1}
                  />
                </Reveal>
              ))}
            </Window>
          </Reveal>
        ) : (
          <Reveal at={at(4)} style={{ height: "100%" }}>
            <Window title={artifacts[1]} tag={<Pill>Source</Pill>} accent>
              <Bubble author={zh ? "客户界面" : "Customer interface"} accent>
                {zh
                  ? "在申请页面显示处理状态。"
                  : "Display the processing status on the application page."}
              </Bubble>
              <Bubble author={zh ? "内部报表" : "Internal report"}>
                {zh
                  ? "导出时包含同一项处理状态。"
                  : "Include that same processing status in the export."}
              </Bubble>
              <Reveal at={at(5)}>
                <div style={{ ...row, gap: 22 }}>
                  <Arrow color={red} />
                  <Person
                    name="Maya"
                    role={
                      zh
                        ? "产品负责人 · 接手材料"
                        : "Product owner · Receives materials"
                    }
                    active
                  />
                </div>
              </Reveal>
            </Window>
          </Reveal>
        )}
      </div>
    </Frame>
  );
}

export function S06Topics({ language, cues }: SceneProps) {
  const zh = language === "zh-CN";
  const { fps } = useVideoConfig();
  const { step, at } = useSteps(cues, language, [
    ["大家在群里讨论", "The people discuss scope"],
    ["Maya 看到", "Maya can take a message"],
    ["与此同时", "Meanwhile, Nora’s Duet"],
    ["每个 Duet 带着", "Each Duet brings"],
    ["人直接联系自己的 Duet", "The Duets coordinate through the topic"],
  ]);
  const topic = zh ? "申请状态可见" : "Application status visibility";
  const contributions = zh
    ? [
        ["Maya’s Duet", "10:14", "写清客户展示和内部导出两项需求。"],
        ["Alex’s Duet", "10:32", "确认页面与导出可以复用同一状态字段。"],
        ["Sam’s Duet", "11:05", "验收时核对同一申请的两个状态。"],
      ]
    : [
        [
          "Maya’s Duet",
          "10:14",
          "Specify customer display and internal export.",
        ],
        [
          "Alex’s Duet",
          "10:32",
          "Check whether both can use the same status field.",
        ],
        ["Sam’s Duet", "11:05", "Verify both values for the same application."],
      ];
  return (
    <Frame
      number={6}
      title={
        zh
          ? "人在群聊，Duet 在共享主题协作"
          : "People chat. Duets work in a shared topic."
      }
      strap={
        zh
          ? "从一条真人消息，到围绕主题异步交接"
          : "From a human message to asynchronous work in a topic"
      }
    >
      <div style={{ ...split, gridTemplateColumns: "0.91fr 1fr" }}>
        <Window
          title={
            step === 0
              ? "My Duet · Nora"
              : zh
                ? "申请体验工作群"
                : "Application experience"
          }
          tag={<Pill>{step === 0 ? "Private" : "Group chat"}</Pill>}
          accent={step < 3}
        >
          {step === 0 ? (
            <Bubble author={zh ? "Nora → 自己的 Duet" : "Nora → Her own Duet"}>
              {zh
                ? "请建一个真人群，邀请 Maya、Alex 和 Sam。"
                : "Create a group chat with Maya, Alex, and Sam."}
            </Bubble>
          ) : (
            <Reveal at={at(1)}>
              <div style={stack}>
                <div style={{ ...row, gap: 9, flexWrap: "wrap" }}>
                  {["Nora", "Maya", "Alex", "Sam"].map((name) => (
                    <Pill key={name} active={name === "Maya" && step >= 2}>
                      {name}
                    </Pill>
                  ))}
                </div>
                <Bubble author="Nora" accent={step === 2}>
                  {zh
                    ? "我们先确定申请状态展示和导出的范围。"
                    : "Let’s agree on the scope for displaying and exporting application status."}
                </Bubble>
              </div>
            </Reveal>
          )}
          <Reveal at={at(2)}>
            <div
              style={{
                ...stack,
                gap: 11,
                padding: 18,
                border: `1px solid ${line}`,
                background: "#fff",
              }}
            >
              <div
                style={{
                  ...row,
                  justifyContent: "space-between",
                  flexWrap: "wrap",
                }}
              >
                <Pill active>Ask my Duet</Pill>
                <Label>{zh ? "Maya 的私聊" : "Maya’s private chat"}</Label>
              </div>
              <div style={{ ...row, flexWrap: "wrap", gap: 13 }}>
                <Text size={28} style={{ fontWeight: 730 }}>
                  Maya
                </Text>
                <Arrow color={red} />
                <Person name="Maya’s Duet" agent active />
                <Reveal at={at(3)}>
                  <div style={{ ...row, gap: 12 }}>
                    <Arrow color={red} />
                    <Pill>Topic</Pill>
                  </div>
                </Reveal>
              </div>
            </div>
          </Reveal>
          {step >= 4 && (
            <Reveal at={at(4)}>
              <Text size={27} muted>
                {zh
                  ? "Duet 按主人在群里的权限读取共享历史和文件。"
                  : "Each Duet reads shared history and files with its employee’s group access."}
              </Text>
            </Reveal>
          )}
        </Window>
        <Reveal at={at(3)} style={{ height: "100%" }}>
          <Window title={topic} tag={<Pill active>Topic</Pill>} accent>
            <div style={{ ...stack, gap: 8 }}>
              <Label>Topic owner · Nora’s Duet</Label>
              <File
                title={zh ? "研究报告" : "Research report"}
                detail="Shared files"
                active
              />
              <Text size={25} muted>
                {zh
                  ? "邀请 Maya、Alex 和 Sam 的 Duet 留言。"
                  : "Invites Maya’s, Alex’s, and Sam’s Duets to contribute."}
              </Text>
            </div>
            <div style={{ ...stack, gap: 0 }}>
              {contributions.map(([name, time, message], index) => (
                <Reveal key={name} at={at(4) + Math.round(index * 0.62 * fps)}>
                  <div
                    style={{ padding: "6px 0", borderTop: `1px solid ${line}` }}
                  >
                    <div style={{ ...row, justifyContent: "space-between" }}>
                      <Label>{name}</Label>
                      <Label>{time}</Label>
                    </div>
                    <Text size={28}>{message}</Text>
                  </div>
                </Reveal>
              ))}
            </div>
            {step >= 5 && (
              <Reveal at={at(5)}>
                <Pill active>
                  {zh
                    ? "分工明确 · 异步接力"
                    : "Responsibilities agreed · Asynchronous handoff"}
                </Pill>
              </Reveal>
            )}
          </Window>
        </Reveal>
      </div>
    </Frame>
  );
}
