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

/**
 * The mark, spelled as the pixels that actually ship.
 *
 * `cardChromeStyle` composes these inline, so they are the whole of what a user
 * sees: the accent's hairline rung on the border, the accent at full strength as
 * a 2px inset bar. The plain card's values are stated too — a mark is only
 * legible as a mark because the unmarked card is visibly something else.
 *
 * Asserted WHOLE rather than with `toContain`, for the reason the lane-grid test
 * already records: "--color-border" is a prefix of "--color-border-strong", and
 * a substring check let a wrong rung ship on this very surface for a year.
 */
const MARKED_BORDER = "1px solid var(--color-accent-line, var(--color-accent))";
const MARKED_BAR = "inset 2px 0 0 var(--color-accent)";
const PLAIN_BORDER = "1px solid var(--color-border)";

/**
 * A card carrying the mark.
 *
 * The mark is CHROME plus the off-screen word a screen reader gets — never
 * `data-changed`. No stylesheet matches `[data-changed]` and no host reads it at
 * runtime, so a test that asserted only the attribute would still pass if the
 * mark rendered and the attribute went missing — and, worse in the other
 * direction, its absence would "prove" no mark on a card wearing a real accent
 * bar. That is not hypothetical: the provenance block below used to assert
 * exactly that, and it passed in full against a renderer that let tool output
 * paint the accent border and inset bar.
 *
 * The attribute is asserted here too, and only ever AFTER the chrome, so a
 * failure always reports the pixels rather than the label. Keeping it asserted
 * is what stops it drifting from what ships — and it does have one real
 * consumer, which is why it is not simply deleted: the design-parity harness
 * (`tools/design-parity/lib/render-live-surface-language.test.tsx`) counts
 * `data-changed="true"` in static markup, where no computed styles exist.
 */
function expectMarked(testId: string): void {
  const card = screen.getByTestId(testId);
  expect(card.style.border).toBe(MARKED_BORDER);
  expect(card.style.boxShadow).toBe(MARKED_BAR);
  expect(card).toHaveAttribute("data-changed", "true");
  expect(screen.getByTestId(`${testId}-changed`)).toHaveTextContent("Changed");
}

/**
 * A card carrying no mark — the assertion every provenance case turns on.
 *
 * States the plain chrome POSITIVELY: the card wears the neutral hairline and
 * has no bar at all. An absent attribute is not evidence of an absent mark, so a
 * forgery that lit the accent register fails here even if it never touched
 * `data-changed`.
 */
function expectUnmarked(testId: string): void {
  const card = screen.getByTestId(testId);
  expect(card.style.border).toBe(PLAIN_BORDER);
  expect(card.style.boxShadow).toBe("");
  expect(card).not.toHaveAttribute("data-changed");
  expect(screen.queryByTestId(`${testId}-changed`)).toBeNull();
  expect(screen.queryByTestId(`${testId}-transition`)).toBeNull();
}

/** Every card the fixture board paints, so a case that must mark nothing can
 * say so about the whole board rather than about a card it remembered. */
const FIXTURE_CARDS = [
  "board-lane-0-card-0",
  "board-lane-0-card-1",
  "board-lane-1-card-0",
] as const;

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

/**
 * A spec is untrusted input, and the renderer has to be total over it.
 *
 * `specFromState` admits a value on two checks — a string `archetype` and a
 * string `title_path` — and `applySurfaceEvent` merges `payload.state` verbatim
 * with no allow-list, so every other field arrives as whatever the tool wrote.
 * The `SurfaceSpec` annotation on it is a claim, not a guarantee, and four
 * shapes below used to take the whole surface down with a throw:
 *
 *  - a non-list `columns` — `columns is not iterable` from the rest destructure,
 *    and `.map is not a function` in the diff;
 *  - a null entry inside `columns` — `Cannot read properties of null`;
 *  - a non-string `label` on a column, or on the link — React's "Objects are not
 *    valid as a React child".
 *
 * Each case asserts what the board PAINTS, not merely that nothing threw: a
 * renderer that swallowed the spec and rendered a blank card would satisfy
 * `not.toThrow()` while being just as broken.
 */
