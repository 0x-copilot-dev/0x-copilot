/* design-parity · live CHAT & TOOL CALLS render (vitest + jsdom)
 *
 * The local Claude Design source is an interactive walkthrough. This harness
 * renders its comparable completed-web-search state using the actual shared
 * chat-surface components: TcChat's inline tool card and the Run cockpit's
 * AgentsTab. It is intentionally a focused measurement fixture, not a mock
 * reimplementation of either component.
 */
import { createElement as h } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { copyFileSync, mkdirSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { it } from "vitest";

import type { SubagentEntry } from "@0x-copilot/api-types";
import type {
  Session,
  SseSubscribeOptions,
  SseSubscription,
  Transport,
  TransportCapabilities,
  TypedRequest,
} from "@0x-copilot/chat-transport";
import {
  AgentsTab,
  TcChat,
  TransportProvider,
  type SubagentActivityRecord,
  type ToolCallEntry,
} from "@0x-copilot/chat-surface";

const HERE = (p: string): string => fileURLToPath(new URL(p, import.meta.url));
const REPO = (p: string): string => HERE("../../../" + p);
const LIVE = (p: string): string =>
  HERE("../surfaces/chat-tool-calls/live/" + p);

const fakeTransport: Transport = {
  request: <TRes,>(_request: TypedRequest): Promise<TRes> =>
    Promise.resolve({} as TRes),
  subscribeServerSentEvents: (_opts: SseSubscribeOptions): SseSubscription => ({
    close: () => undefined,
  }),
  getSession: (): Session => ({ bearer: null }),
  capabilities: (): TransportCapabilities => ({
    substrate: "web",
    nativeSecretStorage: false,
    fileSystemAccess: false,
    clipboardWrite: false,
    openExternal: false,
  }),
};

const webSearch: ToolCallEntry = {
  id: "web-search",
  toolName: "web_search",
  title: "Search the web",
  status: "complete",
  sequenceNo: 3,
  createdAtMs: 1,
  summary: "3 relevant sources · synthesized below",
  args: { q: "payments gateway incident postmortem Jan 28" },
  result: { pages_fetched: 3, pages_kept: 3 },
  // Values mirror only metadata an honest runtime event can provide. The
  // fixture deliberately avoids inferring origin/authority from `toolName`.
  provenance: { source: "mcp", serverName: "web.search" },
  accessMode: "read",
  durationMs: 1200,
  subagentTaskIds: ["task_research_incident"],
};

const researchSubagent: SubagentEntry & {
  readonly parent_agent_role: string;
  readonly parent_agent_name: string | null;
  readonly model_display_label: string;
  readonly current_activity: string;
} = {
  task_id: "task_research_incident",
  parent_run_id: "run_parity",
  subagent_name: "research",
  status: "running",
  display_title: "Research · incident postmortem",
  // The design's live-status copy is deliberately supplied here. The card
  // still derives this row from the real SubagentEntry projection.
  objective_summary: "web.fetch × 3 · ranking pages",
  started_at: "2026-07-24T12:00:00.000Z",
  completed_at: null,
  duration_ms: null,
  result_summary: null,
  safe_error_code: null,
  safe_error_message: null,
  token_usage: null,
  parent_agent_role: "supervisor",
  parent_agent_name: null,
  model_display_label: "Haiku 4.5",
  current_activity: "web.fetch × 3 · ranking pages",
};

// `supervisor` is the runtime's role alias, not an independently emitted
// subagent task. Keep this factual role-only hint in the fixture so the parity
// harness catches a regression that would invent a misleading parent row (or
// indent this real task beneath one). A concrete `parent_task_id` is required
// before Focus represents a hierarchy.

const researchActivities: ReadonlyMap<
  string,
  readonly SubagentActivityRecord[]
> = new Map([
  [
    researchSubagent.task_id,
    [
      {
        id: "search-1",
        kind: "tool",
        title: "web_search",
        status: "completed",
        summary: "6 hits",
        inputSummary: null,
        result: null,
        isError: false,
      },
      {
        id: "fetch-1",
        kind: "tool",
        title: "web_fetch",
        status: "running",
        summary: "Fetching postmortem and status page",
        inputSummary: null,
        result: null,
        isError: false,
      },
    ],
  ],
]);

function shell(inner: string): string {
  return `<!doctype html>
<html lang="en" data-theme="dark">
  <head>
    <meta charset="utf-8" />
    <title>design-parity · Chat & Tool Calls · LIVE</title>
    <link rel="stylesheet" href="./styles.css" />
    <link rel="stylesheet" href="./workspace.css" />
    <link rel="stylesheet" href="./subagents.css" />
    <style>
      html, body { margin: 0; min-height: 100%; background: var(--color-bg); }
      #frame {
        box-sizing: border-box; display: grid; grid-template-columns: minmax(0, 1fr) 340px;
        width: 1200px; height: 816px; overflow: hidden; background: var(--color-bg);
        color: var(--color-text); font-family: var(--font-sans);
      }
      #tool { min-width: 0; padding: 16px; border-right: 1px solid var(--color-border); }
      #tool > [data-testid="tc-chat"] { height: 100%; }
      #agents { min-width: 0; display: flex; flex-direction: column; background: var(--color-bg-elevated); }
      .parity-agents-header { align-items: center; border-bottom: 1px solid var(--color-border); color: var(--color-text); display: flex; font-size: 13px; font-weight: 600; min-height: 48px; padding: 0 14px; }
      #agents [data-testid="workspace-agents-tab"] { flex: 1; }
    </style>
  </head>
  <body><div id="frame">${inner}</div></body>
</html>`;
}

it("renders the live completed web-search tool and active subagent", () => {
  mkdirSync(LIVE(""), { recursive: true });
  for (const [source, destination] of [
    ["packages/design-system/src/styles.css", "styles.css"],
    ["packages/chat-surface/src/workspace/workspace.css", "workspace.css"],
    ["packages/chat-surface/src/subagents/subagents.css", "subagents.css"],
  ] as const) {
    copyFileSync(REPO(source), LIVE(destination));
  }
  mkdirSync(LIVE("fonts"), { recursive: true });
  for (const font of [
    "jetbrains-mono-latin.woff2",
    "jetbrains-mono-latin-ext.woff2",
  ]) {
    copyFileSync(
      REPO(`packages/design-system/src/fonts/${font}`),
      LIVE(`fonts/${font}`),
    );
  }

  const tool = renderToStaticMarkup(
    h(
      TransportProvider,
      { transport: fakeTransport },
      h(TcChat, {
        conversationId: "conv_parity",
        mode: "focus",
        messages: [
          {
            message_id: "prompt",
            role: "user",
            parts: [{ type: "text", text: "Check the incident postmortem." }],
            created_at_ms: 0,
          },
        ],
        toolCalls: [webSearch],
        renderComposer: () => h("div", { "data-parity-composer": "true" }),
      }),
    ),
  );
  const agents = renderToStaticMarkup(
    h(AgentsTab, {
      subagents: new Map([[researchSubagent.task_id, researchSubagent]]),
      activitiesByTask: researchActivities,
    }),
  );

  writeFileSync(
    LIVE("web-search.html"),
    shell(
      `<main id="tool" data-parity="tool">${tool}</main>` +
        `<aside id="agents" data-parity="agents"><div class="parity-agents-header">Agents</div>${agents}</aside>`,
    ),
  );
});
