// RunHeader — presentation tests (PR-3.5).

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { RunHeader } from "./RunHeader";

describe("RunHeader", () => {
  it("renders the ACTIVE RUN kicker and the goal", () => {
    render(
      <RunHeader
        goal="Ship the renewal batch"
        mode="studio"
        onModeChange={() => {}}
      />,
    );
    expect(screen.getByTestId("run-header-kicker").textContent).toBe(
      "ACTIVE RUN",
    );
    expect(screen.getByTestId("run-header-goal").textContent).toBe(
      "Ship the renewal batch",
    );
    expect(screen.getByTestId("run-header")).toHaveStyle({
      height: "38px",
      gap: "12px",
      padding: "0px 13px",
    });
    expect(screen.queryByTestId("run-header-window-dots")).toBeNull();

    // PRD-02 — the goal is VISIBLE, not clipped to 1x1. This is the whole
    // finding: the bar computed the goal and then rendered it into
    // `clip: rect(0,0,0,0)`.
    const goal = screen.getByTestId("run-header-goal");
    expect(getComputedStyle(goal).clip).not.toBe("rect(0px, 0px, 0px, 0px)");
    expect(getComputedStyle(goal).position).not.toBe("absolute");

    // D-2.3 — the mode is stated ONCE, by the control that owns it. The old bar
    // said it twice on one 38px row.
    expect(screen.getByTestId("run-header").textContent).not.toContain(
      "0xCopilot",
    );
  });

  it("falls back to idle copy when the goal is null/empty (never a blank h2, and the kicker never claims a run)", () => {
    const { rerender } = render(
      <RunHeader goal={null} mode="studio" onModeChange={() => {}} />,
    );
    // The eyebrow must NOT say "ACTIVE RUN" with no run, and the goal line is a
    // standby posture — not a duplicate of the empty-state card's copy.
    expect(screen.getByTestId("run-header-kicker").textContent).toBe("STANDBY");
    expect(screen.getByTestId("run-header-goal").textContent).toBe(
      "Standing by",
    );
    rerender(<RunHeader goal="   " mode="studio" onModeChange={() => {}} />);
    expect(screen.getByTestId("run-header-kicker").textContent).toBe("STANDBY");
    expect(screen.getByTestId("run-header-goal").textContent).toBe(
      "Standing by",
    );
  });

  it("renders a two-tab Studio/Focus segmented control reflecting the mode", () => {
    render(<RunHeader goal="G" mode="focus" onModeChange={() => {}} />);
    const tablist = screen.getByTestId("run-mode-switcher");
    expect(tablist.getAttribute("role")).toBe("tablist");
    expect(screen.getAllByRole("tab").map((tab) => tab.textContent)).toEqual([
      "Focus",
      "Studio",
    ]);
    const studio = screen.getByTestId("run-mode-studio");
    const focus = screen.getByTestId("run-mode-focus");
    expect(studio.getAttribute("aria-selected")).toBe("false");
    expect(focus.getAttribute("aria-selected")).toBe("true");
    // Roving tabindex: only the selected tab is in the tab order.
    expect(studio.getAttribute("tabindex")).toBe("-1");
    expect(focus.getAttribute("tabindex")).toBe("0");
  });

  it("fires onModeChange when a segment is clicked", () => {
    const onModeChange = vi.fn();
    render(<RunHeader goal="G" mode="studio" onModeChange={onModeChange} />);
    fireEvent.click(screen.getByTestId("run-mode-focus"));
    expect(onModeChange).toHaveBeenCalledWith("focus");
  });

  it("cycles modes with ArrowLeft/ArrowRight over the two values", () => {
    const onModeChange = vi.fn();
    render(<RunHeader goal="G" mode="studio" onModeChange={onModeChange} />);
    const tablist = screen.getByTestId("run-mode-switcher");
    fireEvent.keyDown(tablist, { key: "ArrowRight" });
    expect(onModeChange).toHaveBeenLastCalledWith("focus");
    // Wraps back to studio from focus going right.
    onModeChange.mockClear();
    render(<RunHeader goal="G" mode="focus" onModeChange={onModeChange} />);
    fireEvent.keyDown(screen.getAllByTestId("run-mode-switcher")[1], {
      key: "ArrowRight",
    });
    expect(onModeChange).toHaveBeenLastCalledWith("studio");
  });

  it("renders an optional status node beside the goal", () => {
    render(
      <RunHeader
        goal="G"
        mode="studio"
        onModeChange={() => {}}
        status={<span data-testid="probe">working</span>}
      />,
    );
    expect(screen.getByTestId("run-header-status")).not.toBeNull();
    expect(screen.getByTestId("probe").textContent).toBe("working");
  });

  // PRD-02 FR-2.7 — the goal absorbs the row; nothing else may shrink.
  it("gives the goal the flexible track and pins the status + mode clusters", () => {
    render(
      <RunHeader
        goal="A deliberately long run goal that must not move the mode control"
        mode="studio"
        onModeChange={() => {}}
        runStatus="running"
        status={<span data-testid="probe">viewing 11:43</span>}
      />,
    );

    expect(screen.queryByTestId("run-header-window-dots")).toBeNull();

    // The goal is the ONLY thing that gives way, and it ellipsises rather than
    // wrapping (a wrapping title would break the 38px row, FR-2.8).
    expect(screen.getByTestId("run-header-goal")).toHaveStyle({
      flex: "1",
      minWidth: "0px",
      overflow: "hidden",
      textOverflow: "ellipsis",
      whiteSpace: "nowrap",
    });

    // Everything to its right is fixed — a long goal cannot push the mode
    // control off the row.
    expect(screen.getByTestId("run-header-status")).toHaveStyle({
      flex: "none",
    });
    expect(screen.getByTestId("run-mode-switcher")).toHaveStyle({
      flexShrink: "0",
      marginLeft: "auto",
    });
  });

  // PRD-02 D-2.6 — the accessible name must not repeat what is now visible.
  it("announces the goal once, not twice", () => {
    render(
      <RunHeader
        goal="Ship the renewal batch"
        mode="studio"
        onModeChange={() => {}}
      />,
    );
    const occurrences = screen.getAllByText("Ship the renewal batch");
    expect(occurrences).toHaveLength(1);
    // The kicker has no visible equivalent now, so it stays hidden-but-present.
    const kicker = screen.getByTestId("run-header-kicker");
    expect(kicker.textContent).toBe("ACTIVE RUN");
    expect(getComputedStyle(kicker).clip).toBe("rect(0px, 0px, 0px, 0px)");
  });

  // The `● working` / `● WAITING` pulse was REMOVED from the header (it was one
  // of three status chips there). The canvas already states the run's condition
  // in words a person can act on, each tool card carries its own status, and the
  // composer reflects in-flight — the chrome copy was the least specific of the
  // four and the first thing a user asked to lose.
  it("renders no status pulse, in any run state", () => {
    for (const runStatus of [
      "running",
      "waiting_for_approval",
      "completed",
    ] as const) {
      const { unmount } = render(
        <RunHeader
          goal="G"
          mode="studio"
          onModeChange={() => {}}
          runStatus={runStatus}
        />,
      );
      expect(screen.queryByTestId("run-header-status-pulse")).toBeNull();
      expect(screen.queryByTestId("run-header-pulse-dot")).toBeNull();
      unmount();
    }
  });
});
