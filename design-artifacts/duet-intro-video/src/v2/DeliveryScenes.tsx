import { useCurrentFrame, interpolate } from "remotion";
import {
  C,
  Card,
  File,
  Frame,
  Label,
  Person,
  Pill,
  Reveal,
  Stage,
  Text,
  Arrow,
  col,
  row,
  tr,
  useSteps,
  type SceneProps,
} from "./Shared";

export function S07Memory({ language, cues }: SceneProps) {
  const { step, elapsed } = useSteps(cues, language, [
    ["每四小时", "Every four hours"],
    ["Git 留下", "Git preserves"],
    ["主动建立", "creates a Tracking"],
    ["首节点", "first node"],
    ["详细进度", "Detailed progress"],
  ]);
  const names = [
    tr(language, "短期上下文", "Recent context"),
    "Dream + Git",
    "Tracking",
  ];
  const active = step === 0 ? 0 : step < 3 ? 1 : 2;
  return (
    <Frame
      number={7}
      title={tr(
        language,
        "周四，接着周一的工作",
        "Pick up on Thursday where Monday began",
      )}
      strap={tr(
        language,
        "记住眼前的对话，也能找回持续中的事项。",
        "Remember the conversation and recover the ongoing work.",
      )}
    >
      <div style={{ ...row, alignItems: "stretch", height: "100%", gap: 36 }}>
        <div style={{ width: 510, ...col }}>
          <Card>
            <Person
              name="Nora"
              role={tr(
                language,
                "周四 · 回到 My Duet",
                "Thursday · Back in My Duet",
              )}
            />
            <Text size={37} style={{ marginTop: 30 }}>
              {tr(
                language,
                "“申请状态那件事到哪了？”",
                "“Where are we on application status?”",
              )}
            </Text>
          </Card>
          {names.map((name, i) => (
            <div
              key={name}
              style={{
                ...row,
                padding: "23px 28px",
                borderLeft: `5px solid ${active === i ? C.red : C.line}`,
                background: active === i ? C.white : undefined,
                color: active === i ? C.ink : C.muted,
                fontSize: 32,
              }}
            >
              <span style={{ fontSize: 24 }}>0{i + 1}</span>
              {name}
            </div>
          ))}
        </div>
        <Stage elapsed={elapsed} style={{ flex: 1 }}>
          <Card style={{ height: "100%" }}>
            {step === 0 ? (
              <>
                <Label>
                  {tr(
                    language,
                    "刚才的话接得上",
                    "Continue the immediate conversation",
                  )}
                </Label>
                <Text size={42}>
                  {tr(
                    language,
                    "滚动摘要 + 最近问答原文",
                    "Rolling summary + recent exchanges",
                  )}
                </Text>
                <div
                  style={{
                    marginTop: 35,
                    padding: 25,
                    background: "#f1f2ef",
                    borderRadius: 10,
                  }}
                >
                  <Text muted size={27}>
                    {tr(
                      language,
                      "较早内容已整理为摘要",
                      "Earlier context, summarized",
                    )}
                  </Text>
                  <Text style={{ marginTop: 16 }}>
                    {tr(
                      language,
                      "本周正在研究申请体验；报告需附原文来源。",
                      "Research the application journey. Include original sources.",
                    )}
                  </Text>
                </div>
                <div style={{ marginTop: 22, ...col, gap: 12 }}>
                  <Text>
                    {tr(
                      language,
                      "Nora：先看客户页面和内部报表。",
                      "Nora: Start with the customer page and report.",
                    )}
                  </Text>
                  <Text>
                    {tr(
                      language,
                      "Duet：已将两处列为调研范围。",
                      "Duet: Both are included in the research scope.",
                    )}
                  </Text>
                </div>
              </>
            ) : step < 3 ? (
              <>
                <Label>
                  {tr(language, "长期认识逐步积累", "Build durable knowledge")}
                </Label>
                <div style={{ ...row, justifyContent: "space-between" }}>
                  <Text size={46}>Dream</Text>
                  <Pill active>
                    {tr(language, "每 4 小时", "Every 4 hours")}
                  </Pill>
                </div>
                <div style={{ ...row, marginTop: 30 }}>
                  <Pill>{tr(language, "可见群聊", "Visible group chats")}</Pill>
                  <Pill>
                    {tr(
                      language,
                      "与自己的 Duet 对话",
                      "Own Duet conversations",
                    )}
                  </Pill>
                </div>
                <Text muted style={{ marginTop: 28 }}>
                  {tr(
                    language,
                    "增量整理事实、偏好与决定",
                    "Consolidate new facts, preferences and decisions",
                  )}
                </Text>
                {step === 2 && (
                  <>
                    <div
                      style={{
                        borderTop: `1px solid ${C.line}`,
                        marginTop: 25,
                        paddingTop: 24,
                      }}
                    >
                      <Text size={31}>
                        {tr(
                          language,
                          "计划日期：周五 → 下周一",
                          "Plan date: Friday → Next Monday",
                        )}
                      </Text>
                      <div style={{ ...row, marginTop: 15 }}>
                        <Pill active>Git · v18</Pill>
                        <Text size={26} muted>
                          {tr(
                            language,
                            "查看来源与修改缘由",
                            "Trace the source and reason",
                          )}
                        </Text>
                      </div>
                    </div>
                    <Text size={25} muted style={{ marginTop: 24 }}>
                      {tr(
                        language,
                        "Duet 依据有效决定更新事实。",
                        "Duet updates the fact from the applicable decision.",
                      )}
                    </Text>
                  </>
                )}
              </>
            ) : (
              <>
                <Label>{tr(language, "持续事项", "Ongoing work")}</Label>
                <Text size={44}>Tracking</Text>
                <Card accent style={{ marginTop: 24, padding: 24 }}>
                  <Text size={31}>
                    {tr(
                      language,
                      "申请状态响应",
                      "Application status response",
                    )}
                  </Text>
                  <Text size={25} muted style={{ marginTop: 10 }}>
                    {tr(
                      language,
                      "目标：把影响研究推进到可验证的功能",
                      "Goal: Turn impact research into a verified feature",
                    )}
                  </Text>
                  <div style={{ marginTop: 15 }}>
                    <Pill active>
                      {tr(
                        language,
                        "首节点元数据留在上下文",
                        "Root metadata stays in context",
                      )}
                    </Pill>
                  </div>
                </Card>
                {step >= 4 && (
                  <Text size={29} style={{ marginTop: 25 }}>
                    {tr(
                      language,
                      "用首节点定位原事项",
                      "Use the root node to locate the work",
                    )}
                  </Text>
                )}
                {step >= 5 && (
                  <div
                    style={{
                      marginTop: 25,
                      ...row,
                      alignItems: "stretch",
                      gap: 12,
                    }}
                  >
                    {[
                      tr(language, "周一\n启动调研", "Mon\nResearch"),
                      tr(language, "周二\n报告交付", "Tue\nReport"),
                      tr(language, "周三\n需求明确", "Wed\nScope"),
                      tr(language, "周四\n开发接手", "Thu\nBuild"),
                    ].map((label, i) => (
                      <div
                        key={label}
                        style={{
                          flex: 1,
                          padding: 16,
                          background: i === 3 ? C.wash : "#f1f2ef",
                          borderBottom: `3px solid ${i === 3 ? C.red : C.line}`,
                          fontSize: 25,
                          lineHeight: 1.5,
                          whiteSpace: "pre-line",
                        }}
                      >
                        {label}
                      </div>
                    ))}
                  </div>
                )}
              </>
            )}
          </Card>
        </Stage>
      </div>
    </Frame>
  );
}

