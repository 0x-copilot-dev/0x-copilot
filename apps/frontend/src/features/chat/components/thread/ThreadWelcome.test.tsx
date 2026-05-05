import { act, render, screen, within } from "@testing-library/react";
import type { ComponentType, ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Render the assistant-ui suggestion primitives as transparent pass-throughs
// so the welcome can render without a full AssistantRuntimeProvider in the
// test. SuggestionByIndex invokes its components.Suggestion; Trigger becomes
// a plain <button> that emits its children.
vi.mock("@assistant-ui/react", () => ({
  ThreadPrimitive: {
    SuggestionByIndex: ({
      components: { Suggestion },
    }: {
      index: number;
      components: { Suggestion: ComponentType };
    }) => <Suggestion />,
  },
  SuggestionPrimitive: {
    Trigger: ({
      children,
      className,
      title,
    }: {
      children: ReactNode;
      className?: string;
      title?: string;
      send?: boolean;
    }) => (
      <button type="button" className={className} title={title}>
        {children}
      </button>
    ),
  },
}));

import { ThreadWelcome } from "./ThreadWelcome";

function at(hour: number): Date {
  return new Date(2026, 4, 5, hour, 0, 0, 0);
}

describe("ThreadWelcome", () => {
  it("renders the time-aware greeting (afternoon, named)", () => {
    render(<ThreadWelcome firstName="Sarah" now={at(14)} />);
    expect(screen.getByTestId("welcome-greeting")).toHaveTextContent(
      "Good afternoon, Sarah.",
    );
  });

  it("drops the name when none provided", () => {
    render(<ThreadWelcome now={at(8)} />);
    expect(screen.getByTestId("welcome-greeting")).toHaveTextContent(
      "Good morning.",
    );
  });

  it("renders four suggestion cards with category eyebrows in declared order", () => {
    render(<ThreadWelcome now={at(8)} />);
    const list = screen.getByLabelText("Suggested prompts");
    const items = within(list).getAllByRole("listitem");
    expect(items).toHaveLength(4);

    const eyebrows = within(list).getAllByText(
      /^(DRAFT|SUMMARIZE|FIND|COMPARE)$/,
    );
    expect(eyebrows.map((n) => n.textContent)).toEqual([
      "DRAFT",
      "SUMMARIZE",
      "FIND",
      "COMPARE",
    ]);
  });

  it("greeting is an h1 and there are four card buttons", () => {
    render(<ThreadWelcome now={at(8)} />);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(
      "Good morning.",
    );
    const list = screen.getByLabelText("Suggested prompts");
    expect(within(list).getAllByRole("button")).toHaveLength(4);
  });

  it("late-night greeting after 23:00", () => {
    render(<ThreadWelcome now={at(23)} firstName="Sarah" />);
    expect(screen.getByTestId("welcome-greeting")).toHaveTextContent(
      "Working late, Sarah.",
    );
  });

  // PR 3.5 / G7 — minute-tick transition. The internal `setInterval` polls
  // wall-clock time once a minute so an idle empty thread crosses bucket
  // boundaries (e.g. evening → late) without a refresh. We freeze the clock
  // at 22:59 and advance it past the boundary; the greeting must follow.
  describe("minute-tick transition", () => {
    beforeEach(() => {
      vi.useFakeTimers();
    });
    afterEach(() => {
      vi.useRealTimers();
    });

    it("re-evaluates the greeting once a minute as wall-clock advances", async () => {
      // 22:59 today — initial render renders "Good evening".
      const start = new Date(2026, 4, 5, 22, 59, 0, 0);
      vi.setSystemTime(start);
      render(<ThreadWelcome firstName="Sarah" now={start} />);
      expect(screen.getByTestId("welcome-greeting")).toHaveTextContent(
        "Good evening, Sarah.",
      );

      // Advance the system clock past 23:00 and let the interval fire.
      vi.setSystemTime(new Date(2026, 4, 5, 23, 0, 30, 0));
      await act(async () => {
        vi.advanceTimersByTime(60_000);
      });
      expect(screen.getByTestId("welcome-greeting")).toHaveTextContent(
        "Working late, Sarah.",
      );
    });
  });
});
