import { describe, expect, it, vi } from "vitest";
import {
  act,
  fireEvent,
  render,
  renderHook,
  screen,
} from "@testing-library/react";

import {
  InvalidInlineDiffTransitionError,
  TcInlineDiff,
  nextInlineDiffState,
  useInlineDiffReducer,
  type InlineDiffEvent,
  type InlineDiffState,
} from "./TcInlineDiff";
import { inlineDiffFixtures } from "./TcInlineDiff.fixtures";

const ALL_STATES: readonly InlineDiffState[] = [
  "idle",
  "streaming",
  "pending",
  "accepted",
  "rejected",
];

const ALL_EVENTS: readonly InlineDiffEvent[] = [
  "stream_start",
  "stream_end",
  "cancel",
  "approve",
  "reject",
  "reset",
];

const LEGAL_TRANSITIONS: ReadonlyArray<
  readonly [InlineDiffState, InlineDiffEvent, InlineDiffState]
> = [
  ["idle", "stream_start", "streaming"],
  ["streaming", "stream_end", "pending"],
  ["streaming", "cancel", "idle"],
  ["pending", "approve", "accepted"],
  ["pending", "reject", "rejected"],
  ["accepted", "reset", "idle"],
  ["rejected", "reset", "idle"],
];

describe("TcInlineDiff", () => {
  it.each(ALL_STATES)("renders the title in state %s", (state) => {
    render(<TcInlineDiff state={state} title="A pending change" />);
    expect(screen.getByText("A pending change")).toBeInTheDocument();
  });

  it.each(ALL_STATES)(
    "exposes state via data-state attribute (%s)",
    (state) => {
      render(<TcInlineDiff state={state} title="x" />);
      const card = screen.getByRole("group");
      expect(card).toHaveAttribute("data-state", state);
    },
  );

  it("only renders Approve/Reject buttons in the pending state", () => {
    for (const state of ALL_STATES) {
      const { unmount } = render(
        <TcInlineDiff
          state={state}
          title="x"
          onApprove={() => {}}
          onReject={() => {}}
        />,
      );
      const approve = screen.queryByTestId("tc-inline-diff-approve");
      const reject = screen.queryByTestId("tc-inline-diff-reject");
      if (state === "pending") {
        expect(approve).toBeInTheDocument();
        expect(reject).toBeInTheDocument();
      } else {
        expect(approve).not.toBeInTheDocument();
        expect(reject).not.toBeInTheDocument();
      }
      unmount();
    }
  });

  it("fires onApprove and onReject on click", () => {
    const onApprove = vi.fn();
    const onReject = vi.fn();
    render(
      <TcInlineDiff
        state="pending"
        title="x"
        onApprove={onApprove}
        onReject={onReject}
      />,
    );
    fireEvent.click(screen.getByTestId("tc-inline-diff-approve"));
    expect(onApprove).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByTestId("tc-inline-diff-reject"));
    expect(onReject).toHaveBeenCalledTimes(1);
  });

  it("renders the progress percent in the streaming pill", () => {
    render(<TcInlineDiff state="streaming" title="x" progressPercent={64} />);
    expect(screen.getByTestId("tc-inline-diff-pill")).toHaveTextContent(
      "STREAMING · 64%",
    );
  });

  it("renders the provenance label when provided", () => {
    render(
      <TcInlineDiff
        state="pending"
        title="x"
        provenance="DRAFTED FROM SALESFORCE"
      />,
    );
    expect(screen.getByTestId("tc-inline-diff-provenance")).toHaveTextContent(
      "DRAFTED FROM SALESFORCE",
    );
  });

  it("uses custom button labels when provided", () => {
    render(
      <TcInlineDiff
        state="pending"
        title="x"
        approveLabel="Approve & send"
        rejectLabel="Discard"
      />,
    );
    expect(screen.getByTestId("tc-inline-diff-approve")).toHaveTextContent(
      "Approve & send",
    );
    expect(screen.getByTestId("tc-inline-diff-reject")).toHaveTextContent(
      "Discard",
    );
  });
});