function PolicyRows({ active }: { active: number }) {
  return (
    <div style={{ ...col, gap: 15 }}>
      {[
        ["pull_request.opened", "Sam’s Duet"],
        ["changes_requested", "Alex’s Duet"],
        ["approved", "LoopLab"],
      ].map(([event, target], i) => (
        <div
          key={event}
          style={{
            ...row,
            justifyContent: "space-between",
            padding: "21px 24px",
            background: active === i ? C.wash : "#f4f5f2",
            borderLeft: `4px solid ${active === i ? C.red : "transparent"}`,
            fontSize: 27,
          }}
        >
          <span>{event}</span>
          <span style={{ color: active === i ? C.red : C.muted }}>
            → {target}
          </span>
        </div>
      ))}
    </div>
  );
}

export function S08Loop({ language, cues }: SceneProps) {
  const { step, elapsed } = useSteps(cues, language, [
    ["GitHub", "GitHub"],
    ["独立运行", "separate service"],
    ["Sam 的 Duet", "Sam’s Duet understands"],
    ["它发现", "It finds"],
    ["结构化结果", "reads that outcome"],
  ]);
  return (
    <Frame
      number={8}
      title={tr(
        language,
        "事件到了，下一棒交给谁",
        "An event arrives. Who takes the next step?",
      )}
      strap={tr(
        language,
        "Agent Loop 执行规则；Duet 理解并完成工作。",
        "Agent Loop follows policy. Duet understands and performs the work.",
      )}
    >
      <div style={{ ...row, alignItems: "stretch", height: "100%", gap: 34 }}>
        <Card style={{ width: 735, padding: 26 }}>
          <Label>
            {tr(language, "来自 Alex 的交付", "Alex’s deliverable")}
          </Label>
          <Person name="Alex’s Duet" agent role="Development" />
          <File
            title="PR #248 · Status visibility"
            detail={tr(
              language,
              "关联需求 · Story APP-42",
              "Linked requirement · Story APP-42",
            )}
            active
            style={{ marginTop: 22 }}
          />
          {step >= 1 && (
            <Reveal at={0}>
              <div
                style={{
                  marginTop: 20,
                  padding: 20,
                  background: "#f1f2ef",
                  borderRadius: 10,
                }}
              >
                <Text size={27} muted>
                  GitHub event
                </Text>
                <Text size={34} style={{ marginTop: 12 }}>
                  pull_request.opened
                </Text>
                <Text size={25} muted style={{ marginTop: 10 }}>
                  PR #248 · commit a41c8e2
                </Text>
              </div>
            </Reveal>
          )}
          {step >= 3 && (
            <div style={{ marginTop: 25, ...row }}>
              <Arrow />
              <Person name="Sam’s Duet" agent role="Review" />
            </div>
          )}
        </Card>
        <Stage elapsed={elapsed} style={{ flex: 1 }}>
          <Card style={{ height: "100%" }}>
            {step < 3 ? (
              <>
                <Label>
                  {tr(language, "独立的调度核心", "Independent routing core")}
                </Label>
                <div
                  style={{
                    ...row,
                    justifyContent: "space-between",
                    marginBottom: 30,
                  }}
                >
                  <Text size={46}>Agent Loop</Text>
                  <Pill active>policy</Pill>
                </div>
                {step >= 2 ? (
                  <>
                    <PolicyRows active={0} />
                    <Text size={29} muted style={{ marginTop: 32 }}>
                      {tr(
                        language,
                        "确定性的条件匹配，无需 LLM。",
                        "Deterministic matching. No LLM in the core.",
                      )}
                    </Text>
                  </>
                ) : (
                  <Text size={38} muted style={{ marginTop: 75 }}>
                    {tr(
                      language,
                      "等待事件，按约定分发",
                      "Wait for an event, then follow the agreement",
                    )}
                  </Text>
                )}
              </>
            ) : step < 5 ? (
              <>
                <Label>{tr(language, "Sam 的评审", "Sam’s review")}</Label>
                <Text size={42}>
                  {tr(
                    language,
                    "对照同一份需求检查",
                    "Check the same requirement",
                  )}
                </Text>
                <div style={{ ...col, marginTop: 34 }}>
                  <Card style={{ padding: 24 }}>
                    <Text size={31}>
                      ✓{" "}
                      {tr(
                        language,
                        "客户能看到处理状态",
                        "Customers see their application status",
                      )}
                    </Text>
                  </Card>
                  <Card accent={step >= 4} style={{ padding: 24 }}>
                    <Text size={31}>
                      {step >= 4 ? "!" : "○"}{" "}
                      {tr(
                        language,
                        "内部报表包含相同状态",
                        "The internal report contains that status",
                      )}
                    </Text>
                  </Card>
                </div>
                {step >= 4 && (
                  <Text size={30} style={{ marginTop: 28, color: C.red }}>
                    {tr(
                      language,
                      "发现缺项：导出报表没有状态列。",
                      "Missing: the status column in the exported report.",
                    )}
                  </Text>
                )}
              </>
            ) : (
              <>
                <Label>
                  {tr(
                    language,
                    "返回明确的评审结果",
                    "Return an explicit review outcome",
                  )}
                </Label>
                <Pill active>changes_requested</Pill>
                <Text size={34} style={{ marginTop: 22 }}>
                  {tr(
                    language,
                    "请在内部报表中加入处理状态。",
                    "Add processing status to the internal report.",
                  )}
                </Text>
                <Text size={26} muted style={{ marginTop: 18 }}>
                  PR #248 · commit a41c8e2
                </Text>
                <div style={{ marginTop: 35 }}>
                  <PolicyRows active={1} />
                </div>
              </>
            )}
          </Card>
        </Stage>
      </div>
    </Frame>
  );
}

