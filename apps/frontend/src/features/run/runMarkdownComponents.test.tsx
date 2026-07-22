// runMarkdownComponents — web citation chip renderer wiring (WC-P6a).
//
// Asserts the host contribution the cockpit threads as `markdownComponents`
// resolves a projected `[[N]]` chip: the anchor dispatcher routes `#cite-ord:N`
// to the ordinal chip wrapper, which resolves the ordinal against the
// `projectCitations` output fed through the shared `CitationsProvider`.

import { render, screen } from "@testing-library/react";
import type { ReactElement } from "react";
import { describe, expect, it } from "vitest";

import { CitationsProvider, projectCitations } from "@0x-copilot/chat-surface";
import type { RuntimeEventEnvelope } from "@0x-copilot/api-types";

import { runMarkdownComponents } from "./runMarkdownComponents";

function citationMade(ordinal: number, callId: string): RuntimeEventEnvelope {
  return {
    event_id: "e1",
    run_id: "run-1",
    conversation_id: "conv-1",
    sequence_no: 1,
    activity_kind: "tool",
    event_type: "citation_made",
    payload: {
      link: {
        conversation_ordinal: ordinal,
        message_id: "msg-1",
        prose_offset: 0,
        prose_length: 5,
        source_tool_call_id: callId,
      },
    },
    created_at: new Date(1_716_000_000_000).toISOString(),
  } as RuntimeEventEnvelope;
}

function withProjection(events: RuntimeEventEnvelope[], node: ReactElement) {
  const p = projectCitations(events);
  return (
    <CitationsProvider
      citations={p.citations}
      byRun={p.byRun}
      terminalRuns={p.terminalRuns}
      linksByRun={p.linksByRun}
      activeRunId={p.activeRunId}
    >
      {node}
    </CitationsProvider>
  );
}

describe("runMarkdownComponents", () => {
  const Anchor = runMarkdownComponents.a;

  it("resolves an [[N]] ordinal anchor to a bound citation chip", () => {
    render(
      withProjection(
        [citationMade(3, "call_z")],
        <Anchor href="#cite-ord:3">3</Anchor>,
      ),
    );
    const chip = screen.getByText("3");
    expect(chip.getAttribute("data-source-tool-call-id")).toBe("call_z");
    expect(chip.getAttribute("data-conversation-ordinal")).toBe("3");
  });

  it("renders the muted placeholder for an unresolved ordinal", () => {
    render(
      withProjection(
        [citationMade(3, "call_z")],
        <Anchor href="#cite-ord:99">99</Anchor>,
      ),
    );
    // Ordinal 99 has no link → the headless chip shows the `?` placeholder.
    expect(screen.getByText("?")).not.toBeNull();
  });

  it("passes a non-citation anchor through as a plain link", () => {
    render(<Anchor href="https://example.com">docs</Anchor>);
    const link = screen.getByText("docs");
    expect(link.getAttribute("href")).toBe("https://example.com");
    expect(link.getAttribute("target")).toBe("_blank");
  });
});
