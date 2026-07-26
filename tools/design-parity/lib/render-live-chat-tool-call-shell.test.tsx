/* design-parity · live CHAT / TOOL-CALL SHELL render
 * ===========================================================================
 * This is deliberately a REAL RunDestination + ThreadCanvas render.  It does
 * not recreate the design mock with bespoke HTML: the deterministic transport
 * feeds ordinary persisted messages and SSE envelopes into the cockpit, then
 * serialises the mounted DOM for Playwright's computed-style extractor.
 *
 * One fixture exists for every walkthrough state in the supplied Design
 * Compiler file.  A fixture is a useful contract in its own right: it proves
 * the state is reachable from shipping projections, before its styles are ever
 * compared with the design baseline.
 * =========================================================================== */
import { createElement as h } from "react";
import { copyFileSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it } from "vitest";

import type {
  ConversationId,
  RuntimeEventEnvelope,
} from "@0x-copilot/api-types";
import type {
  Session,
  SseSubscribeOptions,
  SseSubscription,
  Transport,
  TransportCapabilities,
  TypedRequest,
} from "@0x-copilot/chat-transport";
import {
  KeyValueStoreProvider,
  TransportProvider,
  type KeyValueStore,
  type WorkspaceStageHost,
} from "@0x-copilot/chat-surface";

import { DesktopWindowFrame } from "../../../apps/desktop/renderer/DesktopWindowFrame";
import { DestinationOutlet } from "../../../apps/desktop/renderer/DestinationOutlet";
import { runModeKey } from "../../../packages/chat-surface/src/destinations/run/useRunMode";

const HERE = (p: string): string => fileURLToPath(new URL(p, import.meta.url));
const REPO = (p: string): string => HERE("../../../" + p);
const LIVE = (p: string): string =>
  HERE("../surfaces/chat-tool-call-shell/live/" + p);

const CONVERSATION_ID = "conv_parity_chat_tool_shell" as ConversationId;
const RUN_ID = "run_parity_chat_tool_shell";
const STAGE_ID = "stage_parity_chat_tool_shell";
const PROPOSAL_DIGEST = "a".repeat(64);
const TARGET_DIGEST = "b".repeat(64);

export const CHAT_TOOL_CALL_SHELL_STATES = [
  "focus-thinking",
  "studio-third-party-read",
  "studio-web-chat-only",
  "studio-csv-chat-only",
  "studio-write-held",
  "studio-wrap-file",
] as const;
export type ChatToolCallShellState =
  (typeof CHAT_TOOL_CALL_SHELL_STATES)[number];

const CAPABILITIES: TransportCapabilities = {
  substrate: "web",
  nativeSecretStorage: false,
  fileSystemAccess: false,
  clipboardWrite: false,
  openExternal: false,
};

interface CapturedSubscription {
  readonly path: string;
  readonly eventName?: string;
  readonly onMessage?: (raw: string) => void;
  closed: boolean;
}

/** A strict-enough fake facade transport: every state reaches the same normal
 * cockpit request/SSE path the web and desktop hosts use. */
class ShellTransport implements Transport {
  readonly requests: TypedRequest[] = [];
  readonly subscriptions: CapturedSubscription[] = [];

