import type { ReactElement } from "react";
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import type { SurfaceSpec, SurfaceState } from "../_shared/specTypes";
import { BOARD_SPEC, BOARD_STATE } from "./fixtures";
import { boardAdapter, CARD_RENDER_CAP } from "./BoardRenderer";

/**
 * The adapter is typed to `SurfaceState`, but what actually arrives is an
 * untrusted boundary value — a change list riding alongside, a `data` that is
 * not the shape the spec promised. The renderer has to be total over that, so
 * these cases go in through the same entry point the host uses.
 */
function renderBoard(state: unknown): ReactElement {
  return boardAdapter.renderCurrent(state as SurfaceState);
}

describe("boardAdapter contract", () => {
  it("registers scheme 'board' with first-party metadata", () => {
    expect(boardAdapter.scheme).toBe("board");
    expect(boardAdapter.metadata.origin).toBe("first-party");
    expect(boardAdapter.metadata.schemaVersion).toBe(1);
  });

  it("matches only board:// uris", () => {
    expect(boardAdapter.matches("board://linear/sprint")).toBe(true);
    expect(boardAdapter.matches("record://x")).toBe(false);
  });
});

describe("boardAdapter.renderCurrent", () => {
  it("groups cards into lanes by group_by_path", () => {
    render(boardAdapter.renderCurrent(BOARD_STATE));
    const lanes = screen.getByTestId("board-lanes");
    // Two distinct statuses: In Progress (2 cards) + Todo (1 card).
    expect(lanes).toHaveTextContent("In Progress");
    expect(lanes).toHaveTextContent("Todo");
    expect(screen.getByTestId("board-lane-0")).toBeInTheDocument();
    expect(screen.getByTestId("board-lane-1")).toBeInTheDocument();
  });

  it("renders each card's title column and field columns", () => {
    render(boardAdapter.renderCurrent(BOARD_STATE));
    expect(screen.getByTestId("board-lanes")).toHaveTextContent(
      "Wire archetype renderers",
    );
    expect(screen.getByTestId("board-lanes")).toHaveTextContent("Sarah");
  });

  it("renders the no-spec view without throwing when the spec is absent", () => {
    const state: SurfaceState = { data: [{ title: "x" }] };
    expect(() => render(boardAdapter.renderCurrent(state))).not.toThrow();
    expect(screen.getByTestId("surface-no-spec-note")).toBeInTheDocument();
    expect(screen.getByTestId("surface-read-only-footer")).toBeInTheDocument();
    expect(screen.queryByTestId("surface-preparing-hint")).toBeNull();
  });

  it("states the true card count when the spec-less payload is a bare array", () => {
    const state: SurfaceState = {
      data: [{ title: "x" }, { title: "y" }, { title: "z" }],
    };
    render(boardAdapter.renderCurrent(state));
    // Only the first card's fields are painted; the badge is what keeps the
    // note's "the payload as the tool sent it" honest about the other two.
    expect(screen.getByTestId("field-title-value")).toHaveTextContent("x");
    expect(screen.getByTestId("surface-badge")).toHaveTextContent("3 cards");
  });
});

