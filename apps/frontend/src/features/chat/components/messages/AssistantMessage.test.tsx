// PR 3.5 / G6 — class contract + PR 3.5 / G9 — MessageSourcesStrip mount.
//
// Two contracts under test:
//   1. The container classes (`aui-message aui-message--assistant`) that
//      `apps/frontend/src/styles.css` uses to flush-left the body and
//      drop the bubble (PR 2.3).
//   2. `<MessageSourcesStrip>` mounts iff the run has sealed citations
//      AND the assistant message has reached terminal status. This is the
//      G9 fix proven at the component layer.

import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import type {
  CitationSourceRef,
  RuntimeEventEnvelope,
} from "@enterprise-search/api-types";

// Stub the parts walker so we exercise only the strip-mount branch and
// the class contract. The real walker is covered by its own tests; here
// we just need a sentinel so AssistantMessage's body renders.
vi.mock("../../runtime/components", async (orig) => {
  const actual = (await orig()) as Record<string, unknown>;
  return {
    ...actual,
    MessageParts: () => <span data-testid="parts" />,
  };
});

// Stub everything `AssistantMessage` imports so we exercise only the
// strip-mount branch and the class contract. The real MarkdownText /
// tools / footer are covered by their own tests.
vi.mock("../markdown/MarkdownText", () => ({
  MarkdownText: () => null,
}));
vi.mock("@enterprise-search/chat-surface", async () => ({
  ...(await vi.importActual<typeof import("@enterprise-search/chat-surface")>(
    "@enterprise-search/chat-surface",
  )),
  Reasoning: () => null,
}));
vi.mock("../markdown/ReasoningGroup", () => ({
  ReasoningGroup: () => null,
}));
vi.mock("../tools/ApprovalTool", () => ({ ApprovalTool: () => null }));
vi.mock("../tools/ConnectorAuthTool", () => ({
  ConnectorAuthTool: () => null,
}));
vi.mock("../tools/McpTool", () => ({ McpTool: () => null }));
vi.mock("../tools/ProgressTool", () => ({ ProgressTool: () => null }));
vi.mock("../tools/SubagentTool", () => ({ SubagentTool: () => null }));
vi.mock("../tools/ToolFallback", () => ({ ToolFallback: () => null }));
vi.mock("../tools/ToolGroup", () => ({ ToolGroup: () => null }));
vi.mock("./AssistantMessageFooter", () => ({
  AssistantMessageFooter: () => <span data-testid="footer" />,
}));

import { AssistantMessage } from "./AssistantMessage";
import { CitationsProvider } from "../citations/citationsContext";
import { applyCitationEvent } from "../../chatModel/citationReducer";
import { emptyCitationRegistry } from "../../chatModel/citationsRegistry";

const RUN_ID = "run_a";
const CONVERSATION_ID = "conv_a";

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

function renderWith(props: {
  status: { type: string };
  runId?: string;
  withCitations?: boolean;
  terminal?: boolean;
}) {
  let registry = emptyCitationRegistry();
  if (props.withCitations) {
    registry = applyCitationEvent(registry, ingestedEvent(citation()));
    registry = applyCitationEvent(
      registry,
      ingestedEvent(
        citation({ citation_id: "c2", ordinal: 2, source_doc_id: "drive_x" }),
      ),
    );
  }
  const terminalRuns = new Set(
    props.terminal && props.runId ? [props.runId] : [],
  );
  return render(
    <CitationsProvider
      citations={new Map()}
      byRun={registry}
      terminalRuns={terminalRuns}
    >
      <AssistantMessage
        message={{
          role: "assistant",
          content: [],
          status: props.status as never,
          metadata: props.runId
            ? { custom: { run_id: props.runId } }
            : undefined,
        }}
        onMcpAuthConnect={async () => undefined}
        onMcpAuthSkip={async () => undefined}
      />
    </CitationsProvider>,
  );
}

describe("AssistantMessage", () => {
  it("renders the flush-left class contract that styles.css keys off", () => {
    const { container } = renderWith({ status: { type: "complete" } });
    const root = container.querySelector(".aui-message");
    expect(root).not.toBeNull();
    expect(root!.className).toContain("aui-message--assistant");
  });

  it("does not render MessageSourcesStrip mid-stream (status running)", () => {
    renderWith({
      status: { type: "running" },
      runId: RUN_ID,
      withCitations: true,
      terminal: false,
    });
    expect(screen.queryByLabelText(/Sources cited/i)).toBeNull();
  });

  it("renders MessageSourcesStrip when run is terminal AND citations exist", () => {
    renderWith({
      status: { type: "complete" },
      runId: RUN_ID,
      withCitations: true,
      terminal: true,
    });
    const strip = screen.getByLabelText(/Sources cited/i);
    expect(strip).toBeInTheDocument();
    // One chip per citation, each clickable.
    expect(screen.getAllByRole("listitem")).toHaveLength(2);
  });

  it("does not render the strip when run is terminal but has no citations", () => {
    renderWith({
      status: { type: "complete" },
      runId: RUN_ID,
      withCitations: false,
      terminal: true,
    });
    expect(screen.queryByLabelText(/Sources cited/i)).toBeNull();
  });

  it("does not render the strip when message has no run_id (optimistic / system)", () => {
    renderWith({
      status: { type: "complete" },
      withCitations: true,
      terminal: true,
    });
    expect(screen.queryByLabelText(/Sources cited/i)).toBeNull();
  });
});
