/** Real browser + isolated application stack. No business API or WebSocket interception. */
const { chromium } = require("playwright");
const fs = require("node:fs/promises");
const path = require("node:path");
const os = require("node:os");
const { execFileSync } = require("node:child_process");
const { randomUUID } = require("node:crypto");
const runId = randomUUID().slice(0, 8);
const { login } = require("./helpers.cjs");
const redact = (value) =>
  String(value).replace(
    /E2E_ONLY_FAKE_[A-Za-z0-9_-]+/g,
    "[redacted-fixture-value]",
  );
const artifactDir = path.resolve(
  process.env.TWINTEAMS_E2E_ARTIFACT_DIR ||
    path.join(os.tmpdir(), `twinteams-browser-e2e-${Date.now()}`),
);
let activeBrowser;
const report = {
  kind: "real-browser-e2e",
  business_api_interception: false,
  websocket_interception: false,
  synthetic_model:
    (process.env.TWINTEAMS_E2E_MODEL_MODE || process.env.MODEL_MODE) !==
    "live-gpt-6-luna",
  scenario: process.env.TWINTEAMS_E2E_SCENARIO || "offline-regression",
  model_mode: process.env.TWINTEAMS_E2E_MODEL_MODE || process.env.MODEL_MODE,
  test_namespace: process.env.TWINTEAMS_E2E_NAMESPACE,
  run_id: runId,
  started_at: new Date().toISOString(),
  cases: [],
  status: "failed",
};
for (const [signal, code] of [
  ["SIGINT", 130],
  ["SIGTERM", 143],
])
  process.once(signal, async () => {
    report.status = "failed";
    report.error = `Interrupted by ${signal}`;
    try {
      await activeBrowser?.close();
    } finally {
      await fs.mkdir(artifactDir, { recursive: true });
      await fs.writeFile(
        path.join(artifactDir, "report.json"),
        redact(JSON.stringify(report, null, 2)),
      );
      process.exit(code);
    }
  });