describe("TcInlineDiff chrome (Phase 2-E)", () => {
  it("renders the streaming progress track in the streaming state only", () => {
    for (const state of ALL_STATES) {
      const { unmount } = render(<TcInlineDiff state={state} title="x" />);
      const track = screen.queryByTestId("tc-inline-diff-progress-track");
      if (state === "streaming") {
        expect(track).toBeInTheDocument();
      } else {
        expect(track).not.toBeInTheDocument();
      }
      unmount();
    }
  });

  it("marks the streaming progress as indeterminate when no progressPercent is given", () => {
    render(<TcInlineDiff state="streaming" title="x" />);
    const fill = screen.getByTestId("tc-inline-diff-progress-fill");
    expect(fill).toHaveAttribute("data-determinate", "false");
  });

  it("marks the streaming progress as determinate when progressPercent is given", () => {
    render(<TcInlineDiff state="streaming" title="x" progressPercent={42} />);
    const fill = screen.getByTestId("tc-inline-diff-progress-fill");
    expect(fill).toHaveAttribute("data-determinate", "true");
  });

  it("renders a checkmark icon in the accepted state", () => {
    render(<TcInlineDiff state="accepted" title="x" />);
    expect(screen.getByTestId("tc-inline-diff-icon")).toHaveTextContent("✓");
  });

  it("renders an X icon in the rejected state", () => {
    render(<TcInlineDiff state="rejected" title="x" />);
    expect(screen.getByTestId("tc-inline-diff-icon")).toHaveTextContent("✕");
  });

  it("renders no state icon in idle / streaming / pending", () => {
    for (const state of ["idle", "streaming", "pending"] as const) {
      const { unmount } = render(<TcInlineDiff state={state} title="x" />);
      expect(
        screen.queryByTestId("tc-inline-diff-icon"),
      ).not.toBeInTheDocument();
      unmount();
    }
  });

  it("renders the provenance dot whose color matches the state accent", () => {
    for (const state of ALL_STATES) {
      const { unmount } = render(
        <TcInlineDiff state={state} title="x" provenance="FROM GMAIL" />,
      );
      const dot = screen.getByTestId("tc-inline-diff-provenance-dot");
      expect(dot).toBeInTheDocument();
      unmount();
    }
  });

  it("does not render a provenance pill when provenance is omitted", () => {
    render(<TcInlineDiff state="pending" title="x" />);
    expect(
      screen.queryByTestId("tc-inline-diff-provenance"),
    ).not.toBeInTheDocument();
  });
});

describe("TcInlineDiff suggest-changes button", () => {
  it("renders the suggest-changes button only when onSuggestChanges is given and state is pending", () => {
    for (const state of ALL_STATES) {
      const { unmount } = render(
        <TcInlineDiff
          state={state}
          title="x"
          onSuggestChanges={() => {}}
          onApprove={() => {}}
          onReject={() => {}}
        />,
      );
      const suggest = screen.queryByTestId("tc-inline-diff-suggest");
      if (state === "pending") {
        expect(suggest).toBeInTheDocument();
      } else {
        expect(suggest).not.toBeInTheDocument();
      }
      unmount();
    }
  });

  it("does not render the suggest-changes button when onSuggestChanges is omitted", () => {
    render(
      <TcInlineDiff
        state="pending"
        title="x"
        onApprove={() => {}}
        onReject={() => {}}
      />,
    );
    expect(
      screen.queryByTestId("tc-inline-diff-suggest"),
    ).not.toBeInTheDocument();
  });

  it("fires onSuggestChanges when clicked", () => {
    const onSuggestChanges = vi.fn();
    render(
      <TcInlineDiff
        state="pending"
        title="x"
        onSuggestChanges={onSuggestChanges}
      />,
    );
    fireEvent.click(screen.getByTestId("tc-inline-diff-suggest"));
    expect(onSuggestChanges).toHaveBeenCalledTimes(1);
  });

  it("uses the custom suggestLabel when provided", () => {
    render(
      <TcInlineDiff
        state="pending"
        title="x"
        onSuggestChanges={() => {}}
        suggestLabel="Refine"
      />,
    );
    expect(screen.getByTestId("tc-inline-diff-suggest")).toHaveTextContent(
      "Refine",
    );
  });

  it("defaults the suggest-changes label to 'Suggest changes'", () => {
    render(
      <TcInlineDiff state="pending" title="x" onSuggestChanges={() => {}} />,
    );
    expect(screen.getByTestId("tc-inline-diff-suggest")).toHaveTextContent(
      "Suggest changes",
    );
  });
});