// Grouping is what makes this archetype a board rather than a list: lane
// membership, lane order, and the per-lane count all have to be right.
describe("BoardRenderer grouping", () => {
  it("puts each card in the lane its group value names, in first-seen order", () => {
    render(boardAdapter.renderCurrent(BOARD_STATE));
    expect(screen.getByTestId("board-lane-0-header")).toHaveTextContent(
      "In Progress",
    );
    expect(screen.getByTestId("board-lane-1-header")).toHaveTextContent("Todo");
    // Lane 0 holds cards 0 and 2 of the fixture; lane 1 holds card 1.
    expect(screen.getByTestId("board-lane-0-card-0-title")).toHaveTextContent(
      "Wire archetype renderers",
    );
    expect(screen.getByTestId("board-lane-0-card-1-title")).toHaveTextContent(
      "Golden fixtures",
    );
    expect(screen.getByTestId("board-lane-1-card-0-title")).toHaveTextContent(
      "Spec authoring skill",
    );
    expect(screen.queryByTestId("board-lane-1-card-1")).toBeNull();
  });

  it("counts the cards in each lane, not the cards on the board", () => {
    render(boardAdapter.renderCurrent(BOARD_STATE));
    expect(screen.getByTestId("board-lane-0-count")).toHaveTextContent("2");
    expect(screen.getByTestId("board-lane-1-count")).toHaveTextContent("1");
    expect(screen.getByTestId("surface-badge")).toHaveTextContent("3 cards");
  });

  it("renders each remaining column as a label/value pair in the meta row", () => {
    render(boardAdapter.renderCurrent(BOARD_STATE));
    const meta = screen.getByTestId("board-lane-0-card-0-meta");
    expect(meta).toHaveTextContent("Assignee");
    expect(meta).toHaveTextContent("Sarah");
    // The title column is the card title, never a meta entry.
    expect(meta).not.toHaveTextContent("Wire archetype renderers");
  });

  // Caught by rendering it: half-filled cards are the norm on a real board, and
  // a column with no value was painting its label alone — "OWNER unassigned
  // DUE" — which reads as a fact that got truncated rather than one that was
  // never there.
  it("drops a meta entry whose value resolves to nothing, label and all", () => {
    const spec: SurfaceSpec = {
      spec_version: 1,
      archetype: "board",
      source: { server: "s", tool: "t" },
      title_path: "board.name",
      items_path: "cards",
      columns: [
        { label: "Title", path: "title" },
        { label: "Owner", path: "assignee", format: "user" },
        { label: "Due", path: "due", format: "datetime" },
      ],
    };
    render(
      boardAdapter.renderCurrent({
        spec,
        data: {
          board: { name: "Half-filled" },
          cards: [
            { title: "Has an owner", assignee: "Sarah" },
            { title: "Has nothing else" },
          ],
        },
      }),
    );
    const meta = screen.getByTestId("board-lane-0-card-0-meta");
    expect(meta).toHaveTextContent("Owner");
    expect(meta).toHaveTextContent("Sarah");
    expect(meta).not.toHaveTextContent("Due");
    // Every column empty ⇒ no meta row at all, rather than an empty strip
    // pushing a gap under the title.
    expect(screen.queryByTestId("board-lane-0-card-1-meta")).toBeNull();
    expect(screen.getByTestId("board-lane-0-card-1-title")).toHaveTextContent(
      "Has nothing else",
    );
  });
});

// The ungrouped bucket: a spec with no `group_by_path`, and a card whose group
// value resolves to nothing, both land in one honest lane rather than silently
// disappearing or inventing a group name from the payload.
describe("BoardRenderer ungrouped bucket", () => {
  const ungroupedSpec: SurfaceSpec = {
    spec_version: 1,
    archetype: "board",
    source: { server: "s", tool: "t" },
    title_path: "board.name",
    items_path: "cards",
    columns: [{ label: "Title", path: "title" }],
  };

  it("puts every card in one 'Ungrouped' lane when the spec has no group_by_path", () => {
    render(
      boardAdapter.renderCurrent({
        spec: ungroupedSpec,
        data: {
          board: { name: "Flat" },
          cards: [{ title: "One" }, { title: "Two" }],
        },
      }),
    );
    expect(screen.getByTestId("board-lane-0-header")).toHaveTextContent(
      "Ungrouped",
    );
    expect(screen.getByTestId("board-lane-0-count")).toHaveTextContent("2");
    expect(screen.queryByTestId("board-lane-1")).toBeNull();
  });

  it("falls back to 'Ungrouped' for a card whose group value is missing or empty", () => {
    render(
      renderBoard({
        spec: { ...ungroupedSpec, group_by_path: "status" },
        data: {
          board: { name: "Partial" },
          cards: [
            { title: "Known", status: "Todo" },
            { title: "Missing" },
            { title: "Empty", status: "" },
            { title: "Nulled", status: null },
          ],
        },
      }),
    );
    expect(screen.getByTestId("board-lane-0-header")).toHaveTextContent("Todo");
    expect(screen.getByTestId("board-lane-1-header")).toHaveTextContent(
      "Ungrouped",
    );
    expect(screen.getByTestId("board-lane-1-count")).toHaveTextContent("3");
  });
});

describe("BoardRenderer empty board", () => {
  const spec: SurfaceSpec = { ...BOARD_SPEC, items_path: "cards" };

  it("says the board is empty instead of painting a lane grid", () => {
    render(
      boardAdapter.renderCurrent({
        spec,
        data: { board: { name: "Sprint 43" }, cards: [] },
      }),
    );
    expect(screen.getByTestId("surface-empty")).toHaveTextContent(
      "No cards to display.",
    );
    expect(screen.queryByTestId("board-lanes")).toBeNull();
    expect(screen.getByTestId("surface-badge")).toHaveTextContent("0 cards");
  });

  it("stays empty rather than throwing when items_path resolves to a non-array", () => {
    for (const cards of [undefined, null, "nope", 7, { a: 1 }]) {
      const { unmount } = render(
        renderBoard({
          spec,
          data: { board: { name: "Hostile" }, cards },
        }),
      );
      expect(screen.getByTestId("surface-empty")).toBeInTheDocument();
      expect(screen.queryByTestId("board-lanes")).toBeNull();
      unmount();
    }
  });
});

