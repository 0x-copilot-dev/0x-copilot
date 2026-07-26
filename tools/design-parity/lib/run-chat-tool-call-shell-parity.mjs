#!/usr/bin/env node
/* design-parity · CHAT / TOOL-CALL SHELL runner
 * ===========================================================================
 * One reproducible command for the authoritative six-state harness:
 *
 *   node tools/design-parity/lib/run-chat-tool-call-shell-parity.mjs
 *
 * It starts a private static server, materialises the controlled Design
 * Compiler reference, renders the REAL RunDestination fixture, extracts both
 * sides with the shared Playwright computed-style driver, and writes one
 * state report plus an aggregate Markdown report. It never takes or compares
 * screenshots.
 * =========================================================================== */
import { createHash } from "node:crypto";
import { execFileSync, spawn } from "node:child_process";
import { createServer } from "node:http";
import {
  existsSync,
  mkdirSync,
  readFileSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { dirname, extname, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const TOOLS = resolve(HERE, "..");
const REPO = resolve(TOOLS, "../..");
const SURFACE = "surfaces/chat-tool-call-shell";
const SURFACE_DIR = resolve(TOOLS, SURFACE);
const OUT = resolve(SURFACE_DIR, "out");
const STATES = [
  "focus-thinking",
  "studio-third-party-read",
  "studio-web-chat-only",
  "studio-csv-chat-only",
  "studio-write-held",
  "studio-wrap-file",
];

const MIME = {
  ".css": "text/css; charset=utf-8",
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".svg": "image/svg+xml",
  ".woff2": "font/woff2",
};

function run(command, args) {
  // Do not use execFileSync here. The comparison browser loads pages from the
  // in-process static server below; a synchronous child would block Node's
  // event loop and deadlock that server during page.goto().
  return new Promise((resolveRun, rejectRun) => {
    const child = spawn(command, args, { cwd: TOOLS, stdio: "inherit" });
    child.once("error", rejectRun);
    child.once("close", (code, signal) => {
      if (code === 0) resolveRun();
      else
        rejectRun(
          new Error(
            `Parity command failed (${command}, code=${String(code)}, signal=${String(signal)}).`,
          ),
        );
    });
  });
}

function git(args) {
  try {
    return execFileSync("git", args, { cwd: REPO, encoding: "utf8" }).trim();
  } catch {
    return "unavailable";
  }
}

function staticServer() {
  return createServer((request, response) => {
    const url = new URL(request.url ?? "/", "http://127.0.0.1");
    const pathname = decodeURIComponent(url.pathname);
    const target = resolve(TOOLS, `.${pathname}`);
    // Serve the harness subtree only; the runner never makes the repository a
    // general-purpose file server.
    if (!(target === TOOLS || target.startsWith(`${TOOLS}${sep}`))) {
      response.writeHead(403).end("Forbidden");
      return;
    }
    try {
      const file = statSync(target).isDirectory()
        ? resolve(target, "index.html")
        : target;
      response.writeHead(200, {
        "content-type": MIME[extname(file)] ?? "application/octet-stream",
        "cache-control": "no-store",
      });
      response.end(readFileSync(file));
    } catch {
      response.writeHead(404).end("Not found");
    }
  });
}

function listen(server) {
  return new Promise((resolveListen, rejectListen) => {
    server.once("error", rejectListen);
    server.listen(0, "127.0.0.1", () => {
      server.off("error", rejectListen);
      resolveListen();
    });
  });
}

function close(server) {
  return new Promise((resolveClose) => server.close(resolveClose));
}

function profilePath(side, state) {
  return `${SURFACE}/out/${side}-${state}.json`;
}

function reportPath(state) {
  return `${SURFACE}/out/report-${state}.md`;
}

function sha256(path) {
  return createHash("sha256").update(readFileSync(path)).digest("hex");
}

function baselineGaps(anchorsPath, liveProfile) {
  const anchors = JSON.parse(readFileSync(anchorsPath, "utf8"));
  return (
    anchors.elements
      // A baseline-gap annotation is historical context, not a waiver. Keep it
      // in the aggregate only while the corresponding live anchor is still
      // absent; once production renders it, reporting the old claim would make
      // the parity evidence less truthful than the strict comparator output.
      .filter(
        (element) =>
          element.baselineGap &&
          (liveProfile[element.label] === undefined ||
            liveProfile[element.label].matched === false),
      )
      .map((element) => ({
        label: element.label,
        // Do not repeat a stale historical description when the strict map has
        // no live selector at all: that is an observability defect in the map,
        // not proof that production omitted the component.
        detail:
          element.live === undefined
            ? "The strict anchor defines no live selector, so the extractor cannot observe the shipping component."
            : element.baselineGap,
      }))
  );
}

function normalizeMarkdownReport(path) {
  // The shared comparator deliberately terminates its Markdown with a blank
  // paragraph. Keep checked-in baselines conventional: one final newline,
  // never a trailing blank line.
  const normalized = readFileSync(path, "utf8").replace(/\s+$/u, "");
  writeFileSync(path, `${normalized}\n`);
}

function writeAggregate(resultRows) {
  const total = resultRows.reduce(
    (sum, row) => {
      sum.high += row.counts.high ?? 0;
      sum.medium += row.counts.medium ?? 0;
      sum.low += row.counts.low ?? 0;
      sum.info += row.counts.info ?? 0;
      return sum;
    },
    { high: 0, medium: 0, low: 0, info: 0 },
  );
  const reference = resolve(SURFACE_DIR, "design/reference.dc.html");
  const provenance = JSON.parse(
    readFileSync(resolve(SURFACE_DIR, "design/PROVENANCE.json"), "utf8"),
  );
  const baselineGapsByState = resultRows.flatMap((row) =>
    row.gaps.map((gap) => ({ state: row.state, ...gap })),
  );
  const lines = [
    "# Design-parity — chat/tool-call shell",
    "",
    "This is an authoritative computed-style parity baseline for the supplied **Chat & Tool Calls** Design Compiler walkthrough. It measures the real shipping `RunDestination` / `ThreadCanvas` composition using deterministic persisted-message and SSE fixtures. It does **not** use screenshot assertions.",
    "",
    `**Aggregate measured findings:** 🔴 HIGH ${total.high} · 🟠 MEDIUM ${total.medium} · 🟡 LOW ${total.low} · ⚪ INFO ${total.info}.`,
    "",
    "## State coverage",
    "",
    "| Walkthrough state | Design anchors | Live anchors | HIGH | MEDIUM | State report |",
    "|---|---:|---:|---:|---:|---|",
    ...resultRows.map(
      (row) =>
        `| \`${row.state}\` | ${row.designMatched}/${row.designTotal} | ${row.liveMatched}/${row.liveTotal} | ${row.counts.high ?? 0} | ${row.counts.medium ?? 0} | [report-${row.state}.md](./report-${row.state}.md) |`,
    ),
    "",
    "The six states are fixture-backed and state-specific: Focus thinking; third-party Linear read; web chat-only read; local CSV chat-only read; held local-file write; and completed wrap-file result.",
    "",
    "## Provenance and method",
    "",
    `- Supplied design reference: \`${relative(REPO, reference)}\``,
    `- Reference SHA-256: \`${sha256(reference)}\``,
    `- Vendor manifest: [design/PROVENANCE.json](../design/PROVENANCE.json) (source, support runtime, and CSS checksums).`,
    `- Repository commit measured: \`${git(["rev-parse", "HEAD"])}\`; origin/main: \`${git(["rev-parse", "origin/main"])}\`.`,
    `- Design capture: Design Compiler state selected at construction from \`?state=…\`; autoplay disabled; runtime-only \`data-parity-anchor\` attributes added after mount.`,
    `- Live capture: [render-live-chat-tool-call-shell.test.tsx](../../../lib/render-live-chat-tool-call-shell.test.tsx) mounts the shipping desktop \`DesktopWindowFrame\` and \`DestinationOutlet\`, which routes through the real desktop \`RunBinder\` into \`RunDestination\` / \`ThreadCanvas\` with its normal Transport/SSE projection path.`,
    `- Browser extraction: shared [extract-playwright.mjs](../../../lib/extract-playwright.mjs) + [extract-computed.js](../../../lib/extract-computed.js), viewport 1200×816.`,
    `- Comparator: shared [compare.mjs](../../../lib/compare.mjs); every anchor map is \`strict: true\` and declares **no** \`expectDivergence\` waiver.`,
    "",
    "## Measured design-baseline gaps (not waived)",
    "",
    ...(baselineGapsByState.length === 0
      ? ["None."]
      : baselineGapsByState.map(
          (gap) => `- \`${gap.state}\` · \`${gap.label}\`: ${gap.detail}`,
        )),
    "",
    ...(baselineGapsByState.length === 0
      ? [
          "Every strict design anchor has a live counterpart; no absence is waived.",
        ]
      : [
          "These appear as missing-in-live HIGH rows in their state report. They are listed here so the harness cannot accidentally turn the absence into an expected divergence.",
        ]),
    "",
    "## Reproduce",
    "",
    "```bash",
    "node tools/design-parity/lib/run-chat-tool-call-shell-parity.mjs",
    "```",
    "",
    "The runner owns a short-lived loopback static server and regenerates ignored JSON profiles plus tracked Markdown reports. The live HTML itself remains under the harness-wide ignored `surfaces/*/live/` directory.",
  ];
  writeFileSync(resolve(OUT, "report.md"), `${lines.join("\n")}\n`);
}

mkdirSync(OUT, { recursive: true });
console.log("[parity] prepare controlled design reference");
await run(process.execPath, ["lib/prepare-chat-tool-call-shell-reference.mjs"]);
console.log("[parity] render six real RunDestination fixtures");
await run(resolve(REPO, "node_modules/.bin/vitest"), [
  "run",
  "--config",
  "vitest.config.mjs",
  "lib/render-live-chat-tool-call-shell.test.tsx",
]);

const server = staticServer();
await listen(server);
const address = server.address();
if (address === null || typeof address === "string") {
  await close(server);
  throw new Error("Parity server did not expose a TCP port.");
}
const baseUrl = `http://127.0.0.1:${address.port}`;
console.log(`[parity] static capture server ${baseUrl}`);
const rows = [];

try {
  for (const state of STATES) {
    console.log(`[parity] extract + compare ${state}`);
    const anchors = `${SURFACE}/anchors/${state}.json`;
    const design = profilePath("design", state);
    const live = profilePath("live", state);
    await run(process.execPath, [
      "lib/extract-playwright.mjs",
      "--url",
      `${baseUrl}/${SURFACE}/design/index.html?state=${state}`,
      "--anchors",
      anchors,
      "--side",
      "design",
      "--out",
      design,
      "--viewport",
      "1200x816",
      "--wait-for",
      `[data-parity-ready="${state}"]`,
      "--delay",
      "500",
      "--fail-on-missing",
    ]);
    await run(process.execPath, [
      "lib/extract-playwright.mjs",
      "--url",
      `${baseUrl}/${SURFACE}/live/${state}.html`,
      "--anchors",
      anchors,
      "--side",
      "live",
      "--out",
      live,
      "--viewport",
      "1200x816",
      "--wait-for",
      "#parity-frame",
      "--delay",
      "100",
    ]);
    await run(process.execPath, [
      "lib/compare.mjs",
      design,
      live,
      "--anchors",
      anchors,
      "--out",
      reportPath(state),
      "--surface",
      "chat-tool-call-shell",
      "--state",
      state,
    ]);
    normalizeMarkdownReport(resolve(TOOLS, reportPath(state)));
    const designProfile = JSON.parse(
      readFileSync(resolve(TOOLS, design), "utf8"),
    );
    const liveProfile = JSON.parse(readFileSync(resolve(TOOLS, live), "utf8"));
    const report = JSON.parse(
      readFileSync(
        resolve(TOOLS, reportPath(state).replace(/\.md$/, ".json")),
        "utf8",
      ),
    );
    rows.push({
      state,
      counts: report.counts,
      designMatched: Object.values(designProfile).filter(
        (entry) => entry.matched !== false,
      ).length,
      designTotal: Object.keys(designProfile).length,
      liveMatched: Object.values(liveProfile).filter(
        (entry) => entry.matched !== false,
      ).length,
      liveTotal: Object.keys(liveProfile).length,
      gaps: baselineGaps(resolve(TOOLS, anchors), liveProfile),
    });
  }
  writeAggregate(rows);
  console.log(`aggregate report: ${SURFACE}/out/report.md`);
} finally {
  await close(server);
}
