// @vitest-environment jsdom
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { PendingWorkCardV2 } from "../destinations/run/pendingWorkV2Projection";
import { PendingWorkV2List } from "./PendingWorkV2List";

function card(over: Partial<PendingWorkCardV2> = {}): PendingWorkCardV2 {
  return {
    runId: "run_a",
    subjectKind: "effect",
    subjectId: "stage_a",
    status: "held",
    openedSeq: 2,
    latestSeq: 2,
    ...over,
  };
}

describe("PendingWorkV2List", () => {
  it("renders controlled labels only and never exposes opaque target data", () => {
    const hostileId = '<img src=x onerror="alert(1)">';
    const { container } = render(
      <PendingWorkV2List
        cards={[card({ runId: "run_secret", subjectId: hostileId })]}
        loading={false}
        partial={false}
        stale={false}
        hasMore={false}
        onReview={() => undefined}
        onLoadMore={() => undefined}
      />,
    );

    expect(screen.getByTestId("pending-work-v2-kind").textContent).toBe(
      "PROPOSED CHANGE",
    );
    expect(screen.getByTestId("pending-work-v2-status").textContent).toBe(
      "Held for review",
    );
    expect(container.textContent).not.toContain("run_secret");
    expect(container.textContent).not.toContain(hostileId);
    expect(container.querySelector("img")).toBeNull();
  });

  it("preserves the exact run/subject target in the Review callback", () => {
    const onReview = vi.fn();
    const target = card({ subjectKind: "gate", subjectId: "workspace:op_9" });
    render(
      <PendingWorkV2List
        cards={[target]}
        loading={false}
        partial={false}
        stale={false}
        hasMore={false}
        onReview={onReview}
        onLoadMore={() => undefined}
      />,
    );

    fireEvent.click(screen.getByTestId("pending-work-v2-review"));
    expect(onReview).toHaveBeenCalledWith(target);
  });

  it("provides an explicit bounded page affordance", () => {
    const onLoadMore = vi.fn();
    render(
      <PendingWorkV2List
        cards={[card()]}
        loading={false}
        partial={false}
        stale={false}
        hasMore={true}
        onReview={() => undefined}
        onLoadMore={onLoadMore}
      />,
    );
    fireEvent.click(screen.getByTestId("pending-work-v2-load-more"));
    expect(onLoadMore).toHaveBeenCalledTimes(1);
  });

  it("renders only controlled honesty copy for partial or stale data", () => {
    const { container } = render(
      <PendingWorkV2List
        cards={[]}
        loading={false}
        partial
        stale
        hasMore={false}
        onReview={() => undefined}
        onLoadMore={() => undefined}
      />,
    );

    expect(screen.getByTestId("pending-work-v2-partial")).toHaveTextContent(
      "Some runtime work couldn't be loaded.",
    );
    expect(screen.getByTestId("pending-work-v2-stale")).toHaveTextContent(
      "Runtime work may be out of date.",
    );
    expect(container.textContent).not.toContain("run_");
  });
});
