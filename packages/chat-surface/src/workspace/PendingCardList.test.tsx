// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { PendingCard } from "../destinations/run/pendingCardsProjection";
import { PendingCardList } from "./PendingCardList";

function gateCard(over: Partial<PendingCard> = {}): PendingCard {
  return {
    itemKind: "gate",
    runId: "run_1",
    gateId: "g1",
    stageId: null,
    surfaceId: null,
    title: "to read ENG-1",
    connector: "linear",
    ledgerId: "ra7f·001",
    openedSeq: 1,
    rowsPending: null,
    rowsTotal: null,
    ...over,
  };
}

function stageCard(over: Partial<PendingCard> = {}): PendingCard {
  return {
    itemKind: "staged_write",
    runId: "run_1",
    gateId: null,
    stageId: "s1",
    surfaceId: "surf_1",
    title: "gmail · send",
    connector: "gmail",
    ledgerId: "ra7f·010",
    openedSeq: 10,
    rowsPending: null,
    rowsTotal: null,
    ...over,
  };
}

describe("PendingCardList", () => {
  it("renders one card per item with kind label, connector, ledger id", () => {
    render(
      <PendingCardList
        cards={[gateCard(), stageCard()]}
        onReview={() => undefined}
      />,
    );
    const cards = screen.getAllByTestId("pending-card-review");
    expect(cards).toHaveLength(2);
    const kinds = screen
      .getAllByTestId("pending-card-kind")
      .map((el) => el.textContent);
    expect(kinds).toEqual(["GATE", "HELD DRAFT"]);
    expect(screen.getByText("ra7f·001")).toBeInTheDocument();
    expect(screen.getByText("ra7f·010")).toBeInTheDocument();
  });

  it("labels a row-set with 'STAGED CHANGES' and a 'N of M waiting' pill", () => {
    render(
      <PendingCardList
        cards={[stageCard({ rowsPending: 5, rowsTotal: 8 })]}
        onReview={() => undefined}
      />,
    );
    expect(screen.getByTestId("pending-card-kind").textContent).toBe(
      "STAGED CHANGES",
    );
    expect(screen.getByTestId("pending-card-rows").textContent).toBe(
      "5 of 8 waiting",
    );
  });

  it("fires onReview with the exact card clicked", () => {
    const onReview = vi.fn();
    const card = stageCard();
    render(<PendingCardList cards={[card]} onReview={onReview} />);
    screen.getByTestId("pending-card-review").click();
    expect(onReview).toHaveBeenCalledWith(card);
  });

  it("renders a hostile title as plain text, never as HTML", () => {
    const hostile = "<img src=x onerror=alert(1)>";
    render(
      <PendingCardList
        cards={[gateCard({ title: hostile })]}
        onReview={() => undefined}
      />,
    );
    const title = screen.getByTestId("pending-card-title");
    expect(title.textContent).toBe(hostile);
    // No injected element — the string is a text node only.
    expect(title.querySelector("img")).toBeNull();
  });

  it("shows the empty copy when the queue is clear", () => {
    render(<PendingCardList cards={[]} onReview={() => undefined} />);
    expect(screen.getByTestId("pending-card-list-empty")).toBeInTheDocument();
  });
});