  async request<TRes>(request: TypedRequest): Promise<TRes> {
    this.requests.push(request);
    if (request.path.endsWith("/messages")) {
      return {
        messages: [
          {
            message_id: "message-parity-user",
            role: "user",
            content_text:
              "Catch me up on ENG-142, then prepare the requested file.",
            created_at: "2026-02-09T09:00:00.000Z",
          },
        ],
      } as TRes;
    }
    if (request.path.endsWith("/surfaces")) return { surfaces: [] } as TRes;
    if (request.path === "/v1/settings/provider-keys") {
      return { keys: [{ provider: "anthropic" }] } as TRes;
    }
    if (request.path.includes("/pending-work")) {
      return { cards: [], agents: [] } as TRes;
    }
    if (request.path.endsWith(`/conversations/${CONVERSATION_ID}/runs`)) {
      return {
        runs: [
          {
            run_id: RUN_ID,
            status: "running",
            model_name: "Claude Sonnet 4.5",
            created_at: "2026-02-09T09:00:00.000Z",
          },
        ],
      } as TRes;
    }
    if (request.path.endsWith(`/conversations/${CONVERSATION_ID}`)) {
      return {
        latest_run_id: RUN_ID,
        latest_run_id_any_status: RUN_ID,
      } as TRes;
    }
    // State fixtures never activate an action, but keep callback paths honest
    // if a component schedules a best-effort fetch during mount.
    return {} as TRes;
  }

  subscribeServerSentEvents(options: SseSubscribeOptions): SseSubscription {
    const subscription: CapturedSubscription = {
      path: options.path,
      eventName: options.eventName,
      onMessage: options.onMessage,
      closed: false,
    };
    this.subscriptions.push(subscription);
    return { close: () => (subscription.closed = true) };
  }

  getSession(): Session {
    return { bearer: null };
  }

  capabilities(): TransportCapabilities {
    return CAPABILITIES;
  }

  get sessionSubscription(): CapturedSubscription | undefined {
    return [...this.subscriptions]
      .reverse()
      .find(
        (subscription) =>
          !subscription.closed && subscription.eventName === "runtime_event",
      );
  }
}

function makeStore(mode: "focus" | "studio"): KeyValueStore {
  const values = new Map<string, string>();
  values.set(runModeKey(CONVERSATION_ID), mode);
  return {
    get: (key) => values.get(key) ?? null,
    set: (key, value) => {
      if (value === null) values.delete(key);
      else values.set(key, value);
    },
    keys: (prefix) =>
      [...values.keys()].filter(
        (key) => prefix === undefined || key.startsWith(prefix),
      ),
  };
}

function runtimeEvent(
  state: ChatToolCallShellState,
  sequenceNo: number,
  eventType: string,
  payload: Record<string, unknown>,
  overrides: Partial<RuntimeEventEnvelope> = {},
): RuntimeEventEnvelope {
  return {
    event_id: `parity-${state}-${sequenceNo}`,
    run_id: RUN_ID,
    conversation_id: CONVERSATION_ID,
    sequence_no: sequenceNo,
    event_type: eventType,
    activity_kind: "tool",
    payload,
    created_at: new Date(Date.UTC(2026, 1, 9, 9, 0, sequenceNo)).toISOString(),
    ...overrides,
  } as RuntimeEventEnvelope;
}

function toolEvents(
  state: ChatToolCallShellState,
  sequenceNo: number,
  input: {
    readonly id: string;
    readonly name: string;
    readonly title: string;
    readonly summary: string;
    readonly args: Record<string, unknown>;
    readonly output: Record<string, unknown>;
    readonly server: string;
  },
): readonly RuntimeEventEnvelope[] {
  const common = {
    call_id: input.id,
    tool_name: input.name,
    provenance: { source: "mcp", server_name: input.server },
    access_mode: "read",
    duration_ms: 820,
  };
  return [
    runtimeEvent(
      state,
      sequenceNo,
      "tool_call_started",
      {
        ...common,
        args: input.args,
        summary: "Reading…",
      },
      {
        display_title: input.title,
        summary: "Reading…",
        status: "running",
      },
    ),
    runtimeEvent(
      state,
      sequenceNo + 1,
      "tool_result",
      {
        ...common,
        output: input.output,
        status: "completed",
        summary: input.summary,
      },
      {
        display_title: input.title,
        summary: input.summary,
        status: "completed",
      },
    ),
  ];
}