export function S09Repair({ language, cues }: SceneProps) {
  const { step, elapsed } = useSteps(cues, language, [
    ["补上报表字段", "adds the report field"],
    ["新版本", "updated version"],
    ["接下来", "then send the change"],
    ["每次交付", "Each handoff"],
  ]);
  return (
    <Frame
      number={9}
      title={tr(
        language,
        "修复后，再走完同一条流程",
        "A correction returns to the same workflow",
      )}
      strap={tr(
        language,
        "同一需求、同一 PR，带着新版本继续前进。",
        "The same requirement and PR continue with a new version.",
      )}
    >
      <div style={{ ...row, marginBottom: 30 }}>
        {["APP-42", "PR #248", step === 0 ? "a41c8e2" : "b72de90"].map((x) => (
          <Pill key={x} active={x === "b72de90"}>
            {x}
          </Pill>
        ))}
      </div>
      <Stage elapsed={elapsed} style={{ height: 510 }}>
        <div style={{ ...row, alignItems: "stretch", height: "100%", gap: 30 }}>
          <Card style={{ flex: 1 }}>
            <Person name="Alex’s Duet" agent role="Development" />
            <Text size={31} style={{ marginTop: 26 }}>
              {step === 0
                ? tr(
                    language,
                    "收到具体缺项和对应版本",
                    "Receive the missing requirement and version",
                  )
                : tr(language, "补齐报表状态列", "Add the status column")}
            </Text>
            <div
              style={{
                marginTop: 22,
                border: `1px solid ${C.line}`,
                borderRadius: 10,
                overflow: "hidden",
              }}
            >
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "1fr 1fr",
                  background: "#eceeea",
                  padding: 16,
                  fontSize: 23,
                }}
              >
                <span>Application</span>
                <span>Processing status</span>
              </div>
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "1fr 1fr",
                  padding: 20,
                  fontSize: 28,
                }}
              >
                <span>APP-1048</span>
                <span style={{ color: step >= 1 ? C.green : C.red }}>
                  {step >= 1 ? "In review" : "—"}
                </span>
              </div>
            </div>
            {step >= 1 && (
              <div style={{ marginTop: 18 }}>
                <Pill active>New commit · b72de90</Pill>
              </div>
            )}
          </Card>
          <div style={{ ...row }}>
            <Arrow />
          </div>
          <Card style={{ flex: 1, opacity: step >= 2 ? 1 : 0.35 }}>
            <Person name="Sam’s Duet" agent role="Review" />
            <Text size={35} style={{ marginTop: 35 }}>
              {tr(language, "复审新版本", "Review the updated version")}
            </Text>
            <div style={{ marginTop: 34, ...col }}>
              <Text size={29}>
                ✓ {tr(language, "客户页面", "Customer page")}
              </Text>
              <Text size={29}>
                ✓ {tr(language, "导出报表", "Exported report")}
              </Text>
            </div>
            {step >= 2 && (
              <div style={{ marginTop: 35 }}>
                <Pill active>approved</Pill>
              </div>
            )}
          </Card>
          <div style={{ ...row }}>
            <Arrow />
          </div>
          <Card
            accent={step >= 3}
            style={{ flex: 1, opacity: step >= 3 ? 1 : 0.35 }}
          >
            <Label>{tr(language, "下一次交付", "Next handoff")}</Label>
            <Text size={46}>LoopLab</Text>
            <div style={{ marginTop: 32, ...col }}>
              <Text size={29}>
                {tr(
                  language,
                  "需求与验收条件",
                  "Requirement and acceptance criteria",
                )}
              </Text>
              <Text size={29}>
                {tr(language, "变更与提交版本", "Change and commit version")}
              </Text>
              <Text size={29}>
                {tr(
                  language,
                  "可测试的产品环境",
                  "An environment ready to test",
                )}
              </Text>
            </div>
            {step >= 4 && (
              <div style={{ marginTop: 30 }}>
                <Pill active>
                  {tr(
                    language,
                    "下一位可以直接接手",
                    "Ready for the next participant",
                  )}
                </Pill>
              </div>
            )}
          </Card>
        </div>
      </Stage>
    </Frame>
  );
}