describe("BoardRenderer card cap", () => {
  const spec: SurfaceSpec = {
    spec_version: 1,
    archetype: "board",
    source: { server: "s", tool: "t" },
    title_path: "name",
    items_path: "cards",
    group_by_path: "status",
    columns: [{ label: "Title", path: "title" }],
  };
  const data = {
    name: "Huge",
    cards: Array.from({ length: 250 }, (_, i) => ({
      title: `Card ${i}`,
      status: "Todo",
    })),
  };

  it("paints at most CARD_RENDER_CAP cards and states the truncation", () => {
    render(boardAdapter.renderCurrent({ spec, data }));
    expect(
      screen.getByTestId(`board-lane-0-card-${CARD_RENDER_CAP - 1}`),
    ).toBeInTheDocument();
    expect(
      screen.queryByTestId(`board-lane-0-card-${CARD_RENDER_CAP}`),
    ).toBeNull();
    expect(screen.getByTestId("board-card-cap")).toHaveTextContent(
      "Showing 200 of 250 cards.",
    );
  });

  it("badges the true total, so the cap note is the only thing that shrinks", () => {
    render(boardAdapter.renderCurrent({ spec, data }));
    expect(screen.getByTestId("surface-badge")).toHaveTextContent("250 cards");
    expect(screen.getByTestId("board-lane-0-count")).toHaveTextContent("200");
  });

  it("shows no cap note when the board fits", () => {
    render(boardAdapter.renderCurrent(BOARD_STATE));
    expect(screen.queryByTestId("board-card-cap")).toBeNull();
  });
});

// The change mark is the ATTENTION register — it says a decision is owed on
// THIS card. It must therefore appear on exactly the cards a change names, and
// it must not be reachable from the payload itself.
describe("BoardRenderer changed-card mark", () => {
  const changedState = {
    ...BOARD_STATE,
    changes: [{ field: "cards.2.assignee", old: "Marcus", new: "Priya" }],
  };

  it("marks only the card the change names, keyed by its original index", () => {
    render(renderBoard(changedState));
    // Fixture card 2 ("Golden fixtures") is the SECOND card of lane 0 —
    // grouping reorders cards, and the mark has to survive that.
    const marked = screen.getByTestId("board-lane-0-card-1");
    expect(marked).toHaveAttribute("data-changed", "true");
    expect(screen.getByTestId("board-lane-0-card-1-title")).toHaveTextContent(
      "Golden fixtures",
    );
    for (const testId of ["board-lane-0-card-0", "board-lane-1-card-0"]) {
      expect(screen.getByTestId(testId)).not.toHaveAttribute("data-changed");
    }
  });

  it("gives the marked card the accent hairline and the 2px inset bar", () => {
    render(renderBoard(changedState));
    const marked = screen.getByTestId("board-lane-0-card-1");
    // The bar is the accent at full strength; the border is the accent's
    // hairline rung — NOT the board's identity hue, which stays on the kicker.
    expect(marked.style.boxShadow).toBe("inset 2px 0 0 var(--color-accent)");
    expect(marked.style.border).toContain("--color-accent-line");
    expect(marked.style.border).not.toContain("--surface-src");

    const plain = screen.getByTestId("board-lane-0-card-0");
    expect(plain.style.boxShadow).toBe("");
    expect(plain.style.border).not.toContain("--color-accent");
  });

  it("does not encode the mark in colour alone", () => {
    render(renderBoard(changedState));
    expect(screen.getByTestId("board-lane-0-card-1-changed")).toHaveTextContent(
      "Changed",
    );
    expect(screen.queryByTestId("board-lane-0-card-0-changed")).toBeNull();
  });

  it("marks nothing when no change list rides along", () => {
    render(boardAdapter.renderCurrent(BOARD_STATE));
    for (const testId of [
      "board-lane-0-card-0",
      "board-lane-0-card-1",
      "board-lane-1-card-0",
    ]) {
      expect(screen.getByTestId(testId)).not.toHaveAttribute("data-changed");
    }
  });

  it("ignores change paths that do not name a card index", () => {
    render(
      renderBoard({
        ...BOARD_STATE,
        changes: [
          // The whole list — marking every lane would make the mark noise.
          { field: "cards", old: 1, new: 2 },
          // Another branch of the payload entirely.
          { field: "board.name", old: "Sprint 41", new: "Sprint 42" },
          // Right prefix, no index.
          { field: "cards.status", old: "a", new: "b" },
          // Out of range: no card to mark.
          { field: "cards.99.status", old: "a", new: "b" },
        ],
      }),
    );
    for (const testId of [
      "board-lane-0-card-0",
      "board-lane-0-card-1",
      "board-lane-1-card-0",
    ]) {
      expect(screen.getByTestId(testId)).not.toHaveAttribute("data-changed");
    }
  });

  it("marks the whole card when the change names the item itself", () => {
    render(
      renderBoard({
        ...BOARD_STATE,
        changes: [{ field: "cards.1", old: null, new: { title: "x" } }],
      }),
    );
    expect(screen.getByTestId("board-lane-1-card-0")).toHaveAttribute(
      "data-changed",
      "true",
    );
    expect(screen.getByTestId("board-lane-0-card-0")).not.toHaveAttribute(
      "data-changed",
    );
  });

  it("survives a hostile change list without throwing", () => {
    expect(() =>
      render(
        renderBoard({
          ...BOARD_STATE,
          changes: [null, 7, "cards.0", { field: 42 }, { field: "cards.0" }],
        }),
      ),
    ).not.toThrow();
    expect(screen.getByTestId("board-lane-0-card-0")).toHaveAttribute(
      "data-changed",
      "true",
    );
  });
});