function workspaceStage(
  state: ChatToolCallShellState,
  sequenceNo: number,
  applied: boolean,
): readonly RuntimeEventEnvelope[] {
  const staged = runtimeEvent(
    state,
    sequenceNo,
    "effect.staged",
    {
      v: 1,
      stage_id: STAGE_ID,
      operation_id: "operation_parity_chat_tool_shell",
      executor: "workspace",
      target_ref: "target://private-workspace-root",
      target_digest: TARGET_DIGEST,
      proposal_ref: "proposal://private-workspace-change-set",
      proposal_digest: PROPOSAL_DIGEST,
      policy: "ask",
      op: "create_file",
      display_target: "/workspace/standup-2026-02-09.md",
      author_actor: "user",
    },
    {
      activity_kind: "event",
      display_title: "Create workspace file",
      summary: "Awaiting your approval",
      status: "waiting",
    },
  );
  if (!applied) return [staged];
  return [
    staged,
    runtimeEvent(
      state,
      sequenceNo + 1,
      "effect.applied",
      {
        v: 1,
        stage_id: STAGE_ID,
        revision: 1,
        outcome: "applied",
      },
      {
        activity_kind: "event",
        display_title: "Workspace file created",
        summary: "standup-2026-02-09.md",
        status: "completed",
      },
    ),
  ];
}

