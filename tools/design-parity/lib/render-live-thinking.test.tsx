/* design-parity · live THINKING / TRANSCRIPT TYPE-SCALE render (vitest + jsdom)
 *
 * Renders the REAL `TcChat` in Focus mode over a transcript shaped like the one
 * a user actually sees: user turns, an assistant turn whose reasoning span
 * brackets two tool calls, and a settled answer. It exists to measure the
 * transcript's TYPE SCALE — user bubble vs answer vs thinking chrome — and the
 * spacing between a thought and the answer under it, which is a layout question
 * no unit test can answer (jsdom runs no layout, so the numbers come from
 * `extract-computed.js` in a real browser against this file's output).
 *
 * Three states are emitted from one fixture, because the row is a different
 * component in each and a scale that is right in one and wrong in another is
 * exactly the bug this harness is here to catch:
 *
 * - `settled` — the run finished; the header is the plain elapsed label;
 * - `running` — the span is still streaming; the header is `ThinkingShimmer`,
 *   and the wrapper carries `data-run-status="streaming"`, which is the
 *   selector context the deleted `:has(.reasoning-markdown)` override matched;
 * - `failed`  — a folded step errored, which must never be hidden by the
 *   collapsed row that now holds it.
 */
import { createElement as h } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { copyFileSync, mkdirSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { it } from "vitest";

import type {
  Session,
  SseSubscribeOptions,
  SseSubscription,
  Transport,
  TransportCapabilities,
  TypedRequest,
} from "@0x-copilot/chat-transport";
import {
  TcChat,
  TransportProvider,
  type ToolCallEntry,
} from "@0x-copilot/chat-surface";
// Relative, like render-live-login.test.tsx does for app components: this is
// the cockpit's `!important` scope layer, which is NOT on the package barrel
// (hosts get it by mounting RunDestination). Measuring the transcript without
// it would measure a surface the product never draws — that layer is exactly
// where a stale rule was found re-typesetting reasoning mid-stream.
import { RunCockpitScopeStyles } from "../../../packages/chat-surface/src/destinations/run/RunDestination";

const HERE = (p: string): string => fileURLToPath(new URL(p, import.meta.url));
const REPO = (p: string): string => HERE("../../../" + p);
const LIVE = (p: string): string => HERE("../surfaces/thinking/live/" + p);

const RUN = "run_thinking";

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

/* Two calls the model made WHILE it was thinking — seq 2 and 3 sit between the
   reasoning part (seq 1) and the answer (seq 4). That bracket is the whole
   point: it is what decides whether a tool card belongs inside the thought or
   after it. */
const listDir: ToolCallEntry = {
  id: "call_ls",
  toolName: "list_directory",
  title: "List directory",
  status: "complete",
  sequenceNo: 2,
  createdAtMs: 2,
  runId: RUN,
  summary: "12 entries · 2 csv",
  args: { path: "kscope-benchmarks" },
  durationMs: 140,
};

const readCsv = (
  status: ToolCallEntry["status"],
  errorMessage?: string,
): ToolCallEntry => ({
  id: "call_read",
  toolName: "read_file",
  title: "Read file",
  status,
  sequenceNo: 3,
  createdAtMs: 3,
  runId: RUN,
  summary: status === "error" ? "" : "20 rows · 8 columns",
  args: { path: "kscope-benchmarks/random_data.csv" },
  ...(errorMessage === undefined ? {} : { errorMessage }),
  ...(status === "running" ? {} : { durationMs: 90 }),
});

const REASONING = `The user is asking whether the folder holds any CSVs, so the
first move is a directory listing rather than a guess. Two candidates come back;
reading the smaller one confirms the header row before I describe the columns.`;

const ANSWER = `Yes, there are two CSV files in \`kscope-benchmarks\`:

- \`claude-sonnet-5/runs.csv\`
- \`random_data.csv\`

\`random_data.csv\` is a 20-row employee dataset with columns \`id\`, \`name\`,
\`age\`, \`city\`, \`department\`, \`salary\`, \`join_date\`, and \`active\`.`;

function messages(running: boolean) {
  return [
    {
      message_id: "u1",
      role: "user" as const,
      parts: [
        { type: "text" as const, text: "are there any csv in the folder" },
      ],
      created_at_ms: 0,
      run_id: RUN,
    },
    {
      message_id: "a1",
      role: "assistant" as const,
      run_id: RUN,
      created_at_ms: 1,
      parts: running
        ? [
            {
              type: "reasoning" as const,
              text: REASONING,
              seq: 1,
              startedAtMs: 1_000,
              updatedAtMs: 7_000,
              status: { type: "running" as const },
            },
          ]
        : [
            {
              type: "reasoning" as const,
              text: REASONING,
              seq: 1,
              startedAtMs: 1_000,
              updatedAtMs: 7_000,
              status: { type: "complete" as const },
            },
            {
              type: "text" as const,
              text: ANSWER,
              seq: 4,
              status: { type: "complete" as const },
            },
          ],
    },
  ];
}

function shell(inner: string): string {
  return `<!doctype html>
<html lang="en" data-theme="dark">
  <head>
    <meta charset="utf-8" />
    <title>design-parity · Thinking &amp; transcript scale · LIVE</title>
    <link rel="stylesheet" href="./styles.css" />
    <link rel="stylesheet" href="./markdown.css" />
    <link rel="stylesheet" href="./workspace.css" />
    <link rel="stylesheet" href="./surface-language.css" />
    <link rel="stylesheet" href="./review-surfaces.css" />
    <style>
      html, body { margin: 0; min-height: 100%; background: var(--color-bg); }
      #frame {
        box-sizing: border-box; width: 1080px; min-height: 640px; padding: 20px 28px;
        background: var(--color-bg); color: var(--color-text);
        font-family: var(--font-sans);
      }
      #frame > [data-testid="tc-chat"] { height: auto; }
    </style>
  </head>
  <body><div id="frame">${inner}</div></body>
</html>`;
}

type State = "settled" | "running" | "failed";

function render(state: State): string {
  const second =
    state === "running"
      ? readCsv("running")
      : state === "failed"
        ? readCsv("error", "ENOENT: no such file or directory")
        : readCsv("complete");
  return renderToStaticMarkup(
    h(
      TransportProvider,
      { transport: fakeTransport },
      // The real cockpit's wrapper + scope CSS. `data-run-status="streaming"`
      // on the running state is what used to trigger the deleted
      // `:has(.reasoning-markdown)` override, so this harness now renders the
      // exact selector context that rule matched.
      h(
        "div",
        {
          className: "run-destination",
          "data-mode": "focus",
          "data-run-status": state === "running" ? "streaming" : "idle",
        },
        h(RunCockpitScopeStyles),
        h(TcChat, {
          conversationId: "conv_thinking",
          mode: "focus",
          activeRunId: RUN,
          messages: messages(state === "running"),
          toolCalls: [listDir, second],
          renderComposer: () => h("div", { "data-parity-composer": "true" }),
        }),
      ),
    ),
  );
}

it("renders the thinking block, its tool calls and the answer under it", () => {
  mkdirSync(LIVE(""), { recursive: true });
  for (const [source, destination] of [
    ["packages/design-system/src/styles.css", "styles.css"],
    ["packages/chat-surface/src/messages/markdown.css", "markdown.css"],
    ["packages/chat-surface/src/workspace/workspace.css", "workspace.css"],
    [
      "packages/chat-surface/src/thread-canvas/surface-language.css",
      "surface-language.css",
    ],
    [
      "packages/chat-surface/src/thread-canvas/review-surfaces.css",
      "review-surfaces.css",
    ],
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

  writeFileSync(LIVE("settled.html"), shell(render("settled")));
  writeFileSync(LIVE("running.html"), shell(render("running")));
  writeFileSync(LIVE("failed.html"), shell(render("failed")));
});
