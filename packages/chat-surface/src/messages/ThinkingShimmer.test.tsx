// @vitest-environment jsdom
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import {
  ThinkingBlock,
  ThinkingShimmer,
  reasoningTitle,
} from "./ThinkingShimmer";

/** The visible word, excluding the scoped <style> the host element carries. */
function label(host: HTMLElement): string {
  return host.querySelector(".cs-thinking__label")?.textContent ?? "";
}

describe("ThinkingShimmer", () => {
  it("renders the bare word when the model has told us nothing yet", () => {
    render(<ThinkingShimmer />);
    // The label element, not the host: the host also carries the scoped
    // <style>, whose CSS would otherwise read as transcript text.
    expect(label(screen.getByTestId("cs-thinking"))).toBe("Thinking");
  });

  it("appends a provider-authored detail when there is one", () => {
    render(<ThinkingShimmer detail="Calculating probabilities" />);
    expect(label(screen.getByTestId("cs-thinking"))).toBe(
      "Thinking: Calculating probabilities",
    );
  });

  it("announces politely — it appears with no user action", () => {
    render(<ThinkingShimmer />);
    expect(screen.getByTestId("cs-thinking")).toHaveAttribute(
      "aria-live",
      "polite",
    );
  });

  it("ships the reduced-motion fallback, not just the animation", () => {
    // A shimmer that simply vanishes under prefers-reduced-motion stops
    // signalling that anything is happening, which is the whole point.
    const { container } = render(<ThinkingShimmer />);
    const css = container.querySelector("style")?.textContent ?? "";
    expect(css).toContain("prefers-reduced-motion");
    expect(css).toContain("animation: none");
  });
});

describe("reasoningTitle", () => {
  it("lifts the bold header OpenAI puts on a reasoning summary", () => {
    expect(reasoningTitle("**Calculating probabilities**\n\nI need to…")).toBe(
      "Calculating probabilities",
    );
  });

  it("returns null rather than inventing one from the first sentence", () => {
    // Anthropic summaries carry no header. Manufacturing a title would put
    // words in the model's mouth.
    expect(reasoningTitle("I'm checking what math.isqrt does.")).toBeNull();
  });

  it("ignores bold that is not a leading header", () => {
    expect(reasoningTitle("first **bold** mid-sentence")).toBeNull();
  });
});

describe("ThinkingBlock", () => {
  it("is collapsed by default so thinking never pushes the answer down", () => {
    render(
      <ThinkingBlock text="considering" running>
        <span>body</span>
      </ThinkingBlock>,
    );
    const details = screen.getByTestId(
      "cs-thinking-block",
    ) as HTMLDetailsElement;
    expect(details.open).toBe(false);
  });

  it("shimmers while running", () => {
    render(<ThinkingBlock text="considering" running />);
    expect(screen.getByTestId("cs-thinking")).toBeInTheDocument();
    expect(screen.getByTestId("cs-thinking-block")).toHaveAttribute(
      "data-status",
      "running",
    );
  });

  it("stops shimmering once settled and states the elapsed span", () => {
    render(<ThinkingBlock text="done" running={false} elapsedSeconds={7} />);
    expect(screen.queryByTestId("cs-thinking")).toBeNull();
    expect(screen.getByText("Thought for 7s")).toBeInTheDocument();
  });

  it("falls back to a plain label when no elapsed time is known", () => {
    render(<ThinkingBlock text="done" running={false} />);
    expect(screen.getByText("Thought process")).toBeInTheDocument();
  });

  it("shows the provider's title in the header while running", () => {
    render(<ThinkingBlock text={"**Weighing options**\n\nbody"} running />);
    expect(label(screen.getByTestId("cs-thinking"))).toBe(
      "Thinking: Weighing options",
    );
  });
});

/**
 * The work the model did while thinking now lives INSIDE the thought. Folding
 * it in is only safe while these hold: the header always says how much is under
 * it, and the row opens itself whenever what it holds is live or broken.
 */