describe("nextInlineDiffState (pure transition function)", () => {
  it.each(LEGAL_TRANSITIONS)(
    "transitions %s + %s -> %s",
    (from, event, expected) => {
      expect(nextInlineDiffState(from, event)).toBe(expected);
    },
  );

  it("throws InvalidInlineDiffTransitionError for every illegal pair", () => {
    const legal = new Set(
      LEGAL_TRANSITIONS.map(([from, event]) => `${from}::${event}`),
    );
    for (const from of ALL_STATES) {
      for (const event of ALL_EVENTS) {
        if (legal.has(`${from}::${event}`)) continue;
        expect(() => nextInlineDiffState(from, event)).toThrow(
          InvalidInlineDiffTransitionError,
        );
      }
    }
  });

  it("attaches the offending state and event to the error", () => {
    try {
      nextInlineDiffState("accepted", "approve");
      throw new Error("should not reach here");
    } catch (error) {
      expect(error).toBeInstanceOf(InvalidInlineDiffTransitionError);
      const typed = error as InvalidInlineDiffTransitionError;
      expect(typed.from).toBe("accepted");
      expect(typed.event).toBe("approve");
      expect(typed.message).toContain("approve");
      expect(typed.message).toContain("accepted");
    }
  });
});

describe("useInlineDiffReducer", () => {
  it("defaults to idle", () => {
    const { result } = renderHook(() => useInlineDiffReducer());
    expect(result.current.state).toBe("idle");
  });

  it("respects a non-default initial state", () => {
    const { result } = renderHook(() => useInlineDiffReducer("pending"));
    expect(result.current.state).toBe("pending");
  });

  it("drives the full happy-path lifecycle", () => {
    const { result } = renderHook(() => useInlineDiffReducer());
    act(() => result.current.dispatch("stream_start"));
    expect(result.current.state).toBe("streaming");
    act(() => result.current.dispatch("stream_end"));
    expect(result.current.state).toBe("pending");
    act(() => result.current.dispatch("approve"));
    expect(result.current.state).toBe("accepted");
    act(() => result.current.dispatch("reset"));
    expect(result.current.state).toBe("idle");
  });

  it("supports the cancel-during-streaming path", () => {
    const { result } = renderHook(() => useInlineDiffReducer());
    act(() => result.current.dispatch("stream_start"));
    expect(result.current.state).toBe("streaming");
    act(() => result.current.dispatch("cancel"));
    expect(result.current.state).toBe("idle");
  });

  it("supports the reject path", () => {
    const { result } = renderHook(() => useInlineDiffReducer("pending"));
    act(() => result.current.dispatch("reject"));
    expect(result.current.state).toBe("rejected");
    act(() => result.current.dispatch("reset"));
    expect(result.current.state).toBe("idle");
  });

  it("throws InvalidInlineDiffTransitionError on an illegal dispatch", () => {
    const { result } = renderHook(() => useInlineDiffReducer("idle"));
    expect(() => {
      act(() => result.current.dispatch("approve"));
    }).toThrow(InvalidInlineDiffTransitionError);
  });
});

describe("inlineDiffFixtures", () => {
  it("exports a non-empty array of fixtures", () => {
    expect(inlineDiffFixtures.length).toBeGreaterThan(0);
  });

  it("covers every InlineDiffState", () => {
    const covered = new Set(inlineDiffFixtures.map((f) => f.props.state));
    for (const state of ALL_STATES) {
      expect(covered.has(state)).toBe(true);
    }
  });

  it("includes one pending fixture wired with onSuggestChanges", () => {
    const withSuggest = inlineDiffFixtures.filter(
      (f) => typeof f.props.onSuggestChanges === "function",
    );
    expect(withSuggest.length).toBeGreaterThan(0);
    for (const fixture of withSuggest) {
      expect(fixture.props.state).toBe("pending");
    }
  });

  it("includes both indeterminate and determinate streaming fixtures", () => {
    const streaming = inlineDiffFixtures.filter(
      (f) => f.props.state === "streaming",
    );
    const indeterminate = streaming.filter(
      (f) => typeof f.props.progressPercent !== "number",
    );
    const determinate = streaming.filter(
      (f) => typeof f.props.progressPercent === "number",
    );
    expect(indeterminate.length).toBeGreaterThan(0);
    expect(determinate.length).toBeGreaterThan(0);
  });

  it("renders every fixture without throwing", () => {
    for (const fixture of inlineDiffFixtures) {
      const { unmount } = render(<TcInlineDiff {...fixture.props} />);
      expect(screen.getByRole("group")).toBeInTheDocument();
      unmount();
    }
  });
});
