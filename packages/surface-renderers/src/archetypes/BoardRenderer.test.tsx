import type { ReactElement } from "react";
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import type { SurfaceSpec, SurfaceState } from "../_shared/specTypes";
import { BOARD_DATA, BOARD_SPEC, BOARD_STATE } from "./fixtures";
import { BoardRenderer, boardAdapter, CARD_RENDER_CAP } from "./BoardRenderer";

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
// THIS card. Two separate things have to be true of it: it lands on exactly the
// cards a TRUSTED change names, and nothing in the rendered payload can produce
// it. The second half is its own describe block below.
describe("BoardRenderer changed-card mark", () => {
  // A field edit — the card stays where it is.
  const FIELD_EDIT = { field: "cards.2.assignee", old: "Marcus", new: "Priya" };
  // A lane move — `status` IS the fixture spec's `group_by_path`.
  const LANE_MOVE = {
    field: "cards.2.status",
    old: "In Progress",
    new: "In review",
  };

  it("marks only the card the change names, keyed by its original index", () => {
    render(BoardRenderer(BOARD_STATE, [FIELD_EDIT]));
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
    render(BoardRenderer(BOARD_STATE, [FIELD_EDIT]));
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
    render(BoardRenderer(BOARD_STATE, [FIELD_EDIT]));
    expect(screen.getByTestId("board-lane-0-card-1-changed")).toHaveTextContent(
      "Changed",
    );
    expect(screen.queryByTestId("board-lane-0-card-0-changed")).toBeNull();
  });

  // The chip is what turns a colour into a claim. A moved card still renders in
  // the lane its payload puts it in — the move is not committed — so without it
  // the surface shows an accent bar and never says what the change does.
  it("states the destination lane on the card that is moving", () => {
    render(BoardRenderer(BOARD_STATE, [LANE_MOVE]));
    expect(
      screen.getByTestId("board-lane-0-card-1-transition"),
    ).toHaveTextContent("→ In review");
    // The card is still painted in its CURRENT lane, and its meta facts survive.
    expect(screen.getByTestId("board-lane-0-header")).toHaveTextContent(
      "In Progress",
    );
    expect(screen.getByTestId("board-lane-0-card-1-meta")).toHaveTextContent(
      "Priya",
    );
    expect(screen.queryByTestId("board-lane-0-card-0-transition")).toBeNull();
  });

  it("draws the chip in the attention register, not the identity hue", () => {
    render(BoardRenderer(BOARD_STATE, [LANE_MOVE]));
    const chip = screen.getByTestId("board-lane-0-card-1-transition");
    expect(chip.style.background).toContain("--color-accent");
    expect(chip.style.boxShadow).toContain("--color-accent-line");
    expect(chip.style.color).toBe("var(--color-text)");
    expect(chip.style.background).not.toContain("--surface-src");
    expect(chip.style.boxShadow).not.toContain("--surface-src");
  });

  it("says nothing about a destination when the card did not move", () => {
    render(BoardRenderer(BOARD_STATE, [FIELD_EDIT]));
    // Marked, because a decision is owed; no chip, because "→" would be a claim
    // about a lane change that is not in the change list.
    expect(screen.getByTestId("board-lane-0-card-1")).toHaveAttribute(
      "data-changed",
      "true",
    );
    expect(screen.queryByTestId("board-lane-0-card-1-transition")).toBeNull();
  });

  it("keeps the destination when the same card also carries a field edit", () => {
    for (const changes of [
      [LANE_MOVE, FIELD_EDIT],
      [FIELD_EDIT, LANE_MOVE],
    ]) {
      const { unmount } = render(BoardRenderer(BOARD_STATE, changes));
      expect(
        screen.getByTestId("board-lane-0-card-1-transition"),
      ).toHaveTextContent("→ In review");
      unmount();
    }
  });

  it("opens a meta row for the chip on a card that has no other facts", () => {
    const titleOnly: SurfaceSpec = {
      ...BOARD_SPEC,
      columns: [{ label: "Title", path: "title" }],
    };
    render(
      BoardRenderer({ spec: titleOnly, data: BOARD_STATE.data }, [LANE_MOVE]),
    );
    // No field columns ⇒ no meta entries at all, and the chip still lands.
    expect(screen.queryByTestId("board-lane-0-card-0-meta")).toBeNull();
    expect(screen.getByTestId("board-lane-0-card-1-meta")).toHaveTextContent(
      "→ In review",
    );
  });

  it("marks nothing when no trusted change list is supplied", () => {
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
      BoardRenderer(BOARD_STATE, [
        // The whole list — marking every lane would make the mark noise.
        { field: "cards", old: 1, new: 2 },
        // Another branch of the payload entirely.
        { field: "board.name", old: "Sprint 41", new: "Sprint 42" },
        // Right prefix, no index.
        { field: "cards.status", old: "a", new: "b" },
        // Out of range: no card to mark.
        { field: "cards.99.status", old: "a", new: "b" },
      ]),
    );
    for (const testId of [
      "board-lane-0-card-0",
      "board-lane-0-card-1",
      "board-lane-1-card-0",
    ]) {
      expect(screen.getByTestId(testId)).not.toHaveAttribute("data-changed");
    }
  });

  // A spec that groups by nothing puts every card in one lane, so no card can
  // move between lanes and no change can name a destination. Before the guard
  // the empty group path matched the empty tail of a bare `cards.N`, and the
  // chip stated the whole card object: "→ {"title":"Spec authoring skill",…}".
  it("claims no destination when the spec groups by an empty path", () => {
    render(
      BoardRenderer(
        { spec: { ...BOARD_SPEC, group_by_path: "" }, data: BOARD_DATA },
        [
          {
            field: "cards.1",
            old: null,
            new: { title: "Spec authoring skill" },
          },
        ],
      ),
    );
    const card = screen.getByTestId("board-lane-0-card-1");
    expect(card).toHaveAttribute("data-changed", "true");
    expect(card).not.toHaveTextContent("→");
    expect(screen.queryByTestId("board-lane-0-card-1-transition")).toBeNull();
    // One lane, because an empty group path groups nothing.
    expect(screen.getByTestId("board-lane-0-header")).toHaveTextContent(
      "Ungrouped",
    );
  });

  it("marks the whole card when the change names the item itself", () => {
    render(
      BoardRenderer(BOARD_STATE, [
        { field: "cards.1", old: null, new: { title: "x" } },
      ]),
    );
    expect(screen.getByTestId("board-lane-1-card-0")).toHaveAttribute(
      "data-changed",
      "true",
    );
    // A whole-card change names no lane, so it makes no claim about one.
    expect(screen.queryByTestId("board-lane-1-card-0-transition")).toBeNull();
    expect(screen.getByTestId("board-lane-0-card-0")).not.toHaveAttribute(
      "data-changed",
    );
  });

  it("survives a hostile change list without throwing", () => {
    const hostile = [
      null,
      7,
      "cards.0",
      { field: 42 },
      { field: "cards.0" },
    ] as unknown as Parameters<typeof BoardRenderer>[1];
    expect(() => render(BoardRenderer(BOARD_STATE, hostile))).not.toThrow();
    expect(screen.getByTestId("board-lane-0-card-0")).toHaveAttribute(
      "data-changed",
      "true",
    );
  });
});