// The two details in the design that look like mistakes and are not: the 1px
// grid gap IS the divider, and the sticky header's negative `top` is paired
// with the negative margin that cancels the lane's padding.
describe("BoardRenderer lane chrome", () => {
  it("draws lane dividers with the grid gap, not with borders", () => {
    render(boardAdapter.renderCurrent(BOARD_STATE));
    const lanes = screen.getByTestId("board-lanes");
    expect(lanes).toHaveStyle({
      display: "grid",
      gridAutoFlow: "column",
      gridAutoColumns: "minmax(196px, 1fr)",
      gap: "1px",
    });
    // The hairline shows THROUGH the gap: the grid is the line, each lane
    // paints over it, and no lane carries a border of its own.
    expect(lanes.style.background).toContain("--color-border");
    expect(lanes.style.border).toBe("");
    expect(screen.getByTestId("board-lane-0").style.border).toBe("");
  });

  it("contains overscroll on the board and on every lane", () => {
    render(boardAdapter.renderCurrent(BOARD_STATE));
    expect(screen.getByTestId("board-lanes")).toHaveStyle({
      overflowX: "auto",
      overscrollBehaviorX: "contain",
    });
    expect(screen.getByTestId("board-lane-0")).toHaveStyle({
      maxHeight: "352px",
      overflowY: "auto",
      overscrollBehaviorY: "contain",
    });
  });

  it("sticks the lane header flush to the lane's padding box", () => {
    render(boardAdapter.renderCurrent(BOARD_STATE));
    const header = screen.getByTestId("board-lane-0-header");
    // `top` and `margin` are one pair: the margin cancels the lane's 10px
    // padding so the header spans the lane, and the matching negative `top` is
    // what parks it flush instead of 10px down. Both must move together.
    expect(header).toHaveStyle({ position: "sticky", top: "-10px" });
    expect(header.style.margin).toBe("-10px -10px 0px");
    expect(screen.getByTestId("board-lane-0")).toHaveStyle({ padding: "10px" });
    // Opaque, or the cards would scroll visibly under it.
    expect(header.style.background).toContain("--color-surface");
  });

  it("sets the lane header and card meta in the mono micro register", () => {
    render(boardAdapter.renderCurrent(BOARD_STATE));
    const header = screen.getByTestId("board-lane-0-header");
    expect(header.style.fontFamily).toContain("--font-mono");
    expect(header.style.fontSize).toContain("--font-size-mono-9-5");
    expect(header).toHaveStyle({ textTransform: "uppercase" });
    const meta = screen.getByTestId("board-lane-0-card-0-meta");
    expect(meta.style.fontFamily).toContain("--font-mono");
    expect(meta.style.fontSize).toContain("--font-size-mono-9-5");
  });

  it("gives cards the design's chrome", () => {
    render(boardAdapter.renderCurrent(BOARD_STATE));
    const card = screen.getByTestId("board-lane-0-card-0");
    expect(card).toHaveStyle({ borderRadius: "8px", padding: "8px 9px" });
    expect(card.style.background).toContain("--color-surface-elevated");
    expect(screen.getByTestId("board-lane-0-card-0-title")).toHaveStyle({
      fontSize: "12px",
      lineHeight: "1.4",
    });
  });
});

describe("boardAdapter.renderDiff", () => {
  it("renders a before→after row per changed card field", () => {
    render(
      boardAdapter.renderDiff({
        spec: BOARD_STATE.spec,
        changes: [{ field: "assignee", old: "Marcus", new: "Priya" }],
      }),
    );
    expect(screen.getByTestId("board-renderer")).toHaveAttribute(
      "data-mode",
      "diff",
    );
    expect(screen.getByTestId("field-assignee-next")).toHaveTextContent(
      "Priya",
    );
  });
});
