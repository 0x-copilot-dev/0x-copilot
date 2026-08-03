// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
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