function BrowserCheck({
  done,
  exportCheck = false,
}: {
  done: boolean;
  exportCheck?: boolean;
}) {
  return (
    <Card style={{ height: 345, padding: 0, overflow: "hidden" }}>
      <div
        style={{
          background: "#eceeea",
          padding: "16px 24px",
          fontSize: 22,
          color: C.muted,
        }}
      >
        ● ● ●{" "}
        <span style={{ marginLeft: 25 }}>
          {exportCheck ? "report.csv" : "portal.example / application / 1048"}
        </span>
      </div>
      <div style={{ padding: 30 }}>
        <Label>{exportCheck ? "Internal report" : "Application details"}</Label>
        {exportCheck ? (
          <>
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "1fr 1.5fr",
                fontSize: 24,
                paddingBottom: 15,
                borderBottom: `1px solid ${C.line}`,
              }}
            >
              <span>Application</span>
              <span>Processing status</span>
            </div>
            <div
              style={{
                ...row,
                fontSize: 32,
                justifyContent: "space-between",
                marginTop: 24,
              }}
            >
              <span>APP-1048</span>
              <Pill active={done}>In review</Pill>
            </div>
          </>
        ) : (
          <>
            <Text size={35}>Application APP-1048</Text>
            <div style={{ ...row, marginTop: 32 }}>
              <Text size={27}>Processing status</Text>
              <Pill active={done}>In review</Pill>
            </div>
          </>
        )}
        {done && (
          <Text size={25} style={{ color: C.green, marginTop: 25 }}>
            ✓ Assertion passed
          </Text>
        )}
      </div>
    </Card>
  );
}