function fixtureEvents(
  state: ChatToolCallShellState,
): readonly RuntimeEventEnvelope[] {
  const started = runtimeEvent(
    state,
    1,
    "run_started",
    {},
    {
      activity_kind: "run",
      display_title: "Monday catch-up started",
      status: "running",
    },
  );
  switch (state) {
    case "focus-thinking":
      return [
        started,
        runtimeEvent(
          state,
          2,
          "reasoning_summary_delta",
          {
            delta: "Reading the issue history and planning the next step…",
          },
          { activity_kind: "reasoning", display_title: "Thinking" },
        ),
        runtimeEvent(
          state,
          3,
          "model_delta",
          {
            delta: "I’m checking the current issue context now.",
          },
          { activity_kind: "message", display_title: "0xCopilot" },
        ),
      ];
    case "studio-third-party-read":
      return [
        started,
        ...toolEvents(state, 2, {
          id: "linear-get",
          name: "linear.issues.get",
          title: "Read ENG-142",
          summary: "ENG-142 · reconnect regression",
          args: { identifier: "ENG-142" },
          output: { identifier: "ENG-142", state: "In progress" },
          server: "Linear",
        }),
        runtimeEvent(
          state,
          4,
          "surface.created",
          {
            v: 1,
            surface_id: "surface-linear-eng-142",
            kind: "record",
            source: { connector: "linear", op: "issues.get" },
            title: "ENG-142",
            payload_ref: "call:linear-get",
          },
          { activity_kind: "event", display_title: "Issue surface ready" },
        ),
      ];
    case "studio-web-chat-only":
      return [
        started,
        ...toolEvents(state, 2, {
          id: "web-search",
          name: "web.search",
          title: "Search the web",
          summary: "3 sources synthesized in chat",
          args: { query: "ENG-142 reconnect regression" },
          output: { source_count: 3 },
          server: "Web",
        }),
        runtimeEvent(
          state,
          4,
          "sources_ingested",
          {
            citations: [
              {
                citation_id: "web-source-1",
                source_connector: "web",
                source_doc_id: "status-0128",
                source_url: "https://status.northbeam.co/incidents/0128",
                title: "Status page — Checkout latency incident (resolved)",
                snippet: null,
                freshness_at: "2026-01-28T18:00:00.000Z",
                source_tool_call_id: "web-search",
                ordinal: 1,
              },
              {
                citation_id: "web-source-2",
                source_connector: "web",
                source_doc_id: "postmortem-0128",
                source_url: "https://notion.so/eng/pm-0128",
                title: "Postmortem: token refresh & retry drops",
                snippet: null,
                freshness_at: "2026-01-29T10:00:00.000Z",
                source_tool_call_id: "web-search",
                ordinal: 2,
              },
              {
                citation_id: "web-source-3",
                source_connector: "web",
                source_doc_id: "pr-482",
                source_url: "https://github.com/northbeam/checkout/pull/482",
                title: "PR #482 — retry idempotency",
                snippet: null,
                freshness_at: "2026-01-30T14:00:00.000Z",
                source_tool_call_id: "web-search",
                ordinal: 3,
              },
            ],
          },
          { activity_kind: "event", display_title: "Sources ready" },
        ),
        runtimeEvent(
          state,
          5,
          "final_response",
          {
            text: "I found three relevant sources and summarized them here.",
          },
          { activity_kind: "message", display_title: "0xCopilot" },
        ),
      ];
    case "studio-csv-chat-only":
      return [
        started,
        ...toolEvents(state, 2, {
          id: "csv-read",
          name: "fs.read",
          title: "Read forecast_q1.csv",
          summary: "742 rows · 9 columns",
          args: { path: "/workspace/forecast_q1.csv" },
          output: {
            rows: 742,
            columns: 9,
            size_bytes: 62_464,
            metrics: [
              { label: "Rows", value: 742 },
              { label: "Pipeline", value: "$4.1M" },
              { label: "WTD close", value: "$1.6M" },
              { label: "At risk", value: 18 },
              { label: "Median age", value: "34d" },
              { label: "Top region", value: "EMEA" },
            ],
            preview_rows: [
              { region: "EMEA", stage: "At risk", amount: "$410K" },
              { region: "NA", stage: "Commit", amount: "$285K" },
            ],
          },
          server: "Workspace",
        }),
        runtimeEvent(
          state,
          4,
          "final_response",
          {
            text: "The forecast CSV is analyzed in chat; no canvas surface was created.",
          },
          { activity_kind: "message", display_title: "0xCopilot" },
        ),
      ];
    case "studio-write-held":
      return [
        started,
        ...toolEvents(state, 2, {
          id: "file-write",
          name: "fs.write",
          title: "Prepare standup file",
          summary: "Change staged for approval",
          args: { path: "/workspace/standup-2026-02-09.md" },
          output: { staged: true },
          server: "Workspace",
        }),
        ...workspaceStage(state, 4, false),
      ];
    case "studio-wrap-file":
      return [
        started,
        ...toolEvents(state, 2, {
          id: "file-wrap",
          name: "fs.write",
          title: "Create standup file",
          summary: "Workspace file created",
          args: { path: "/workspace/standup-2026-02-09.md" },
          output: { written: true },
          server: "Workspace",
        }),
        ...workspaceStage(state, 4, true),
        runtimeEvent(
          state,
          6,
          "run_completed",
          { status: "completed" },
          {
            activity_kind: "run",
            display_title: "Monday catch-up complete",
            status: "completed",
          },
        ),
      ];
  }
}

const workspaceStageHost: WorkspaceStageHost = { kind: "web" };

function shell(state: ChatToolCallShellState, inner: string): string {
  return `<!doctype html>
<html lang="en" data-theme="dark" data-density="comfortable">
  <head>
    <meta charset="utf-8" />
    <title>design-parity · chat/tool-call shell · ${state} · LIVE</title>
    <link rel="stylesheet" href="./styles.css" />
    <link rel="stylesheet" href="./composer.css" />
    <link rel="stylesheet" href="./workspace.css" />
    <link rel="stylesheet" href="./markdown.css" />
    <link rel="stylesheet" href="./subagents.css" />
    <link rel="stylesheet" href="./desktop.css" />
    <style>
      html, body { margin: 0; min-height: 100%; }
      *, *::before, *::after { animation: none !important; transition: none !important; }
    </style>
  </head>
  <body><div id="root" data-parity-live-state="${state}">${inner}</div></body>
</html>`;
}

