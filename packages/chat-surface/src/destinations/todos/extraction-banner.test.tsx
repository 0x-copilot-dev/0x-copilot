import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import {
  ExtractionBanner,
  type TodoExtraction,
  type TodoExtractionId,
} from "./extraction-banner";

function makeExtraction(
  id: string,
  proposals: number,
  overrides: Partial<TodoExtraction> = {},
): TodoExtraction {
  return {
    id: id as TodoExtractionId,
    source: { thread_id: `thr_${id}`, run_id: `rn_${id}` },
    source_title: `Chat ${id}`,
    proposed_todos: Array.from({ length: proposals }).map((_, i) => ({
      text: `Proposed ${id}.${i}`,
      priority: "med" as const,
      due: i === 0 ? "2026-02-01" : undefined,
    })),
    status: "pending",
    created_at: "2026-01-15T12:00:00.000Z",
    ...overrides,
  };
}

describe("ExtractionBanner", () => {
  it("renders nothing when there are no extractions", () => {
    const { container } = render(
      <ExtractionBanner
        extractions={[]}
        onAccept={vi.fn()}
        onReject={vi.fn()}
        onAcceptAll={vi.fn()}
        onDismiss={vi.fn()}
      />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("renders the headline with total proposal count across extractions", () => {
    const extractions: ReadonlyArray<TodoExtraction> = [
      makeExtraction("e1", 2),
      makeExtraction("e2", 1),
    ];
    render(
      <ExtractionBanner
        extractions={extractions}
        onAccept={vi.fn()}
        onReject={vi.fn()}
        onAcceptAll={vi.fn()}
        onDismiss={vi.fn()}
      />,
    );

    const banner = screen.getByTestId("extraction-banner");
    expect(banner).toHaveAttribute("data-extraction-count", "2");
    expect(banner).toHaveAttribute("data-proposal-count", "3");
    expect(banner).toHaveTextContent(/3 possible todos/i);
    // A11y label per todos-prd.md §9
    expect(banner).toHaveAttribute("aria-label", "3 proposed todos from Atlas");
  });

  it("renders singular copy when there is exactly one proposal", () => {
    render(
      <ExtractionBanner
        extractions={[makeExtraction("solo", 1)]}
        onAccept={vi.fn()}
        onReject={vi.fn()}
        onAcceptAll={vi.fn()}
        onDismiss={vi.fn()}
      />,
    );
    expect(screen.getByTestId("extraction-banner")).toHaveTextContent(
      /1 possible todo from your last chat/i,
    );
  });

  it("shows '+N more' when a single extraction proposes multiple todos", () => {
    render(
      <ExtractionBanner
        extractions={[makeExtraction("e1", 4)]}
        onAccept={vi.fn()}
        onReject={vi.fn()}
        onAcceptAll={vi.fn()}
        onDismiss={vi.fn()}
      />,
    );
    expect(screen.getByTestId("extraction-row-meta")).toHaveTextContent(
      /\+3 more/,
    );
  });

  it("fires onAccept / onReject with the extraction id (one-click)", () => {
    const onAccept = vi.fn();
    const onReject = vi.fn();
    render(
      <ExtractionBanner
        extractions={[makeExtraction("e1", 1), makeExtraction("e2", 1)]}
        onAccept={onAccept}
        onReject={onReject}
        onAcceptAll={vi.fn()}
        onDismiss={vi.fn()}
      />,
    );

    const accepts = screen.getAllByTestId("extraction-row-accept");
    fireEvent.click(accepts[0]!);
    expect(onAccept).toHaveBeenLastCalledWith("e1");

    const rejects = screen.getAllByTestId("extraction-row-reject");
    fireEvent.click(rejects[1]!);
    expect(onReject).toHaveBeenLastCalledWith("e2");
  });

  it("fires onAcceptAll exactly once when 'Accept all' is clicked", () => {
    const onAcceptAll = vi.fn();
    render(
      <ExtractionBanner
        extractions={[makeExtraction("e1", 3)]}
        onAccept={vi.fn()}
        onReject={vi.fn()}
        onAcceptAll={onAcceptAll}
        onDismiss={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByTestId("extraction-banner-accept-all"));
    expect(onAcceptAll).toHaveBeenCalledTimes(1);
  });

  it("fires onDismiss when the close button is clicked", () => {
    const onDismiss = vi.fn();
    render(
      <ExtractionBanner
        extractions={[makeExtraction("e1", 1)]}
        onAccept={vi.fn()}
        onReject={vi.fn()}
        onAcceptAll={vi.fn()}
        onDismiss={onDismiss}
      />,
    );
    fireEvent.click(screen.getByTestId("extraction-banner-dismiss"));
    expect(onDismiss).toHaveBeenCalledTimes(1);
  });

  it("renders a row per extraction, tagged with the extraction id", () => {
    render(
      <ExtractionBanner
        extractions={[
          makeExtraction("e1", 1),
          makeExtraction("e2", 1),
          makeExtraction("e3", 1),
        ]}
        onAccept={vi.fn()}
        onReject={vi.fn()}
        onAcceptAll={vi.fn()}
        onDismiss={vi.fn()}
      />,
    );

    const rows = screen.getAllByTestId("extraction-row");
    expect(rows).toHaveLength(3);
    expect(rows.map((r) => r.getAttribute("data-extraction-id"))).toEqual([
      "e1",
      "e2",
      "e3",
    ]);
  });
});
