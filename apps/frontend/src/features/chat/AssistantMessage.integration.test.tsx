// PR 3.5 / G9 — end-to-end proof that MessageSourcesStrip mounts.
//
// The bug: `MessageSourcesStrip` shipped with unit tests but was never
// rendered inside `AssistantMessage`. This test exercises the full
// path the production code now takes:
//
//   ChatItem  →  chatItemsToThreadMessages (folds run_id into metadata.custom)
//             →  AssistantMessage reads metadata.custom.run_id
//             →  useRunCitations(runId, { sealedOnly: true })
//             →  MessageSourcesStrip renders one chip per citation
//
// If any link in that chain breaks, this test fails.

import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import type {
  CitationSourceRef,
  RuntimeEventEnvelope,
} from "@0x-copilot/api-types";

// Stub the parts walker; AssistantMessage's body is not what this
// integration test exercises (we assert MessageSourcesStrip mount).
vi.mock("./runtime/components", async (orig) => {
  const actual = (await orig()) as Record<string, unknown>;
  return {
    ...actual,
    MessageParts: () => <span data-testid="parts" />,
  };
});
// Stub heavy children — we only assert the strip mount.
vi.mock("./components/markdown/MarkdownText", () => ({
  MarkdownText: () => null,
}));
vi.mock("@0x-copilot/chat-surface", async () => ({
  ...(await vi.importActual<typeof import("@0x-copilot/chat-surface")>(
    "@0x-copilot/chat-surface",
  )),
  Reasoning: () => null,
}));
vi.mock("./components/markdown/ReasoningGroup", () => ({
  ReasoningGroup: () => null,
}));
vi.mock("./components/tools/ApprovalTool", () => ({
  ApprovalTool: () => null,
}));
vi.mock("./components/tools/ConnectorAuthTool", () => ({
  ConnectorAuthTool: () => null,
}));
vi.mock("./components/tools/McpTool", () => ({ McpTool: () => null }));
vi.mock("./components/tools/ProgressTool", () => ({
  ProgressTool: () => null,
}));
vi.mock("./components/tools/SubagentTool", () => ({
  SubagentTool: () => null,
}));
vi.mock("./components/tools/ToolFallback", () => ({
  ToolFallback: () => null,
}));
vi.mock("./components/tools/ToolGroup", () => ({ ToolGroup: () => null }));
vi.mock("./components/messages/AssistantMessageFooter", () => ({
  AssistantMessageFooter: () => null,
}));

import { AssistantMessage } from "./components/messages/AssistantMessage";
import { CitationsProvider } from "./components/citations/citationsContext";
import { applyCitationEvent } from "./chatModel/citationReducer";
import { emptyCitationRegistry } from "@0x-copilot/chat-surface";
import { chatItemsToThreadMessages } from "./chatModel/conversion";
import type { ChatItem } from "./chatModel/types";

const RUN_ID = "run_e2e_1";
const CONVERSATION_ID = "conv_e2e_1";

function citation(
  overrides: Partial<CitationSourceRef> = {},
): CitationSourceRef {
  return {
    citation_id: "c1",
    ordinal: 1,
    source_connector: "notion",
    source_doc_id: "page_1",
    source_url: "https://example.com/p1",
    title: "Approved Positioning",
    snippet: "Aurora 4.0 brings agentic search to every desk.",
    freshness_at: null,
    source_tool_call_id: null,
    ...overrides,
  };
}

function ingestedEvent(c: CitationSourceRef): RuntimeEventEnvelope {
  return {
    event_id: `evt_${c.citation_id}`,
    run_id: RUN_ID,
    conversation_id: CONVERSATION_ID,
    sequence_no: 1,
    activity_kind: "tool",
    created_at: "2026-05-04T12:00:00Z",
    event_type: "source_ingested",
    payload: { citation: c },
  } as RuntimeEventEnvelope;
}

describe("AssistantMessage integration (PR 3.5 / G9)", () => {
  it("renders MessageSourcesStrip end-to-end via the conversion → context → component chain", () => {
    // 1. Build a chat item exactly the way `ChatScreen` would after a
    //    completed run with two citations.
    const items: ChatItem[] = [
      {
        id: "asst_1",
        kind: "message",
        role: "assistant",
        content: [{ type: "text", text: "Per the [c1] positioning…" }],
        runId: RUN_ID,
        status: { type: "complete", reason: "stop" } as never,
      },
    ];

    // 2. Convert through the real pipeline — surfaces run_id into
    //    metadata.custom (the change PR 3.5 made to conversion.ts).
    const threadMessages = chatItemsToThreadMessages(items, null);
    const message = threadMessages[0];
    expect(message.metadata?.custom?.run_id).toBe(RUN_ID);

    // 3. Build the citation registry exactly the way the SSE reducer
    //    does, then mount with the runId in the terminalRuns set.
    let registry = emptyCitationRegistry();
    registry = applyCitationEvent(registry, ingestedEvent(citation()));
    registry = applyCitationEvent(
      registry,
      ingestedEvent(
        citation({
          citation_id: "c2",
          ordinal: 2,
          source_doc_id: "drive_x",
          title: "FY26 Q1 GTM plan",
        }),
      ),
    );

    render(
      <CitationsProvider
        citations={new Map()}
        byRun={registry}
        terminalRuns={new Set([RUN_ID])}
      >
        <AssistantMessage
          message={{
            role: "assistant",
            content: [],
            status: message.status,
            metadata: message.metadata,
          }}
          onMcpAuthConnect={async () => undefined}
          onMcpAuthSkip={async () => undefined}
        />
      </CitationsProvider>,
    );

    // 4. Strip is mounted with one chip per citation in ordinal order.
    const strip = screen.getByLabelText(/Sources cited/i);
    expect(strip).toBeInTheDocument();
    const chips = screen.getAllByRole("listitem");
    expect(chips).toHaveLength(2);
    expect(chips[0]).toHaveTextContent("Approved Positioning");
    expect(chips[1]).toHaveTextContent("FY26 Q1 GTM plan");
  });
});
