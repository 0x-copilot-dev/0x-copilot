#!/usr/bin/env node
/*
 * Chat & Tool Calls — interaction audit
 *
 * This is deliberately an audit recorder, not a golden test: it presses every
 * mapped tool/agent control on both sides and writes the observed semantics,
 * state transitions, payload visibility, and selected computed styles. That
 * makes a mock defect (or a live-app defect) visible instead of silently
 * accepting whichever behavior happened to be implemented first.
 *
 * Prerequisite: render the live fixture, then serve tools/design-parity:
 *   node_modules/.bin/vitest run --config tools/design-parity/vitest.config.mjs \
 *     tools/design-parity/lib/render-live-chat-tool-calls.test.tsx
 *   (cd tools/design-parity && python3 -m http.server 8099)
 *   node lib/audit-interactions-chat-tool-calls.mjs --out /tmp/audit.json
 */
import { existsSync, readdirSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { chromium } from "playwright";

const HERE = dirname(fileURLToPath(import.meta.url));
const argv = process.argv.slice(2);
const flag = (name, fallback) => {
  const index = argv.indexOf(`--${name}`);
  return index >= 0 ? argv[index + 1] : fallback;
};
const baseUrl = flag("base-url", "http://127.0.0.1:8099");
const out = flag("out", null);

function resolveChromiumExecutable() {
  if (process.env.PARITY_CHROMIUM) return process.env.PARITY_CHROMIUM;
  const cache = join(homedir(), "Library/Caches/ms-playwright");
  if (!existsSync(cache)) return undefined;
  const revision = (name) => Number.parseInt(name.split("-").pop(), 10) || 0;
  for (const dir of readdirSync(cache)
    .filter((name) => name.startsWith("chromium_headless_shell-"))
    .sort((left, right) => revision(right) - revision(left))) {
    const executable = join(
      cache,
      dir,
      "chrome-headless-shell-mac-arm64/chrome-headless-shell",
    );
    if (existsSync(executable)) return executable;
  }
  return undefined;
}

const styleSampler = `
globalThis.__interactionStyle = (element) => {
  const style = getComputedStyle(element);
  return {
    fontFamily: style.fontFamily,
    fontSize: style.fontSize,
    fontWeight: style.fontWeight,
    lineHeight: style.lineHeight,
    letterSpacing: style.letterSpacing,
    color: style.color,
    backgroundColor: style.backgroundColor,
    padding: [style.paddingTop, style.paddingRight, style.paddingBottom, style.paddingLeft].join(" "),
    borderRadius: style.borderRadius,
    gap: style.gap,
    display: style.display,
    flexDirection: style.flexDirection,
  };
};`;

const browser = await chromium.launch({
  executablePath: resolveChromiumExecutable(),
});
const report = { baseUrl, design: {}, live: {} };

try {
  const design = await browser.newPage({
    viewport: { width: 1260, height: 900 },
  });
  await design.goto(
    `${baseUrl}/surfaces/chat-tool-calls/design/index.html?autoAdvance=false`,
    { waitUntil: "load" },
  );
  await design.addScriptTag({ content: styleSampler });
  await design.getByRole("button", { name: /Web search/ }).click();
  const tool = design.locator(".tcall").filter({ hasText: "web.search" });
  const header = tool.locator(".tcall__h");
  const body = tool.locator(".tcall__body");
  await tool.waitFor();
  report.design.tool = {
    initial: await tool.evaluate((card) => ({
      bodyVisible: Boolean(card.querySelector(".tcall__body")),
      headerRole: card.querySelector(".tcall__h")?.getAttribute("role"),
      headerTabIndex: card.querySelector(".tcall__h")?.tabIndex,
    })),
  };
  await header.click();
  report.design.tool.afterPointerOpen = await tool.evaluate((card) => ({
    bodyVisible: Boolean(card.querySelector(".tcall__body")),
    args: card
      .querySelector(".tcall__body .kv:nth-child(1)")
      ?.textContent?.replace(/\s+/g, " ")
      .trim(),
    result: card
      .querySelector(".tcall__body .kv:nth-child(2)")
      ?.textContent?.replace(/\s+/g, " ")
      .trim(),
    source: card
      .querySelector(".tcall__body .kv:nth-child(3)")
      ?.textContent?.replace(/\s+/g, " ")
      .trim(),
    spawnedTreeVisible: (card.textContent ?? "").includes("spawned"),
    cardStyle: globalThis.__interactionStyle(card),
    bodyStyle: globalThis.__interactionStyle(
      card.querySelector(".tcall__body"),
    ),
  }));
  await header.click();
  await header.focus();
  await design.keyboard.press("Enter");
  const bodyAfterEnter = await body.count();
  await design.keyboard.press("Space");
  report.design.tool.keyboard = {
    bodyVisibleAfterEnter: bodyAfterEnter > 0,
    bodyVisibleAfterSpace: (await body.count()) > 0,
  };
  const agent = design.locator(
    '[data-screen-label="Activity panel"] .side-body > div:first-child > div:nth-child(2) > div:nth-child(2)',
  );
  const beforeAgent = await agent.evaluate((node) => node.outerHTML);
  await agent.click();
  report.design.agent = {
    clickChangesDom:
      beforeAgent !== (await agent.evaluate((node) => node.outerHTML)),
    role: await agent.getAttribute("role"),
    tabIndex: await agent.evaluate((node) => node.tabIndex),
    cardStyle: await agent.evaluate((node) =>
      globalThis.__interactionStyle(node),
    ),
  };
  await design.close();

  const live = await browser.newPage({
    viewport: { width: 1260, height: 900 },
  });
  await live.goto(`${baseUrl}/surfaces/chat-tool-calls/live/web-search.html`, {
    waitUntil: "load",
  });
  await live.addScriptTag({ content: styleSampler });
  const liveTool = live.locator(
    '#tool [data-testid="tc-chat-tool-web-search"] > details',
  );
  const liveHeader = liveTool.locator(".tc-tool-card__summary");
  report.live.tool = {
    initial: {
      detailsOpen: await liveTool.evaluate((node) => node.open),
      argsVisible: await liveTool.locator('[data-testid$="-args"]').isVisible(),
      resultVisible: await liveTool
        .locator('[data-testid$="-result"]')
        .isVisible(),
    },
  };
  await liveHeader.click();
  report.live.tool.afterHeaderPointer = await liveTool.evaluate((card) => ({
    detailsOpen: card.open,
    fieldLabels: Array.from(
      card.querySelectorAll(
        '[data-testid$="-details"] > div > span:first-child',
      ),
    ).map((label) => label.textContent),
    argsText: card.querySelector('[data-testid$="-args"]')?.textContent,
    resultText: card.querySelector('[data-testid$="-result"]')?.textContent,
    sourceVisible: Array.from(
      card.querySelectorAll(
        '[data-testid$="-details"] > div > span:first-child',
      ),
    ).some((label) => label.textContent === "source"),
    provenanceVisible: /MCP · web\.search/.test(card.textContent ?? ""),
    accessVisible: /read/.test(card.textContent ?? ""),
    durationVisible: /1\.2s/.test(card.textContent ?? ""),
    delegatedTaskIds: Array.from(
      card.querySelectorAll("[data-subagent-task-id]"),
    ).map((link) => link.getAttribute("data-subagent-task-id")),
    cardStyle: globalThis.__interactionStyle(card),
    bodyStyle: globalThis.__interactionStyle(
      card.querySelector('[data-testid$="-details"]'),
    ),
    preStyle: globalThis.__interactionStyle(card.querySelector("pre")),
  }));
  await liveHeader.click();
  await liveHeader.focus();
  await live.keyboard.press("Enter");
  const openAfterEnter = await liveTool.evaluate((node) => node.open);
  await live.keyboard.press("Space");
  report.live.tool.keyboard = {
    headerTabIndex: await liveHeader.evaluate((node) => node.tabIndex),
    openAfterEnter,
    openAfterSpace: await liveTool.evaluate((node) => node.open),
  };
  const liveAgent = live.locator(
    "#agents .atlas-workspace-tab__item--child .agent-activity-row",
  );
  const agentHeader = liveAgent.locator(".agent-activity-row__content");
  const agentDetails = liveAgent.locator(
    '[data-testid^="agent-activity-row-details-"]',
  );
  const agentSummary = agentDetails.locator("summary");
  report.live.agent = {
    initial: {
      detailsOpen: await agentDetails.evaluate((node) => node.open),
      timelineVisible: await liveAgent
        .locator(".aui-tool-card__timeline-item")
        .first()
        .isVisible(),
    },
  };
  await agentHeader.click();
  report.live.agent.afterHeaderPointer = {
    detailsOpen: await agentDetails.evaluate((node) => node.open),
  };
  await agentSummary.click();
  report.live.agent.afterSummaryPointer = await liveAgent.evaluate((card) => ({
    detailsOpen: card.querySelector("details")?.open,
    timelineRows: card.querySelectorAll(".aui-tool-card__timeline-item").length,
    timelineText: card
      .querySelector(".aui-tool-card__timeline")
      ?.textContent?.replace(/\s+/g, " ")
      .trim(),
    cardStyle: globalThis.__interactionStyle(card),
    activityStyle: globalThis.__interactionStyle(
      card.querySelector(".agent-activity-row__activity"),
    ),
  }));
  await agentSummary.click();
  await agentSummary.focus();
  await live.keyboard.press("Enter");
  const agentOpenAfterEnter = await agentDetails.evaluate((node) => node.open);
  await live.keyboard.press("Space");
  report.live.agent.keyboard = {
    summaryTabIndex: await agentSummary.evaluate((node) => node.tabIndex),
    openAfterEnter: agentOpenAfterEnter,
    openAfterSpace: await agentDetails.evaluate((node) => node.open),
  };
  await live.close();
} finally {
  await browser.close();
}

const serialized = `${JSON.stringify(report, null, 2)}\n`;
if (out) writeFileSync(out, serialized);
process.stdout.write(serialized);
