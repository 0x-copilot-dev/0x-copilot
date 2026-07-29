#!/usr/bin/env node
/* design-parity · one-command runner for the SURFACE-LANGUAGE surface
 * =========================================================================
 * Board lanes (PRD-01) and the no-spec view (PRD-02), design vs live, by
 * computed style. Does all four SKILL.md steps end to end:
 *
 *   1. build the design bundle      (esbuild, from node_modules — no CDN)
 *   2. render the live surfaces     (vitest + jsdom, the REAL renderers)
 *   3. extract both sides           (headless chromium, one driver, one
 *                                    extractor, per state)
 *   4. compare + write the reports  (out/report-<state>.md + out/report.md)
 *
 * Nothing here is state the reader has to reproduce by hand: the static server
 * binds an ephemeral port and is closed in a finally, so a failed run does not
 * leave a listener behind.
 *
 * Run: node tools/design-parity/lib/run-surface-language-parity.mjs
 * ========================================================================= */
import { spawn, spawnSync } from "node:child_process";
import { createServer } from "node:http";
import { mkdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { dirname, extname, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const TOOLS = resolve(HERE, "..");
const REPO = resolve(TOOLS, "../..");
const SURFACE = "surfaces/surface-language";
const OUT = resolve(TOOLS, SURFACE, "out");

/**
 * One row per report. `design` and `live` are deliberately separate from
 * `state`, because two of the five reuse a page:
 *
 *  - `no-spec-board` renders BoardRenderer's spec-less path against the SAME
 *    design view as `no-spec`. The generic view is the degradation target for
 *    every archetype (PRD-02), so proving two archetypes land in the same place
 *    is the point; it needs no second design state and no second anchor file.
 *  - `board-capped` has no design counterpart at all. Its design side is the
 *    TABLE surface, borrowed only for `.sft-cap` — the design's one cap-line
 *    register. `anchors/board-capped.json` says so on the tin.
 */
const STATES = [
  { state: "board", design: "board", live: "board", anchors: "board" },
  {
    state: "board-changed",
    design: "board-changed",
    live: "board-changed",
    anchors: "board-changed",
  },
  { state: "no-spec", design: "no-spec", live: "no-spec", anchors: "no-spec" },
  {
    state: "no-spec-board",
    design: "no-spec",
    live: "no-spec-board",
    anchors: "no-spec",
  },
  {
    state: "board-capped",
    design: "table",
    live: "board-capped",
    anchors: "board-capped",
  },
];

const VIEWPORT = "1200x816";
const MIME = {
  ".css": "text/css; charset=utf-8",
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".woff2": "font/woff2",
};

function run(command, args) {
  return new Promise((resolveRun, rejectRun) => {
    const child = spawn(command, args, { cwd: TOOLS, stdio: "inherit" });
    child.once("error", rejectRun);
    child.once("close", (code, signal) => {
      if (code === 0) resolveRun();
      else
        rejectRun(
          new Error(
            `${command} failed (code=${String(code)}, signal=${String(signal)})`,
          ),
        );
    });
  });
}

function staticServer() {
  return createServer((request, response) => {
    const url = new URL(request.url || "/", "http://127.0.0.1");
    const target = resolve(TOOLS, `.${decodeURIComponent(url.pathname)}`);
    if (!(target === TOOLS || target.startsWith(`${TOOLS}${sep}`))) {
      response.writeHead(403).end("Forbidden");
      return;
    }
    try {
      const file = statSync(target).isDirectory()
        ? resolve(target, "index.html")
        : target;
      response.writeHead(200, {
        "content-type": MIME[extname(file)] || "application/octet-stream",
        "cache-control": "no-store",
      });
      response.end(readFileSync(file));
    } catch {
      response.writeHead(404).end("Not found");
    }
  });
}

/**
 * What the LIVE side was measured from.
 *
 * A parity report is a measurement, and a measurement without its subject
 * identified is a rumour. Both PRDs are landing in the same tree this harness
 * reads, so a report taken while `packages/surface-renderers` is dirty can be
 * describing code that no longer exists ten minutes later — the summary says so
 * out loud rather than letting the reader assume it is current.
 */
function liveSubject() {
  const git = (args) =>
    spawnSync("git", args, { cwd: REPO, encoding: "utf8" }).stdout?.trim() ??
    "";
  const head = git(["rev-parse", "--short", "HEAD"]) || "(unknown)";
  const branch = git(["rev-parse", "--abbrev-ref", "HEAD"]) || "(unknown)";
  const dirty = git([
    "status",
    "--porcelain",
    "--",
    "packages/surface-renderers",
    "packages/chat-surface",
    "packages/design-system",
  ]);
  return {
    head,
    branch,
    // `git status --porcelain` indents every line by its index/worktree status
    // columns; trimming the block as a whole would eat only the first line's,
    // leaving a ragged list. Trim per line.
    dirty: dirty.length > 0 ? dirty.split("\n").map((line) => line.trim()) : [],
  };
}

const listen = (server) =>
  new Promise((resolveListen, rejectListen) => {
    server.once("error", rejectListen);
    server.listen(0, "127.0.0.1", () => {
      server.off("error", rejectListen);
      resolveListen();
    });
  });
const close = (server) =>
  new Promise((resolveClose) => server.close(resolveClose));

mkdirSync(OUT, { recursive: true });

console.log("[surface-language] build design bundle");
await run(process.execPath, ["lib/prepare-surface-language-design.mjs"]);

console.log("[surface-language] render the real archetype renderers");
await run(resolve(REPO, "node_modules/.bin/vitest"), [
  "run",
  "--config",
  "vitest.config.mjs",
  "lib/render-live-surface-language.test.tsx",
]);

const server = staticServer();
await listen(server);
const address = server.address();
if (address === null || typeof address === "string") {
  await close(server);
  throw new Error("Parity server did not expose a TCP port.");
}
const baseUrl = `http://127.0.0.1:${address.port}`;
const rows = [];

try {
  for (const entry of STATES) {
    const anchors = `${SURFACE}/anchors/${entry.anchors}.json`;
    const design = `${SURFACE}/out/design-${entry.state}.json`;
    const live = `${SURFACE}/out/live-${entry.state}.json`;
    const report = `${SURFACE}/out/report-${entry.state}.md`;
    console.log(`[surface-language] ${entry.state}`);
    await run(process.execPath, [
      "lib/extract-playwright.mjs",
      "--url",
      `${baseUrl}/${SURFACE}/design/index.html?state=${entry.design}`,
      "--anchors",
      anchors,
      "--side",
      "design",
      "--out",
      design,
      "--viewport",
      VIEWPORT,
      "--wait-for",
      `[data-parity-ready="${entry.design}"]`,
      "--delay",
      "200",
      // The design side IS the baseline: an anchor that cannot be found there
      // is a broken selector, not a finding, and must fail loudly rather than
      // quietly shrink the report.
      "--fail-on-missing",
    ]);
    await run(process.execPath, [
      "lib/extract-playwright.mjs",
      "--url",
      `${baseUrl}/${SURFACE}/live/${entry.live}.html`,
      "--anchors",
      anchors,
      "--side",
      "live",
      "--out",
      live,
      "--viewport",
      VIEWPORT,
      "--wait-for",
      "#parity-frame",
      "--delay",
      "100",
      // Deliberately NOT --fail-on-missing: "present in design, absent in live"
      // is the single most important thing this report can say.
    ]);
    await run(process.execPath, [
      "lib/compare.mjs",
      design,
      live,
      "--anchors",
      anchors,
      "--out",
      report,
      "--surface",
      "surface-language",
      "--state",
      entry.state,
    ]);
    const result = JSON.parse(
      readFileSync(resolve(TOOLS, report.replace(/\.md$/, ".json")), "utf8"),
    );
    const designProfile = JSON.parse(
      readFileSync(resolve(TOOLS, design), "utf8"),
    );
    const liveProfile = JSON.parse(readFileSync(resolve(TOOLS, live), "utf8"));
    rows.push({
      state: entry.state,
      counts: result.counts,
      designMatched: Object.values(designProfile).filter(
        (item) => item.matched !== false,
      ).length,
      designTotal: Object.keys(designProfile).length,
      liveMatched: Object.values(liveProfile).filter(
        (item) => item.matched !== false,
      ).length,
      liveTotal: Object.keys(liveProfile).length,
    });
  }
} finally {
  await close(server);
}

const totals = rows.reduce(
  (sum, row) => {
    for (const key of ["high", "medium", "low", "info"])
      sum[key] += row.counts[key] || 0;
    return sum;
  },
  { high: 0, medium: 0, low: 0, info: 0 },
);
const provenance = JSON.parse(
  readFileSync(resolve(TOOLS, SURFACE, "design/PROVENANCE.json"), "utf8"),
);
const subject = liveSubject();
const lines = [
  "# Design-parity — surface language (board lanes · no-spec view)",
  "",
  "Computed-style comparison of the vendored `0xCopilot Surface Language` mock against the real `@0x-copilot/surface-renderers` archetypes, for the two renders `docs/plan/surface-language/` covers.",
  "",
  `**Aggregate:** 🔴 HIGH ${totals.high} · 🟠 MEDIUM ${totals.medium} · 🟡 LOW ${totals.low} · ⚪ INFO ${totals.info}.`,
  "",
  `**Measured** ${new Date().toISOString()} against \`${subject.branch}\` @ \`${subject.head}\`${
    subject.dirty.length > 0
      ? ` with **${subject.dirty.length} uncommitted change(s)** in the packages this reads:\n\n\`\`\`\n${subject.dirty.join("\n")}\n\`\`\`\n\nRe-run before treating any row below as current.`
      : ", working tree clean for the packages this reads."
  }`,
  "",
  "| State | PRD | Design anchors | Live anchors | HIGH | MEDIUM | Report |",
  "|---|---|---:|---:|---:|---:|---|",
  ...rows.map((row) => {
    const prd = row.state.startsWith("no-spec") ? "PRD-02" : "PRD-01";
    return `| \`${row.state}\` | ${prd} | ${row.designMatched}/${row.designTotal} | ${row.liveMatched}/${row.liveTotal} | ${row.counts.high || 0} | ${row.counts.medium || 0} | [report-${row.state}.md](./report-${row.state}.md) |`;
  }),
  "",
  "A live anchor count below the design count is not a harness fault — it is the finding. Each unmatched label is listed in its own report as `present in design, ABSENT in live`.",
  "",
  "## Provenance",
  "",
  `- Design source: DesignSync project \`${provenance.source.project.projectId}\` (${provenance.source.project.name}), page “${provenance.source.page}”, fetched ${provenance.source.fetchedAt}.`,
  ...Object.entries(provenance.files).map(
    ([file, hash]) => `- \`${file}\` — SHA-256 \`${hash}\`.`,
  ),
  `- ${provenance.note}`,
  "- `index.html`, `_globals.js`, `_mount.jsx` and `copilot-v3.css` in `design/` are HARNESS files this repo wrote (mount shell + import shim), not design source.",
  "- Live pages are serialized from the shipping renderers with the real `design-system/src/styles.css` + `chat-surface/src/thread-canvas/surface-language.css`.",
  "",
  "## Reproduce",
  "",
  "```bash",
  "node tools/design-parity/lib/run-surface-language-parity.mjs",
  "```",
];
writeFileSync(resolve(OUT, "report.md"), `${lines.join("\n")}\n`);
console.log(`${SURFACE}/out/report.md`);