async function guardedURL(value, instance) {
  if (!value || !instance)
    throw new Error("Launcher URL and TWINTEAMS_E2E_INSTANCE_ID are required");
  const url = new URL(value);
  if (
    url.protocol !== "http:" ||
    !["127.0.0.1", "localhost", "[::1]"].includes(url.hostname) ||
    ["8000", "8001"].includes(url.port) ||
    url.username ||
    url.password ||
    url.pathname !== "/" ||
    url.search ||
    url.hash
  )
    throw new Error(
      "E2E URL must be an isolated loopback HTTP origin outside production ports",
    );
  const response = await fetch(new URL("/health", url), {
    signal: AbortSignal.timeout(10000),
  });
  if (!response.ok) throw new Error("Isolated E2E health check failed");
  const health = await response.json();
  if (health.browser_e2e !== true || health.e2e_instance_id !== instance)
    throw new Error(
      "E2E health marker/instance mismatch; refusing browser mutations",
    );
  if (health.model_mode && health.model_mode !== report.model_mode)
    throw new Error("E2E health model mode mismatch");
  if (
    report.model_mode === "live-gpt-6-luna" &&
    (health.model_mode !== report.model_mode ||
      health.scenario !== report.scenario)
  )
    throw new Error("Live E2E model/scenario marker mismatch");
  return url.origin;
}
async function main() {
  await fs.mkdir(artifactDir, { recursive: true });
  const instance = process.env.TWINTEAMS_E2E_INSTANCE_ID;
  const baseURL = await guardedURL(process.env.TWINTEAMS_E2E_URL, instance);
  const secondaryURL = await guardedURL(
    process.env.TWINTEAMS_E2E_SECONDARY_URL || process.env.SECONDARY_URL,
    instance,
  );
  if (
    baseURL === secondaryURL ||
    !report.test_namespace ||
    !["deterministic-offline", "live-gpt-6-luna"].includes(report.model_mode)
  )
    throw new Error(
      "Two isolated APIs, test namespace and an explicit supported model mode are required",
    );
  report.instance_id = instance;
  report.base_url = baseURL;
  report.secondary_url = secondaryURL;
  const live = report.model_mode === "live-gpt-6-luna";
  if (
    live &&
    ![
      "continuous-workflow",
      "tracking-resumption",
      "first-private-group-tracking",
      "scheduled-task-workflow",
      "interaction-live",
      "read-unread-live",
      "group-routing-live",
      "group-routing-follow-on",
    ].includes(process.env.TWINTEAMS_E2E_SCENARIO)
  )
    throw new Error("Explicit live scenario is required");
  report.actual_model = live ? "gpt-6-luna" : null;
  const suites = live && ["group-routing-live", "group-routing-follow-on"].includes(report.scenario)
    ? [require("./group-routing-live.cjs")]
    : live && report.scenario === "read-unread-live"
    ? [require("./read-unread-live.cjs")]
    : live && report.scenario === "interaction-live"
    ? [require("./interaction-live.cjs")]
    : !live && report.scenario === "group-route-recovery"
    ? [require("./group-route-recovery.cjs")]
    : !live && report.scenario === "proactivity"
    ? [require("./proactivity.cjs")]
    : !live && report.scenario === "ask-user"
    ? [require("./ask-user.cjs")]
    : !live && report.scenario === "file-delivery-preview"
    ? [require("./file-preview.cjs")]
    : !live && report.scenario === "personal-onboarding"
    ? [require("./onboarding.cjs")]
    : live
    ? [
        require(
          report.scenario === "scheduled-task-workflow"
            ? "./scheduled-task-live.cjs"
            : report.scenario === "first-private-group-tracking"
            ? "./first-private-group-tracking.cjs"
            : report.scenario === "tracking-resumption"
              ? "./tracking-resumption.cjs"
              : "./continuous-workflow.cjs",
        ),
      ]
    : [
        require("./conversations.cjs"),
        require("./notifications.cjs"),
        require("./avatar.cjs"),
        require("./personal.cjs"),
        require("./scheduled-tasks.cjs"),
        require("./scheduled-task-dispatch.cjs"),
      ];
  const registered = suites
    .flatMap((suite) => suite.cases || [])
    .sort((a, b) => Number(!!a.last) - Number(!!b.last));
  if (
    !registered.length ||
    registered.some(
      (test) => !test.name || typeof test.run !== "function" || test.skip,
    )
  )
    throw new Error(
      "E2E requires named executable cases; empty or skipped suites cannot pass",
    );
  if (new Set(registered.map((test) => test.name)).size !== registered.length)
    throw new Error("Duplicate E2E case names");
  const pattern = process.env.TWINTEAMS_E2E_CASE
    ? new RegExp(process.env.TWINTEAMS_E2E_CASE)
    : null;
  const cases = registered.filter(
    (test) => !pattern || pattern.test(test.name),
  );
  if (!cases.length) throw new Error("E2E case selection matched zero cases");
  report.selection = pattern
    ? {
        pattern: pattern.source,
        selected: cases.length,
        total_registered: registered.length,
        full_suite: false,
      }
    : { full_suite: true, total_registered: registered.length };
  const browser = await chromium.launch({
    headless: process.env.TWINTEAMS_E2E_HEADED !== "1",
    ...(process.env.TWINTEAMS_BROWSER_CHANNEL
      ? { channel: process.env.TWINTEAMS_BROWSER_CHANNEL }
      : {}),
  });
  activeBrowser = browser;
  report.browser = {
    version: browser.version(),
    channel: process.env.TWINTEAMS_BROWSER_CHANNEL || "playwright-chromium",
  };
  report.source_commit = execFileSync("git", ["rev-parse", "HEAD"], {
    cwd: path.resolve(__dirname, "../.."),
    encoding: "utf8",
  }).trim();
  try {
    let serial = 0;
    for (const test of cases) {
      const started = Date.now(),
        contexts = [],
        errors = [],
        details = {};
      const slug = `${String(++serial).padStart(2, "0")}-${test.name.replace(/[^a-z0-9]+/gi, "-").slice(0, 90)}`;
      const result = {
        name: test.name,
        status: "failed",
        duration_ms: 0,
        sensitive: !!test.sensitive,
        artifacts: [],
        details,
      };
      let label = 0,
        checkpointWrites = Promise.resolve();
      const checkpointPath = path.join(artifactDir, `${slug}-progress.json`);
      const t = {
        browser,
        baseURL,
        secondaryURL,
        artifactDir,
        unique: (prefix = "case") =>
          `${prefix.replace(/[^a-z0-9_-]/gi, "-")}-${runId}-${serial}-${++label}`,
        record: (key, value) => {
          details[key] = value;
          const snapshot = redact(
            JSON.stringify(
              {
                case: test.name,
                status: "running",
                updated_at: new Date().toISOString(),
                details,
              },
              null,
              2,
            ),
          );
          checkpointWrites = checkpointWrites
            .then(() => fs.writeFile(checkpointPath, snapshot))
            .catch((error) => {
              errors.push(
                redact(`Cannot save E2E checkpoint: ${error.message}`),
              );
            });
        },
        session: async (code = "DEV-AAA", options = {}) => {
          const origin = options.baseURL || baseURL;
          if (![baseURL, secondaryURL].includes(origin))
            throw new Error("Session origin is outside guarded APIs");
          const context = await browser.newContext({
            baseURL: origin,
            acceptDownloads: true,
            viewport: { width: options.width || 1360, height: 900 },
            locale: "en-US",
            reducedMotion: "reduce",
            permissions: ["clipboard-read", "clipboard-write"],
          });
          contexts.push(context);
          if (options.contextSetup) await options.contextSetup(context);
          if (!test.sensitive)
            await context.tracing.start({
              screenshots: true,
              snapshots: true,
              sources: true,
            });
          const page = await context.newPage();
          page.setDefaultTimeout(12000);
          page.on("pageerror", (error) => errors.push(redact(error.message)));
          await page.goto(`${origin}/#/chat`);
          const auth = code ? await login(page, code) : null;
          return { context, page, owner: auth?.owner, baseURL: origin };
        },
      };
      let timer;
      try {
        await Promise.race([
          test.run(t),
          new Promise((_, reject) => {
            timer = setTimeout(
              () =>
                reject(
                  new Error(
                    `E2E case exceeded ${(test.timeout || 150000) / 1000} seconds`,
                  ),
                ),
              test.timeout || 150000,
            );
          }),
        ]);
        if (errors.length)
          throw new Error(`Browser errors: ${errors.join("; ")}`);
        result.status = "passed";
      } catch (error) {
        result.error = redact(error.stack || error);
        console.error(`FAIL ${test.name}: ${result.error}`);
        if (!test.sensitive)
          for (let i = 0; i < contexts.length; i++) {
            const pages = contexts[i].pages();
            for (let j = 0; j < pages.length; j++) {
              const target = path.join(artifactDir, `${slug}-${i}-${j}.png`);
              try {
                await pages[j].screenshot({ path: target, fullPage: true });
                result.artifacts.push(path.basename(target));
              } catch {}
            }
          }
      } finally {
        clearTimeout(timer);
        await checkpointWrites.catch(() => {});
        for (let i = 0; i < contexts.length; i++) {
          if (!test.sensitive) {
            const target = path.join(artifactDir, `${slug}-${i}.zip`);
            try {
              await contexts[i].tracing.stop(
                result.status === "failed" || test.retainTrace ? { path: target } : {},
              );
              if (result.status === "failed" || test.retainTrace)
                result.artifacts.push(path.basename(target));
            } catch {}
          }
          await contexts[i].close().catch(() => {});
        }
      }
      result.duration_ms = Date.now() - started;
      report.cases.push(result);
      if (result.status === "passed")
        console.log(`PASS ${test.name} (${result.duration_ms}ms)`);
      await fs.writeFile(
        path.join(artifactDir, "report.json"),
        redact(JSON.stringify(report, null, 2)),
      );
      if (result.status !== "passed") {
        report.not_run = cases.slice(serial).map((item) => item.name);
        break;
      }
    }
    report.status =
      report.cases.length &&
      report.cases.every((test) => test.status === "passed")
        ? "passed"
        : "failed";
  } finally {
    await browser.close();
  }
  if (report.status !== "passed") process.exitCode = 1;
}
main()
  .catch((error) => {
    report.error = redact(error.stack || error);
    console.error(report.error);
    process.exitCode = 1;
  })
  .finally(async () => {
    report.finished_at = new Date().toISOString();
    report.passed = report.cases.filter(
      (test) => test.status === "passed",
    ).length;
    report.failed = report.cases.filter(
      (test) => test.status === "failed",
    ).length;
    await fs.mkdir(artifactDir, { recursive: true });
    await fs.writeFile(
      path.join(artifactDir, "report.json"),
      redact(JSON.stringify(report, null, 2)),
    );
    console.log(
      `E2E ${report.status}: ${report.passed} passed, ${report.failed} failed; report ${path.join(artifactDir, "report.json")}`,
    );
  });
