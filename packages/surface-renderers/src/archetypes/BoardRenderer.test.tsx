import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import type { SurfaceState } from "../_shared/specTypes";
import { BOARD_STATE } from "./fixtures";
import { boardAdapter } from "./BoardRenderer";

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

  it("renders the fallback without throwing when the spec is absent", () => {
    const state: SurfaceState = { data: [{ title: "x" }] };
    expect(() => render(boardAdapter.renderCurrent(state))).not.toThrow();
    expect(screen.getByTestId("surface-preparing-hint")).toBeInTheDocument();
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