export function S10Testing({ language, cues }: SceneProps) {
  const { step, elapsed } = useSteps(cues, language, [
    ["LoopLab 的 Agent", "LoopLab’s agent"],
    ["随后", "independent browser"],
    ["逐条检查", "checking the page"],
    ["测试通过", "passing result"],
    ["若有检查失败", "If a check fails"],
  ]);
  const frame = useCurrentFrame();
  return (
    <Frame
      number={10}
      title={tr(
        language,
        "让实际运行证明结果",
        "Let an actual run show the result",
      )}
      strap={tr(
        language,
        "先编排，再由独立浏览器回放固定版本。",
        "Compose the flow, then replay a fixed version independently.",
      )}
    >
      <div style={{ ...row, marginBottom: 28 }}>
        <Pill active={step === 0}>
          {tr(language, "验收条件", "Acceptance criteria")}
        </Pill>
        <Arrow />
        <Pill active={step === 1}>
          {tr(language, "Agent 编排", "Agent composition")}
        </Pill>
        <Arrow />
        <Pill active={step >= 2}>
          {tr(language, "独立回放", "Independent replay")}
        </Pill>
      </div>
      <Stage elapsed={elapsed} style={{ height: 490 }}>
        {step === 1 ? (
          <Card style={{ height: "100%", padding: 42 }}>
            <Label>LoopLab · Test composition</Label>
            <Text size={42}>
              {tr(
                language,
                "调查真实页面，保存可复用节点",
                "Inspect real pages and save reusable nodes",
              )}
            </Text>
            <div
              style={{
                display: "flex",
                marginTop: 48,
                alignItems: "center",
                gap: 14,
              }}
            >
              {[
                tr(language, "打开详情", "Open details"),
                tr(language, "读取状态", "Read status"),
                tr(language, "导出报表", "Export report"),
                tr(language, "检查状态列", "Check column"),
              ].map((label, i) => (
                <div key={label} style={{ display: "contents" }}>
                  {i > 0 && <Arrow />}
                  <Card
                    accent={i === Math.min(3, Math.floor((frame % 240) / 60))}
                    style={{ flex: 1, padding: 25 }}
                  >
                    <Text size={29}>{label}</Text>
                  </Card>
                </div>
              ))}
            </div>
            <Text muted size={28} style={{ marginTop: 42 }}>
              {tr(
                language,
                "保存为固定版本的流程 · Flow v3",
                "Save the flow with fixed versions · Flow v3",
              )}
            </Text>
          </Card>
        ) : step < 4 ? (
          <>
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "1fr 1fr",
                gap: 30,
              }}
            >
              <BrowserCheck done={step >= 2} />
              <BrowserCheck done={step >= 3} exportCheck />
            </div>
            <div
              style={{ ...row, justifyContent: "space-between", marginTop: 27 }}
            >
              <Text size={30}>
                {step === 0
                  ? tr(
                      language,
                      "同一申请，两处状态必须一致。",
                      "One application. The same status in both places.",
                    )
                  : tr(
                      language,
                      "固定步骤 · 无需模型参与回放",
                      "Fixed steps · No model in the replay",
                    )}
              </Text>
              <Pill active={step >= 3}>
                {step >= 3 ? "2 / 2 assertions" : "Flow v3"}
              </Pill>
            </div>
          </>
        ) : (
          <div
            style={{ ...row, alignItems: "stretch", height: "100%", gap: 30 }}
          >
            <Card style={{ flex: 1 }}>
              <Label>Run #RL-318 · commit b72de90</Label>
              <Text size={48} style={{ color: C.green }}>
                ✓ passed
              </Text>
              <div style={{ ...row, marginTop: 30 }}>
                <Pill>2 assertions</Pill>
                <Pill>4 steps</Pill>
              </div>
              <File
                title={tr(
                  language,
                  "验收报告与截图",
                  "Acceptance report and screenshots",
                )}
                detail="Flow v3 · Run #RL-318"
                active
                style={{ marginTop: 35 }}
              />
            </Card>
            <Card accent={step === 5} style={{ flex: 1 }}>
              <Label>
                {step === 5
                  ? tr(language, "如果检查失败", "If a check fails")
                  : tr(language, "结果进入下一步", "The result moves forward")}
              </Label>
              <Text size={39}>
                {step === 5
                  ? tr(
                      language,
                      "失败位置 + 证据 → 修复",
                      "Failure location + evidence → repair",
                    )
                  : tr(
                      language,
                      "同一运行产生两种可用结果",
                      "One run produces two useful outputs",
                    )}
              </Text>
              <div style={{ marginTop: 35, ...col }}>
                {step === 5 ? (
                  <>
                    <Person name="Alex’s Duet" agent />
                    <Text size={30}>
                      {tr(
                        language,
                        "修复后，再执行相同验收流程。",
                        "Correct the issue, then run the same checks again.",
                      )}
                    </Text>
                  </>
                ) : (
                  <>
                    <File
                      title={tr(
                        language,
                        "人能阅读的报告",
                        "A report people can read",
                      )}
                    />
                    <File
                      title={tr(
                        language,
                        "规则能处理的事件",
                        "An event policy can route",
                      )}
                    />
                  </>
                )}
              </div>
            </Card>
          </div>
        )}
      </Stage>
    </Frame>
  );
}