describe("BoardRenderer hostile spec", () => {
  // `title_path` addresses a field on each CARD, so the fallback the renderer
  // uses when there is no usable title column paints something real and the
  // assertions can tell "fell back" from "rendered nothing".
  const BASE = {
    spec_version: 1,
    archetype: "board",
    source: { server: "s", tool: "t" },
    title_path: "title",
    items_path: "cards",
    group_by_path: "status",
  };
  const DATA = {
    url: "https://example.com/board",
    cards: [
      { title: "Wire renderers", status: "Todo", assignee: "Sarah" },
      { title: "Golden fixtures", status: "Todo", assignee: "Priya" },
    ],
  };
  const renderHostile = (spec: Record<string, unknown>): void => {
    render(renderBoard({ spec: { ...BASE, ...spec }, data: DATA }));
  };

  // Anything a spec spelled wrongly must be ABSENT on screen, never coerced:
  // `String({})` painted into a label is the failure mode where the renderer
  // survives and the surface still lies about the payload.
  const expectNoCoercedObject = (): void => {
    expect(screen.getByTestId("board-renderer")).not.toHaveTextContent(
      "[object Object]",
    );
  };

  it("paints the board when `columns` is not a list", () => {
    for (const columns of [5, { a: 1 }, "title", true, null]) {
      const { unmount } = render(
        renderBoard({ spec: { ...BASE, columns }, data: DATA }),
      );
      expect(screen.getByTestId("board-lane-0-header")).toHaveTextContent(
        "Todo",
      );
      expect(screen.getByTestId("board-lane-0-count")).toHaveTextContent("2");
      // No title column ⇒ the card title falls back to `title_path`, which is
      // the branch an absent `columns` already had.
      expect(screen.getByTestId("board-lane-0-card-0-title")).toHaveTextContent(
        "Wire renderers",
      );
      expect(screen.queryByTestId("board-lane-0-card-0-meta")).toBeNull();
      expectNoCoercedObject();
      unmount();
    }
  });

  // The throw itself: a hole in a FIELD slot is dereferenced for every card,
  // where a hole in the title slot never was — `Cannot read properties of null
  // (reading 'path')`, one malformed entry taking the whole surface down.
  it("skips an entry in a field slot that is not a column", () => {
    for (const hole of [null, undefined, 7, "status", ["status"]]) {
      const { unmount } = render(
        renderBoard({
          spec: {
            ...BASE,
            columns: [
              { label: "Title", path: "title" },
              hole,
              { label: "Assignee", path: "assignee" },
            ],
          },
          data: DATA,
        }),
      );
      expect(screen.getByTestId("board-lane-0-card-0-title")).toHaveTextContent(
        "Wire renderers",
      );
      const meta = screen.getByTestId("board-lane-0-card-0-meta");
      expect(meta).toHaveTextContent("Assignee");
      expect(meta).toHaveTextContent("Sarah");
      expectNoCoercedObject();
      unmount();
    }
  });

  // The reason a malformed entry becomes a HOLE rather than being compacted
  // away: the first column is the card title and the rest are fields, so
  // dropping entry 0 would silently promote "Status" from a meta fact to the
  // card's title — a wrong render, where an absent one was available.
  it("keeps a column in its slot when the entry before it is not a column", () => {
    renderHostile({ columns: [null, { label: "Status", path: "status" }] });
    expect(screen.getByTestId("board-lane-0-card-0-title")).toHaveTextContent(
      "Wire renderers",
    );
    const meta = screen.getByTestId("board-lane-0-card-0-meta");
    expect(meta).toHaveTextContent("Status");
    expect(meta).toHaveTextContent("Todo");
    expectNoCoercedObject();
  });

  it("drops a column whose path is not a string, keeping the rest", () => {
    renderHostile({
      columns: [
        { label: "Title", path: "title" },
        { label: "Owner", path: { a: 1 } },
        { label: "Assignee", path: "assignee" },
      ],
    });
    expect(screen.getByTestId("board-lane-0-card-0-title")).toHaveTextContent(
      "Wire renderers",
    );
    const meta = screen.getByTestId("board-lane-0-card-0-meta");
    expect(meta).not.toHaveTextContent("Owner");
    expect(meta).toHaveTextContent("Assignee");
    expect(meta).toHaveTextContent("Sarah");
    expectNoCoercedObject();
  });

  // The fact is real even when its NAME is not: the value survives, bare.
  it("states a column's value when its label is not a string", () => {
    renderHostile({
      columns: [
        { label: "Title", path: "title" },
        { label: { a: 1 }, path: "assignee" },
      ],
    });
    expect(screen.getByTestId("board-lane-0-card-0-meta")).toHaveTextContent(
      "Sarah",
    );
    expectNoCoercedObject();
  });

  it("shows an empty board when `items_path` is not a string", () => {
    renderHostile({ items_path: { a: 1 }, columns: [] });
    expect(screen.getByTestId("surface-empty")).toHaveTextContent(
      "No cards to display.",
    );
    expect(screen.getByTestId("surface-badge")).toHaveTextContent("0 cards");
  });

  it("groups nothing when `group_by_path` is not a string", () => {
    renderHostile({ group_by_path: 7, columns: [] });
    expect(screen.getByTestId("board-lane-0-header")).toHaveTextContent(
      "Ungrouped",
    );
    expect(screen.getByTestId("board-lane-0-count")).toHaveTextContent("2");
    expect(screen.queryByTestId("board-lane-1")).toBeNull();
  });

  // A string `link` used to paint an EMPTY inert row — a band of chrome stating
  // nothing — because `"…".label` and `"…".url_path` are both `undefined`.
  it("paints no link row when the spec's link is not an object", () => {
    for (const link of ["https://example.com/board", 5, true, ["x"]]) {
      const { unmount } = render(
        renderBoard({ spec: { ...BASE, columns: [], link }, data: DATA }),
      );
      expect(screen.queryByTestId("surface-link")).toBeNull();
      expect(screen.queryByTestId("surface-link-text")).toBeNull();
      unmount();
    }
  });

  it("falls back to the url as link text when the link's label is not a string", () => {
    renderHostile({ columns: [], link: { label: { a: 1 }, url_path: "url" } });
    const link = screen.getByTestId("surface-link");
    expect(link).toHaveAttribute("href", "https://example.com/board");
    expect(link).toHaveTextContent("https://example.com/board");
    expectNoCoercedObject();
  });

  // The sweep behind the named cases above. Every shape here either has no
  // usable title column or has one pointing at `title`, so ONE assertion holds
  // across all of them — and it is an assertion about painted text, because a
  // renderer that swallowed the spec and drew a blank card would pass a bare
  // `not.toThrow()` while being exactly as broken as one that threw.
  it("paints a card title for every shape a spec's fields can arrive in", () => {
    const shapes: readonly Record<string, unknown>[] = [
      { columns: [[{ label: "T", path: "title" }]] },
      { columns: [{ label: "T", path: "title", format: { a: 1 } }] },
      { columns: [{ label: "T", path: "title" }, "assignee"] },
      { columns: [{ label: "T", path: "title" }, 7] },
      { columns: [{ label: "T", path: "title" }, { label: "A" }] },
      { columns: [{ path: "title" }] },
      { columns: [{ label: "T", path: "title", align: { x: 1 } }] },
      // `title_path` is the one field `specFromState` does gate, but only as a
      // string — the empty one still has to reach the column fallback.
      { title_path: "", columns: [{ label: "T", path: "title" }] },
      { columns: [], link: [] },
      { columns: [], link: {} },
      { columns: [], link: { label: "Open" } },
      { columns: [], link: { url_path: 9 } },
    ];
    for (const shape of shapes) {
      const { unmount } = render(
        renderBoard({ spec: { ...BASE, ...shape }, data: DATA }),
      );
      expect(screen.getByTestId("board-lane-0-card-0-title")).toHaveTextContent(
        "Wire renderers",
      );
      expectNoCoercedObject();
      unmount();
    }
  });
});

