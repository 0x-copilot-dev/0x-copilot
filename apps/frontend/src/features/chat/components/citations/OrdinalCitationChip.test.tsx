// PR 1.1-rev2 — OrdinalCitationChip + useOrdinalCitation tests.
//
// The chip resolves an ordinal (parsed from a `[[N]]` token by the
// remark plugin) against the citation-link registry. The hook falls
// back to scanning every run in the registry when ``activeRunId`` is
// null — necessary for chips on a completed assistant message, where
// the run is no longer "active" but the chips still need to render
// against the message that emitted them.

import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import type { ReactElement, ReactNode } from "react";
import { CitationsProvider, type CitationLookup } from "./citationsContext";
import { OrdinalCitationChip } from "./OrdinalCitationChip";
import {
  emptyCitationLinkRegistry,
  upsertCitationLink,
} from "../../chatModel/citationLinkReducer";
import type { CitationLink } from "@enterprise-search/api-types";

const RUN = "run_1";

function link(overrides: Partial<CitationLink>): CitationLink {
  return {
    conversation_ordinal: 1,
    message_id: "msg_1",
    prose_offset: 0,
    prose_length: 5,
    source_tool_call_id: "call_xyz",
    ...overrides,
  };
}

function withProvider(
  children: ReactNode,
  opts: {
    activeRunId?: string | null;
    seed?: ReadonlyArray<{ runId: string; link: CitationLink }>;
  } = {},
): ReactElement {
  let registry = emptyCitationLinkRegistry();
  for (const entry of opts.seed ?? []) {
    registry = upsertCitationLink(registry, entry.runId, entry.link);
  }
  const emptyCitations: CitationLookup = new Map();
  return (
    <CitationsProvider
      citations={emptyCitations}
      linksByRun={registry}
      activeRunId={opts.activeRunId ?? null}
    >
      {children}
    </CitationsProvider>
  );
}

describe("OrdinalCitationChip", () => {
  it("renders the ordinal when the link resolves in the active run", () => {
    render(
      withProvider(<OrdinalCitationChip conversationOrdinal={3} />, {
        activeRunId: RUN,
        seed: [{ runId: RUN, link: link({ conversation_ordinal: 3 }) }],
      }),
    );
    const chip = screen.getByRole("link");
    expect(chip.textContent).toBe("3");
    expect(chip.getAttribute("data-conversation-ordinal")).toBe("3");
  });

  it("renders ?-placeholder when no link exists for the ordinal", () => {
    render(
      withProvider(<OrdinalCitationChip conversationOrdinal={42} />, {
        activeRunId: RUN,
        seed: [{ runId: RUN, link: link({ conversation_ordinal: 1 }) }],
      }),
    );
    expect(screen.queryByRole("link")).toBeNull();
    expect(screen.getByText("?")).toBeInTheDocument();
  });

  it("falls back to scanning all runs when activeRunId is null", () => {
    // Regression pin: previously the hook bailed early when
    // activeRunId was null, leaving every chip on a completed
    // assistant message rendered as the muted ?-placeholder.
    render(
      withProvider(<OrdinalCitationChip conversationOrdinal={5} />, {
        activeRunId: null,
        seed: [{ runId: RUN, link: link({ conversation_ordinal: 5 }) }],
      }),
    );
    const chip = screen.getByRole("link");
    expect(chip.textContent).toBe("5");
  });

  it("renders even when source_tool_call_id is empty", () => {
    // Hallucinated ordinal case: the resolver fired the event but
    // the allocator hadn't bound a tool_call_id. Chip still resolves
    // — it just doesn't carry the data attribute that drives the
    // tool-card click handshake.
    render(
      withProvider(<OrdinalCitationChip conversationOrdinal={9} />, {
        activeRunId: RUN,
        seed: [
          {
            runId: RUN,
            link: link({
              conversation_ordinal: 9,
              source_tool_call_id: "",
            }),
          },
        ],
      }),
    );
    const chip = screen.getByRole("link");
    expect(chip.textContent).toBe("9");
    expect(chip.getAttribute("data-source-tool-call-id")).toBeNull();
  });
});