export function S11Progress({ language, cues }: SceneProps) {
  const { step, elapsed } = useSteps(cues, language, [
    ["Nora 知道", "Nora can see"],
    ["Maya 看见", "Maya has evidence"],
    ["Alex 和 Sam", "Alex’s and Sam’s"],
    ["人需要做决定", "When a decision"],
  ]);
  const entries = [
    [
      "Nora",
      tr(
        language,
        "研究已转成经过验证的功能",
        "Research led to a verified feature",
      ),
    ],
    [
      "Maya",
      tr(
        language,
        "需求已验收，待安排发布",
        "Requirement verified; release next",
      ),
    ],
    [
      "Alex",
      tr(language, "代码已修复并通过复验", "Change corrected and verified"),
    ],
    [
      "Sam",
      tr(language, "验收运行与证据已保存", "Test run and evidence recorded"),
    ],
  ];
  return (
    <Frame
      number={11}
      title={tr(
        language,
        "同一结果，回答每个人的问题",
        "One result answers different responsibilities",
      )}
      strap={tr(
        language,
        "共享事实，保留各自的目标与后续决定。",
        "Share the evidence while tracking each person’s goals.",
      )}
    >
      <div style={{ ...row, alignItems: "stretch", height: "100%", gap: 34 }}>
        <div style={{ width: 490, ...col }}>
          <File
            title={tr(language, "验收报告", "Acceptance report")}
            detail="Run #RL-318 · b72de90"
            active
          />
          <Card>
            <Label>{tr(language, "共享去向", "Shared destinations")}</Label>
            <Text size={32}>Topic board</Text>
            <Text size={32} style={{ marginTop: 22 }}>
              Message Center
            </Text>
            <Text size={32} style={{ marginTop: 22 }}>
              Agent Loop
            </Text>
          </Card>
          <Text size={27} muted>
            {tr(
              language,
              "所有记录引用同一份运行证据。",
              "Every record refers to the same run evidence.",
            )}
          </Text>
        </div>
        <Stage elapsed={elapsed} style={{ flex: 1 }}>
          {step < 4 ? (
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "1fr 1fr",
                height: "100%",
                gap: 24,
              }}
            >
              {entries.map(([name, result], i) => (
                <Card
                  key={name}
                  accent={step === i + 1 || (step === 3 && i >= 2)}
                  style={{ opacity: step >= Math.min(3, i + 1) ? 1 : 0.3 }}
                >
                  <Person name={name} role="Personal Tracking" />
                  <Text size={31} style={{ marginTop: 30 }}>
                    {result}
                  </Text>
                  <Text size={23} muted style={{ marginTop: 22 }}>
                    ↳ Run #RL-318
                  </Text>
                </Card>
              ))}
            </div>
          ) : (
            <Card style={{ height: "100%" }}>
              <Person name="Maya" role="Product owner" />
              <Text size={45} style={{ marginTop: 32 }}>
                {tr(
                  language,
                  "现在可以决定发布时间",
                  "Now decide when to release",
                )}
              </Text>
              <div style={{ marginTop: 30, ...col }}>
                <Text size={32}>
                  ✓{" "}
                  {tr(
                    language,
                    "两项验收条件已通过",
                    "Both acceptance conditions passed",
                  )}
                </Text>
                <Text size={32}>
                  ✓{" "}
                  {tr(
                    language,
                    "报告、步骤与截图可查看",
                    "Report, steps and screenshots available",
                  )}
                </Text>
                <Text size={32}>
                  {tr(
                    language,
                    "下一步：安排发布窗口与沟通",
                    "Next: Choose the release window and communication",
                  )}
                </Text>
              </div>
              <div style={{ marginTop: 38 }}>
                <Pill active>
                  {tr(
                    language,
                    "人作决定，Duet 带齐材料",
                    "Human decision, prepared by Duet",
                  )}
                </Pill>
              </div>
            </Card>
          )}
        </Stage>
      </div>
    </Frame>
  );
}