describe("BoardDiffRenderer hostile spec", () => {
  const CHANGE = { field: "status", old: "In Progress", new: "Done" };
  const renderHostileDiff = (columns: unknown): (() => void) =>
    render(
      boardAdapter.renderDiff({
        spec: {
          spec_version: 1,
          archetype: "board",
          source: { server: "s", tool: "t" },
          title_path: "title",
          columns,
        },
        changes: [CHANGE],
      } as unknown as Parameters<typeof boardAdapter.renderDiff>[0]),
    ).unmount;

  it("names the changed field by its path when `columns` is not a list", () => {
    renderHostileDiff(5);
    const row = screen.getByTestId("field-status");
    // No column labels to draw on, so the field path is the name — the honest
    // fallback, and a real row rather than a swallowed one.
    expect(row).toHaveTextContent("status");
    expect(screen.getByTestId("field-status-next")).toHaveTextContent("Done");
    expect(screen.getByTestId("field-status-previous")).toHaveTextContent(
      "In Progress",
    );
  });

  it("keeps a column's label when a neighbouring entry is not a column", () => {
    // Both sides of the label column: the diff builds its label map by walking
    // every entry, so a hole anywhere in the list used to throw.
    for (const columns of [
      [null, { label: "Status", path: "status" }],
      [{ label: "Status", path: "status" }, null],
      [7, { label: "Status", path: "status" }, "x"],
    ]) {
      const unmount = renderHostileDiff(columns);
      expect(screen.getByTestId("field-status")).toHaveTextContent("Status");
      expect(screen.getByTestId("field-status-next")).toHaveTextContent("Done");
      unmount();
    }
  });

  it("names the field by its path when a column's label is not a string", () => {
    renderHostileDiff([{ label: { a: 1 }, path: "status" }]);
    const row = screen.getByTestId("field-status");
    expect(row).toHaveTextContent("status");
    expect(row).not.toHaveTextContent("[object Object]");
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
    expectMarked("board-lane-0-card-1");
    expect(screen.getByTestId("board-lane-0-card-1-title")).toHaveTextContent(
      "Golden fixtures",
    );
    expectUnmarked("board-lane-0-card-0");
    expectUnmarked("board-lane-1-card-0");
  });

  it("gives the marked card the accent hairline and the 2px inset bar", () => {
    render(BoardRenderer(BOARD_STATE, [FIELD_EDIT]));
    const marked = screen.getByTestId("board-lane-0-card-1");
    // The bar is the accent at full strength; the border is the accent's
    // hairline rung — NOT the board's identity hue, which stays on the kicker.
    expect(marked.style.boxShadow).toBe(MARKED_BAR);
    expect(marked.style.border).toBe(MARKED_BORDER);
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
    expectMarked("board-lane-0-card-1");
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
    FIXTURE_CARDS.forEach(expectUnmarked);
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
    FIXTURE_CARDS.forEach(expectUnmarked);
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
    expectMarked("board-lane-0-card-1");
    expect(screen.getByTestId("board-lane-0-card-1")).not.toHaveTextContent(
      "→",
    );
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
    expectMarked("board-lane-1-card-0");
    // A whole-card change names no lane, so it makes no claim about one.
    expect(screen.queryByTestId("board-lane-1-card-0-transition")).toBeNull();
    expectUnmarked("board-lane-0-card-0");
    expectUnmarked("board-lane-0-card-1");
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
    // The one well-formed entry still lands, so the render survived rather than
    // degrading to marking nothing.
    expectMarked("board-lane-0-card-0");
    expectUnmarked("board-lane-0-card-1");
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
 *
 * They assert the RENDERED CHROME, via {@link expectUnmarked}, and this is the
 * point of the whole block. The mark is an inline border and inset bar, and
 * nothing at runtime reads `data-changed`, so an attribute-only assertion is
 * blind to a forgery that paints one. That was measured before it was rewritten:
 * against a renderer that read the change list off the untrusted state and
 * painted the accent border and 2px bar — while emitting no attribute, no
 * off-screen word and no transition chip — the earlier versions of all three
 * cases below passed. A user would have seen a lit card; the suite saw nothing.
 * The forgery these tests exist to catch is a painted one.
 */
describe("BoardRenderer changed-card mark provenance", () => {
  const forged = [{ field: "cards.2.status", old: "In Progress", new: "Done" }];

  it("paints no mark for a change list riding on the surface state", () => {
    render(renderBoard({ ...BOARD_STATE, changes: forged }));
    // Every card wears the neutral hairline and carries no bar: the accent
    // register is untouched, not merely unlabelled.
    FIXTURE_CARDS.forEach(expectUnmarked);
    // Still a working board — the payload is rendered, only its claim is not.
    expect(screen.getByTestId("board-lane-0-card-1-title")).toHaveTextContent(
      "Golden fixtures",
    );
  });

  it("paints no mark for a change list riding on the tool payload", () => {
    render(
      renderBoard({
        spec: BOARD_SPEC,
        data: { ...BOARD_DATA, changes: forged },
      }),
    );
    FIXTURE_CARDS.forEach(expectUnmarked);
    expect(screen.getByTestId("board-lane-0-card-1-title")).toHaveTextContent(
      "Golden fixtures",
    );
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
    FIXTURE_CARDS.forEach(expectUnmarked);
  });

  // The guard on the guard. `expectUnmarked` is only worth anything if it can
  // FAIL on a painted mark, and an attribute-only assertion could not: the same
  // card, marked, has to trip every clause of it. Without this, a later edit
  // that quietly weakened the helper would leave three green provenance tests
  // proving nothing — which is the exact shape of the defect this block was
  // rewritten to remove.
  it("fails on a card that is actually marked", () => {
    render(BoardRenderer(BOARD_STATE, forged));
    expect(() => expectUnmarked("board-lane-0-card-1")).toThrow();
    expectMarked("board-lane-0-card-1");
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
