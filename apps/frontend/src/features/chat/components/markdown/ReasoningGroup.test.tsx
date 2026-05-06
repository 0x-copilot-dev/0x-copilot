// PR 3.6 — Atlas thought-process accordion render contract.
//
// Three things this guards against regressing:
//   1. <details> is closed by default (design decision: calm-by-default).
//   2. Summary label flips between "Thinking…" while running and
//      "Thought process" once complete.
//   3. Elapsed-time stamp is rendered when > 0 and hidden via :empty CSS
//      otherwise (we test the empty content, not the CSS rule).

import { describe, expect, it } from "vitest";
import { render } from "@testing-library/react";
import { ReasoningGroup } from "./ReasoningGroup";

describe("ReasoningGroup", () => {
  it("renders a closed <details> by default", () => {
    const { container } = render(
      <ReasoningGroup startIndex={0} endIndex={0}>
        <span>body</span>
      </ReasoningGroup>,
    );
    const details = container.querySelector("details");
    expect(details).not.toBeNull();
    expect(details?.hasAttribute("open")).toBe(false);
  });

  it("shows 'Thinking…' label while status is running", () => {
    const { container } = render(
      <ReasoningGroup
        startIndex={0}
        endIndex={0}
        status="running"
        elapsedSeconds={3}
      >
        <span>body</span>
      </ReasoningGroup>,
    );
    expect(
      container.querySelector(".aui-reasoning-group__label")?.textContent,
    ).toBe("Thinking…");
    expect(
      container
        .querySelector(".aui-reasoning-group")
        ?.getAttribute("data-status"),
    ).toBe("running");
    expect(
      container.querySelector(".aui-reasoning-group__time")?.textContent,
    ).toBe("3s");
  });

  it("shows 'Thought process' label and elapsed seconds when complete", () => {
    const { container } = render(
      <ReasoningGroup
        startIndex={0}
        endIndex={0}
        status="complete"
        elapsedSeconds={4}
      >
        <span>body</span>
      </ReasoningGroup>,
    );
    expect(
      container.querySelector(".aui-reasoning-group__label")?.textContent,
    ).toBe("Thought process");
    expect(
      container
        .querySelector(".aui-reasoning-group")
        ?.getAttribute("data-status"),
    ).toBe("complete");
    expect(
      container.querySelector(".aui-reasoning-group__time")?.textContent,
    ).toBe("4s");
  });

  it("renders an empty time slot when elapsedSeconds is 0 (no stamp)", () => {
    const { container } = render(
      <ReasoningGroup startIndex={0} endIndex={0} status="complete">
        <span>body</span>
      </ReasoningGroup>,
    );
    expect(
      container.querySelector(".aui-reasoning-group__time")?.textContent,
    ).toBe("");
  });
});