function expectedLiveAnchor(state: ChatToolCallShellState): string {
  switch (state) {
    case "focus-thinking":
      return "tc-focus-panel";
    case "studio-third-party-read":
      return "tc-chat-tool-linear-get";
    case "studio-web-chat-only":
      return "tc-chat-tool-web-search";
    case "studio-csv-chat-only":
      return "tc-chat-tool-csv-read";
    case "studio-write-held":
    case "studio-wrap-file":
      return "tc-workspace-stage";
  }
}

describe("live chat/tool-call shell — RunDestination fixture", () => {
  beforeAll(() => {
    mkdirSync(LIVE(""), { recursive: true });
    copyFileSync(
      REPO("packages/design-system/src/styles.css"),
      LIVE("styles.css"),
    );
    copyFileSync(
      REPO("apps/desktop/renderer/desktop.css"),
      LIVE("desktop.css"),
    );
    for (const [source, destination] of [
      ["packages/chat-surface/src/composer/composer.css", "composer.css"],
      ["packages/chat-surface/src/workspace/workspace.css", "workspace.css"],
      ["packages/chat-surface/src/messages/markdown.css", "markdown.css"],
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
  });

  afterEach(() => cleanup());

  it("keeps six strict state-specific maps with no waived visual drift", () => {
    for (const state of CHAT_TOOL_CALL_SHELL_STATES) {
      const anchors = JSON.parse(
        readFileSync(
          REPO(
            `tools/design-parity/surfaces/chat-tool-call-shell/anchors/${state}.json`,
          ),
          "utf8",
        ),
      ) as {
        state: string;
        strict: boolean;
        elements: ReadonlyArray<{ label: string; expectDivergence?: unknown }>;
      };
      expect(anchors.state).toBe(state);
      expect(anchors.strict).toBe(true);
      expect(anchors.elements.map((entry) => entry.label)).toContain(
        "shell.frame",
      );
      expect(
        anchors.elements.some((entry) => entry.expectDivergence !== undefined),
      ).toBe(false);
    }
  });

  for (const state of CHAT_TOOL_CALL_SHELL_STATES) {
    it(`${state} — serializes the desktop host's real RunDestination/ThreadCanvas state`, async () => {
      const transport = new ShellTransport();
      const mode = state === "focus-thinking" ? "focus" : "studio";
      const rendered = render(
        h(
          TransportProvider,
          { transport },
          h(
            KeyValueStoreProvider,
            { store: makeStore(mode) },
            h(
              DesktopWindowFrame,
              { id: "parity-frame" },
              h(DestinationOutlet, {
                destination: "run",
                conversationId: CONVERSATION_ID,
                workspaceStageHost,
              }),
            ),
          ),
        ),
      );

      await screen.findByTestId("thread-canvas");
      await waitFor(() => expect(transport.sessionSubscription).toBeDefined());
      act(() => {
        for (const event of fixtureEvents(state)) {
          transport.sessionSubscription?.onMessage?.(JSON.stringify(event));
        }
      });
      await screen.findByTestId(expectedLiveAnchor(state));

      // Required shell anchors prove this is the actual cockpit composition,
      // routed through the shipping desktop host rather than a bespoke fixture.
      expect(screen.getByTestId("desktop-window-frame")).not.toBeNull();
      expect(screen.getByTestId("destination-outlet")).not.toBeNull();
      expect(screen.getByTestId("run-header")).not.toBeNull();
      expect(screen.getByTestId("run-mode-switcher")).not.toBeNull();
      expect(screen.getByTestId("tc-chat")).not.toBeNull();
      expect(screen.getByTestId("composer-textarea")).not.toBeNull();

      writeFileSync(
        LIVE(`${state}.html`),
        shell(state, rendered.container.innerHTML),
      );
      const liveHtml = readFileSync(LIVE(`${state}.html`), "utf8");
      expect(liveHtml).toContain('data-testid="desktop-window-frame"');
      expect(liveHtml).toContain('id="parity-frame"');
      expect(liveHtml).not.toContain("#parity-frame {");
    });
  }
});