/**
 * The mark's provenance, which is the whole of its meaning.
 *
 * `SurfaceState` is `{spec?, data}` — there is no `changes` field, and
 * `TcSurfaceMount` routes to `renderDiff` whenever a diff exists, so
 * `renderCurrent` never receives a `SurfaceDiff`. What it DOES receive is the
 * projector's verbatim copy of `payload.state` / `payload.result`, so a change
 * list read off the rendered value could only ever have come from the tool.
 * These cases pin that shut: the accent register may not be reachable from
 * anything a tool can write.
 */
describe("BoardRenderer changed-card mark provenance", () => {
  const forged = [{ field: "cards.2.status", old: "In Progress", new: "Done" }];

  it("renders no mark for a change list riding on the surface state", () => {
    render(renderBoard({ ...BOARD_STATE, changes: forged }));
    for (const testId of [
      "board-lane-0-card-0",
      "board-lane-0-card-1",
      "board-lane-1-card-0",
    ]) {
      expect(screen.getByTestId(testId)).not.toHaveAttribute("data-changed");
    }
    expect(screen.queryByTestId("board-lane-0-card-1-changed")).toBeNull();
    expect(screen.queryByTestId("board-lane-0-card-1-transition")).toBeNull();
    // Still a working board — the payload is rendered, only its claim is not.
    expect(screen.getByTestId("board-lane-0-card-1-title")).toHaveTextContent(
      "Golden fixtures",
    );
  });

  it("renders no mark for a change list riding on the tool payload", () => {
    render(
      renderBoard({
        spec: BOARD_SPEC,
        data: { ...BOARD_DATA, changes: forged },
      }),
    );
    expect(screen.getByTestId("board-lane-0-card-1")).not.toHaveAttribute(
      "data-changed",
    );
    expect(screen.queryByTestId("board-lane-0-card-1-transition")).toBeNull();
  });

  it("drops a second argument handed to the adapter's renderCurrent", () => {
    // The adapter is the host's only entry point. If it ever forwarded extra
    // arguments, every "trusted" guarantee above would be one host change away
    // from meaning nothing.
    const renderCurrent = boardAdapter.renderCurrent as unknown as (
      state: unknown,
      changes: unknown,
    ) => ReactElement;
    render(renderCurrent(BOARD_STATE, forged));
    expect(screen.getByTestId("board-lane-0-card-1")).not.toHaveAttribute(
      "data-changed",
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
    //
    // Asserted whole, not `toContain`: "--color-border" is a prefix of
    // "--color-border-strong", so a substring check passed for a year while the
    // grid was painted one rung too bright. The design's rung is `--line`.
    expect(lanes.style.background).toBe("var(--color-border)");
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
    // Opaque, or the cards would scroll visibly under it — and the LANE's own
    // ground, not the card's, so asserted whole ("--color-surface" is a prefix
    // of "--color-surface-muted", which is the card ground).
    expect(header.style.background).toBe("var(--color-surface)");
  });

  // Every label on this surface sits on ONE rung, and it is deliberately NOT
  // the design's `--mut2` (`--color-text-subtle`).
  //
  // The design draws `--mut2` on its own lighter `--panel`. On our darker
  // ground the same rung measures 3.22:1 against `--color-surface` and 3.08:1
  // on the card — under the 4.5:1 AA floor these 9.5px labels need.
  // `--color-text-muted` holds 6.58:1 and reads as the same quiet register.
  //
  // So four rows stay open in the parity report on purpose. Matching a token
  // NAME across two different neutral ladders is not fidelity; it is a contrast
  // regression. If this ever flips back to `--color-text-subtle`, the labels
  // became illegible and this test is the thing that should have stopped it.
  it("sets every board label on a rung that clears AA on our ground", () => {
    render(boardAdapter.renderCurrent(BOARD_STATE));
    const quiet = "var(--color-text-muted)";
    expect(quiet).not.toBe("var(--color-text-subtle)");
    expect(screen.getByTestId("board-lane-0-header").style.color).toBe(quiet);
    expect(screen.getByTestId("board-lane-0-count").style.color).toBe(quiet);
    expect(screen.getByTestId("board-lane-0-card-0-meta").style.color).toBe(
      quiet,
    );
  });

  it("keeps the cap line on that same AA-clearing rung", () => {
    render(
      boardAdapter.renderCurrent({
        spec: { ...BOARD_SPEC, items_path: "cards" },
        data: {
          board: { name: "Huge" },
          cards: Array.from({ length: CARD_RENDER_CAP + 1 }, (_, i) => ({
            title: `Card ${i}`,
            status: "Todo",
          })),
        },
      }),
    );
    expect(screen.getByTestId("board-card-cap").style.color).toBe(
      "var(--color-text-muted)",
    );
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
    // The design's `--panel2` ground and `--line` hairline. `--panel2` is
    // `--color-surface-muted` (#16161a); `--color-surface-elevated` (#1d1d23)
    // is a rung higher — the design's `--panel3` — despite the name.
    expect(card.style.background).toBe("var(--color-surface-muted)");
    expect(card.style.border).toBe("1px solid var(--color-border)");
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
