#!/usr/bin/env node
/* Strict four-state computed-style parity runner for Generative Surfaces v3. */
import { spawn } from "node:child_process";
import { createServer } from "node:http";
import { mkdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { dirname, extname, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const TOOLS = resolve(HERE, "..");
const REPO = resolve(TOOLS, "../..");
const SURFACE = "surfaces/generative-surfaces-v3";
const OUT = resolve(TOOLS, SURFACE, "out");
const STATES = ["draft-held", "draft-edit", "bulk-review", "bulk-partial"];
const MIME = {
  ".css": "text/css; charset=utf-8",
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8",
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
console.log("[v3 parity] prepare supplied design");
await run(process.execPath, [
  "lib/prepare-generative-surfaces-v3-reference.mjs",
]);
console.log("[v3 parity] render real review components");
await run(resolve(REPO, "node_modules/.bin/vitest"), [
  "run",
  "--config",
  "vitest.config.mjs",
  "lib/render-live-generative-surfaces-v3.test.tsx",
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
  for (const state of STATES) {
    const anchors = `${SURFACE}/anchors/${state}.json`;
    const design = `${SURFACE}/out/design-${state}.json`;
    const live = `${SURFACE}/out/live-${state}.json`;
    const report = `${SURFACE}/out/report-${state}.md`;
    console.log(`[v3 parity] ${state}`);
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
      "300",
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
      report,
      "--surface",
      "generative-surfaces-v3",
      "--state",
      state,
    ]);
    const result = JSON.parse(
      readFileSync(resolve(TOOLS, report.replace(/\.md$/, ".json")), "utf8"),
    );
    const designProfile = JSON.parse(
      readFileSync(resolve(TOOLS, design), "utf8"),
    );
    const liveProfile = JSON.parse(readFileSync(resolve(TOOLS, live), "utf8"));
    rows.push({
      state,
      counts: result.counts,
      designMatched: Object.values(designProfile).filter(
        (entry) => entry.matched !== false,
      ).length,
      designTotal: Object.keys(designProfile).length,
      liveMatched: Object.values(liveProfile).filter(
        (entry) => entry.matched !== false,
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
const lines = [
  "# Design-parity — Generative Surfaces v3 review states",
  "",
  "Strict Playwright `getComputedStyle` comparison of the user-supplied v3 Design Compiler source against the real `TcStagedDraftSurface` and `TcStagedTableSurface`. No screenshot acceptance and no expected-divergence waivers.",
  "",
  `**Aggregate findings:** 🔴 HIGH ${totals.high} · 🟠 MEDIUM ${totals.medium} · 🟡 LOW ${totals.low} · ⚪ INFO ${totals.info}.`,
  "",
  "| State | Design anchors | Live anchors | HIGH | MEDIUM | Report |",
  "|---|---:|---:|---:|---:|---|",
  ...rows.map(
    (row) =>
      `| \`${row.state}\` | ${row.designMatched}/${row.designTotal} | ${row.liveMatched}/${row.liveTotal} | ${row.counts.high || 0} | ${row.counts.medium || 0} | [report-${row.state}.md](./report-${row.state}.md) |`,
  ),
  "",
  "## Provenance",
  "",
  `- Supplied design SHA-256: \`${provenance.files["reference.dc.html"]}\`.`,
  `- Support runtime SHA-256: \`${provenance.files["support.js"]}\`.`,
  `- Design CSS SHA-256: \`${provenance.files["copilot-v3.css"]}\`.`,
  `- Base design CSS SHA-256: \`${provenance.files["copilot.css"]}\`.`,
  "- `reference.dc.html`, `support.js`, `copilot-v3.css`, and `copilot.css` are byte-for-byte vendored copies.",
  "- `index.html` changes only autoplay and initial walkthrough state.",
  "- Live pages are serialized from shipping React components with the real design-system stylesheet.",
  "",
  "## Reproduce",
  "",
  "```bash",
  "node tools/design-parity/lib/run-generative-surfaces-v3-parity.mjs",
  "```",
];
writeFileSync(resolve(OUT, "report.md"), `${lines.join("\n")}\n`);
console.log(`${SURFACE}/out/report.md`);