export function S12WholeLoop({ language, cues }: SceneProps) {
  const { step, elapsed } = useSteps(cues, language, [
    ["团队可以先", "A team can first"],
    ["人继续决定", "Employees continue"],
    ["随着更多岗位", "As more outputs"],
  ]);
  const frame = useCurrentFrame();
  return (
    <Frame
      number={12}
      title={tr(
        language,
        "一段成熟协作，长成自动闭环",
        "An established workflow becomes an AI-native loop",
      )}
      strap={tr(
        language,
        "从个人委托开始，让产出成为下一位的输入。",
        "Begin with personal delegation. Make each output useful to the next role.",
      )}
    >
      <div style={{ position: "relative", height: "100%" }}>
        <svg
          width="1720"
          height="610"
          style={{ position: "absolute", inset: 0 }}
        >
          <ellipse
            cx="860"
            cy="290"
            rx="825"
            ry="220"
            fill="none"
            stroke={C.line}
            strokeWidth="3"
          />
          <ellipse
            cx="860"
            cy="290"
            rx="825"
            ry="220"
            fill="none"
            stroke={C.red}
            strokeWidth="4"
            strokeDasharray="150 3600"
            strokeDashoffset={
              -interpolate(frame, [0, 1800], [0, 3500], {
                extrapolateRight: "clamp",
              })
            }
          />
        </svg>
        <div
          style={{
            position: "absolute",
            left: 620,
            top: 25,
            width: 480,
            textAlign: "center",
            background: C.paper,
          }}
        >
          <Text size={41} style={{ color: C.red }}>
            Agent Loop
          </Text>
          <Text size={24} muted>
            {tr(
              language,
              "policy 承接稳定的交接",
              "Policy carries established handoffs",
            )}
          </Text>
        </div>
        <div
          style={{
            position: "absolute",
            left: 105,
            right: 105,
            top: 176,
            ...row,
            gap: 20,
          }}
        >
          {[
            [
              "Message Center",
              tr(language, "资讯与研究", "Information and research"),
            ],
            [
              "Personal Duets",
              tr(
                language,
                "需求 · 开发 · 评审",
                "Requirements · Build · Review",
              ),
            ],
            [
              "LoopLab",
              tr(language, "运行与验收证据", "Runs and acceptance evidence"),
            ],
          ].map(([name, detail], i) => (
            <div key={name} style={{ display: "contents" }}>
              {i > 0 && <Arrow />}
              <Card
                style={{
                  flex: i === 1 ? 1.3 : 1,
                  textAlign: "center",
                  padding: 28,
                }}
              >
                <Text size={35}>{name}</Text>
                <Text size={25} muted style={{ marginTop: 18 }}>
                  {detail}
                </Text>
              </Card>
            </div>
          ))}
        </div>
        <Stage
          elapsed={elapsed}
          style={{
            position: "absolute",
            left: 260,
            right: 260,
            top: 375,
            height: 90,
            textAlign: "center",
          }}
        >
          <Text size={32}>
            {step === 0
              ? tr(
                  language,
                  "资讯 → 报告 → Story → PR → 验收证据",
                  "Update → Research → Story → PR → Evidence",
                )
              : step === 1
                ? tr(
                    language,
                    "将反复出现的交接写成 policy",
                    "Turn recurring handoffs into policy",
                  )
                : step === 2
                  ? tr(
                      language,
                      "人决定目标、优先级与承诺",
                      "People decide goals, priorities and commitments",
                    )
                  : tr(
                      language,
                      "每完成一段，就扩展一段",
                      "Extend the loop one working process at a time",
                    )}
          </Text>
        </Stage>
        <div
          style={{
            position: "absolute",
            left: 250,
            right: 250,
            top: 515,
            ...row,
            justifyContent: "space-between",
          }}
        >
          {["Nora", "Maya", "Alex", "Sam"].map((name) => (
            <Person key={name} name={name} />
          ))}
        </div>
      </div>
    </Frame>
  );
}