describe("ThinkingBlock · folded activity", () => {
  const cards = [<li key="a">step a</li>, <li key="b">step b</li>];
  const block = (): HTMLDetailsElement =>
    screen.getByTestId("cs-thinking-block") as HTMLDetailsElement;

  it("renders the cards it was given, inside its own body", () => {
    render(
      <ThinkingBlock text="t" running={false} activity={cards} stepCount={2}>
        <span>prose</span>
      </ThinkingBlock>,
    );
    const list = screen.getByTestId("cs-thinking-block-activity");
    expect(list.children).toHaveLength(2);
    expect(block().contains(list)).toBe(true);
  });

  it("states the step count — collapsing over silent work is the failure mode", () => {
    render(
      <ThinkingBlock text="t" running={false} activity={cards} stepCount={2} />,
    );
    expect(screen.getByTestId("cs-thinking-block-steps")).toHaveTextContent(
      "· 2 steps",
    );
  });

  it("says `step` for one, not `1 steps`", () => {
    render(
      <ThinkingBlock
        text="t"
        running={false}
        activity={[cards[0]]}
        stepCount={1}
      />,
    );
    expect(screen.getByTestId("cs-thinking-block-steps")).toHaveTextContent(
      "· 1 step",
    );
  });

  it("says nothing about steps when it holds none", () => {
    render(<ThinkingBlock text="t" running={false} />);
    expect(screen.queryByTestId("cs-thinking-block-steps")).toBeNull();
  });

  // The two rules inherited from ToolRunGroup (D-3.2 / D-3.5). Without them,
  // absorbing the cards would trade a stacked label for a hidden failure.
  it("opens itself while a folded step is still running", () => {
    render(
      <ThinkingBlock
        text="t"
        running
        activity={cards}
        stepCount={2}
        activityRunning
      />,
    );
    expect(block().open).toBe(true);
  });

  it("opens itself when a folded step failed", () => {
    render(
      <ThinkingBlock
        text="t"
        running={false}
        activity={cards}
        stepCount={2}
        failedCount={1}
      />,
    );
    expect(block().open).toBe(true);
    expect(screen.getByTestId("cs-thinking-block-failed")).toHaveTextContent(
      "· 1 failed",
    );
  });

  it("stays collapsed when every folded step settled cleanly", () => {
    // Prose alone must never expand — that is the rule this block exists for.
    render(
      <ThinkingBlock text="t" running={false} activity={cards} stepCount={2} />,
    );
    expect(block().open).toBe(false);
  });

  it("does not auto-collapse a row the reader has touched", () => {
    // The reader opened it to watch the steps; the last one settling must not
    // snap it shut under them. Driven from the KEYBOARD because jsdom
    // implements summary activation — a click on an already-open row closes
    // it, which is the reader asking for exactly that and not the case here.
    const { rerender } = render(
      <ThinkingBlock
        text="t"
        running
        activity={cards}
        stepCount={2}
        activityRunning
      />,
    );
    expect(block().open).toBe(true);
    fireEvent.keyDown(block().querySelector("summary") as HTMLElement, {
      key: "Enter",
    });
    rerender(
      <ThinkingBlock
        text="t"
        running={false}
        activity={cards}
        stepCount={2}
        activityRunning={false}
      />,
    );
    expect(block().open).toBe(true);
    expect(block()).toHaveAttribute("data-pinned", "true");
  });

  it("auto-collapses a settled row the reader never touched", () => {
    // The other half of the same rule: untouched rows still tidy themselves
    // away once the work is done, so a finished turn reads as an answer.
    const { rerender } = render(
      <ThinkingBlock
        text="t"
        running
        activity={cards}
        stepCount={2}
        activityRunning
      />,
    );
    expect(block().open).toBe(true);
    rerender(
      <ThinkingBlock
        text="t"
        running={false}
        activity={cards}
        stepCount={2}
        activityRunning={false}
      />,
    );
    expect(block().open).toBe(false);
  });
});

describe("ThinkingBlock — the digest", () => {
  const DIGEST = "Searched the web · Dispatched 1 subagent";
  const steps = (): HTMLElement =>
    screen.getByTestId("cs-thinking-block-steps");

  it("names what the folded cards did instead of counting them", () => {
    // The live capture behind this: a settled turn read
    // "Thought process · 2 steps" and nothing recorded the search or the
    // subagent. A number says work exists; it takes a noun to say what it was.
    render(
      <ThinkingBlock text="" running={false} stepCount={2} digest={DIGEST} />,
    );
    expect(steps()).toHaveTextContent(`· ${DIGEST}`);
    expect(screen.getByTestId("cs-thinking-block")).toHaveAttribute(
      "data-steps",
      "2",
    );
  });

  it("falls back to the count when the caller cannot describe its cards", () => {
    render(<ThinkingBlock text="" running={false} stepCount={2} digest="  " />);
    expect(steps()).toHaveTextContent("· 2 steps");
  });

  it("says nothing about steps when there are none, digest or not", () => {
    render(
      <ThinkingBlock text="" running={false} stepCount={0} digest={DIGEST} />,
    );
    expect(screen.queryByTestId("cs-thinking-block-steps")).toBeNull();
  });

  it("makes the digest the ONE item that yields in a narrow column", () => {
    // jsdom runs no layout, so this asserts the shrink CONTRACT that decides
    // clipping order. The digest is the only unbounded string in the header; if
    // it were `flex: none`, a ~335px Studio column would push the chevron — the
    // only affordance this control has — off the edge instead.
    render(
      <ThinkingBlock text="" running={false} stepCount={2} digest={DIGEST} />,
    );
    expect(steps()).toHaveStyle({
      flex: "0 1 auto",
      minWidth: "0",
      overflow: "hidden",
      textOverflow: "ellipsis",
      whiteSpace: "nowrap",
    });
    expect(steps()).toHaveAttribute("title", DIGEST);
    const summary = screen
      .getByTestId("cs-thinking-block")
      .querySelector("summary") as HTMLElement;
    expect(summary).toHaveStyle({ maxWidth: "100%" });
  });
});
