// runMarkdownComponents — desktop citation chip renderer wiring.
//
// The desktop twin of `apps/frontend/src/features/run/runMarkdownComponents.test.tsx`.
// Before this host contribution existed, the cockpit passed no `components.a`, so
// Streamdown rendered the raw `[[N]]` token AND raised its own "Open external
// link?" popover over the internal `#cite-ord:N` href. These tests pin the two
// properties that stop both: the ordinal anchor becomes a resolved chip whose
// text is the bare number, and the raw token never survives into the output.

import { cleanup, render, screen } from "@testing-library/react";
import type { ReactElement } from "react";
import { afterEach, describe, expect, it } from "vitest";

import { CitationsProvider, projectCitations } from "@0x-copilot/chat-surface";
import type { RuntimeEventEnvelope } from "@0x-copilot/api-types";

import { runMarkdownComponents } from "./runMarkdownComponents";

function citationMade(ordinal: number, callId: string): RuntimeEventEnvelope {
  return {
    event_id: `e${ordinal}`,
    run_id: "run-1",
    conversation_id: "conv-1",
    sequence_no: ordinal,
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

// This workspace runs vitest with `globals: false`, so testing-library's
// automatic afterEach cleanup does not self-register — do it explicitly, or a
// prior render's chip leaks into the next case's `getByText`.
afterEach(() => {
  cleanup();
});

describe("desktop runMarkdownComponents", () => {
  const Anchor = runMarkdownComponents.a;

  it("resolves an [[N]] ordinal anchor to a bound citation chip", () => {
    render(
      withProjection(
        [citationMade(8, "call_downloads")],
        // The remark plugin emits the token itself as the anchor's child; the
        // dispatcher must DISCARD that label, not render it.
        <Anchor href="#cite-ord:8">[[8]]</Anchor>,
      ),
    );
    const chip = screen.getByText("8");
    expect(chip).toHaveClass("citation-chip");
    expect(chip.getAttribute("data-source-tool-call-id")).toBe(
      "call_downloads",
    );
    expect(chip.getAttribute("data-conversation-ordinal")).toBe("8");
  });

  it("never leaks the raw [[N]] token into the output", () => {
    const { container } = render(
      withProjection(
        [citationMade(8, "call_downloads")],
        <Anchor href="#cite-ord:8">[[8]]</Anchor>,
      ),
    );
    // The reported bug verbatim: `[[8]]` visible in the transcript.
    expect(container.textContent).not.toContain("[[8]]");
    expect(container.textContent).toBe("8");
  });

  it("keeps the citation href internal so no external-link prompt applies", () => {
    render(
      withProjection(
        [citationMade(8, "call_downloads")],
        <Anchor href="#cite-ord:8">[[8]]</Anchor>,
      ),
    );
    const chip = screen.getByText("8");
    // An in-page fragment, and NOT a new-tab target — Streamdown's untrusted-URL
    // popover only wraps anchors it renders itself, which it no longer does here.
    expect(chip.getAttribute("href")).toBe("#tool-call-call_downloads");
    expect(chip.getAttribute("target")).toBeNull();
  });

  it("renders the muted placeholder for an unresolved ordinal", () => {
    render(
      withProjection(
        [citationMade(8, "call_downloads")],
        <Anchor href="#cite-ord:99">[[99]]</Anchor>,
      ),
    );
    // A hallucinated ordinal must not invent a source.
    const placeholder = screen.getByText("?");
    expect(placeholder).toHaveClass("citation-chip--unresolved");
  });

  it("passes a non-citation anchor through as a plain external link", () => {
    render(<Anchor href="https://example.com">docs</Anchor>);
    const link = screen.getByText("docs");
    expect(link.getAttribute("href")).toBe("https://example.com");
    expect(link.getAttribute("target")).toBe("_blank");
    expect(link.getAttribute("rel")).toBe("noreferrer");
  });
});
