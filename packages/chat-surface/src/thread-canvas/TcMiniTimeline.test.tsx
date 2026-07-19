import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import { TcMiniTimeline } from "./TcMiniTimeline";
import type { TimelineBead } from "./eventProjector";

const sampleBeads: readonly TimelineBead[] = [
  {
    id: "evt-0",
    sequenceNo: 0,
    atMs: 1_700_000_000_000,
    lane: "email",
    title: "Drafted email",
    pending: false,
  },
  {
    id: "evt-1",
    sequenceNo: 1,
    atMs: 1_700_000_001_000,
    lane: "sheet",
    title: "Wrote sheet row",
    pending: false,
  },
  {
    id: "evt-2",
    sequenceNo: 2,
    atMs: 1_700_000_002_000,
    lane: "email",
    title: "Pending approval",
    pending: true,
  },
];

describe("TcMiniTimeline", () => {
  it("renders one button per bead", () => {
    render(
      <TcMiniTimeline
        beads={sampleBeads}
        scrubbedTo={null}
        onScrub={() => {}}
        onSnapToNow={() => {}}
      />,
    );
    expect(
      screen.getByTestId("tc-mini-timeline-bead-evt-0"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("tc-mini-timeline-bead-evt-1"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("tc-mini-timeline-bead-evt-2"),
    ).toBeInTheDocument();
  });

  it("renders the empty state when there are no beads (receded: no Live pill, out of tab order)", () => {
    render(
      <TcMiniTimeline
        beads={[]}
        scrubbedTo={null}
        onScrub={() => {}}
        onSnapToNow={() => {}}
      />,
    );
    expect(screen.getByTestId("tc-mini-timeline-empty")).toBeInTheDocument();
    // Progressive disclosure: an empty timeline is permanently live, so the
    // Live/Now pill is withheld and the strip drops out of the tab order.
    expect(
      screen.queryByTestId("tc-mini-timeline-now"),
    ).not.toBeInTheDocument();
    const strip = screen.getByTestId("tc-mini-timeline");
    expect(strip).toHaveAttribute("data-empty", "true");
    expect(strip).toHaveAttribute("tabindex", "-1");
  });

  it("emits onScrub with the bead's sequence_no on click", () => {
    const onScrub = vi.fn();
    render(
      <TcMiniTimeline
        beads={sampleBeads}
        scrubbedTo={null}
        onScrub={onScrub}
        onSnapToNow={() => {}}
      />,
    );
    fireEvent.click(screen.getByTestId("tc-mini-timeline-bead-evt-1"));
    expect(onScrub).toHaveBeenCalledWith(1);
  });

  it("marks the selected bead via aria-pressed and data-selected", () => {
    render(
      <TcMiniTimeline
        beads={sampleBeads}
        scrubbedTo={1}
        onScrub={() => {}}
        onSnapToNow={() => {}}
      />,
    );
    const bead = screen.getByTestId("tc-mini-timeline-bead-evt-1");
    expect(bead).toHaveAttribute("aria-pressed", "true");
    expect(bead).toHaveAttribute("data-selected", "true");
  });

  it("flags pending beads via data-pending", () => {
    render(
      <TcMiniTimeline
        beads={sampleBeads}
        scrubbedTo={null}
        onScrub={() => {}}
        onSnapToNow={() => {}}
      />,
    );
    expect(screen.getByTestId("tc-mini-timeline-bead-evt-2")).toHaveAttribute(
      "data-pending",
      "true",
    );
    expect(screen.getByTestId("tc-mini-timeline-bead-evt-0")).toHaveAttribute(
      "data-pending",
      "false",
    );
  });

  it("renders the Now pill as live when scrubbedTo is null", () => {
    render(
      <TcMiniTimeline
        beads={sampleBeads}
        scrubbedTo={null}
        onScrub={() => {}}
        onSnapToNow={() => {}}
      />,
    );
    expect(screen.getByTestId("tc-mini-timeline-now")).toHaveTextContent(
      "Live",
    );
    expect(screen.getByTestId("tc-mini-timeline-now")).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  it("renders the Now pill as ↩ Now when scrubbed off-live", () => {
    render(
      <TcMiniTimeline
        beads={sampleBeads}
        scrubbedTo={1}
        onScrub={() => {}}
        onSnapToNow={() => {}}
      />,
    );
    expect(screen.getByTestId("tc-mini-timeline-now")).toHaveTextContent(/Now/);
    expect(screen.getByTestId("tc-mini-timeline-now")).toHaveAttribute(
      "aria-pressed",
      "false",
    );
  });

  it("calls onSnapToNow when the Now pill is clicked", () => {
    const onSnapToNow = vi.fn();
    render(
      <TcMiniTimeline
        beads={sampleBeads}
        scrubbedTo={1}
        onScrub={() => {}}
        onSnapToNow={onSnapToNow}
      />,
    );
    fireEvent.click(screen.getByTestId("tc-mini-timeline-now"));
    expect(onSnapToNow).toHaveBeenCalledTimes(1);
  });

  it("calls onExpand when the expand chevron is clicked", () => {
    const onExpand = vi.fn();
    render(
      <TcMiniTimeline
        beads={sampleBeads}
        scrubbedTo={null}
        onScrub={() => {}}
        onSnapToNow={() => {}}
        onExpand={onExpand}
      />,
    );
    fireEvent.click(screen.getByTestId("tc-mini-timeline-expand"));
    expect(onExpand).toHaveBeenCalledTimes(1);
  });

  it("does not render expand chevron when onExpand is omitted", () => {
    render(
      <TcMiniTimeline
        beads={sampleBeads}
        scrubbedTo={null}
        onScrub={() => {}}
        onSnapToNow={() => {}}
      />,
    );
    expect(
      screen.queryByTestId("tc-mini-timeline-expand"),
    ).not.toBeInTheDocument();
  });

  it("ArrowLeft scrubs to the previous bead", () => {
    const onScrub = vi.fn();
    render(
      <TcMiniTimeline
        beads={sampleBeads}
        scrubbedTo={2}
        onScrub={onScrub}
        onSnapToNow={() => {}}
      />,
    );
    fireEvent.keyDown(screen.getByTestId("tc-mini-timeline"), {
      key: "ArrowLeft",
    });
    expect(onScrub).toHaveBeenCalledWith(1);
  });

  it("ArrowRight from the last bead snaps to now", () => {
    const onScrub = vi.fn();
    const onSnapToNow = vi.fn();
    render(
      <TcMiniTimeline
        beads={sampleBeads}
        scrubbedTo={2}
        onScrub={onScrub}
        onSnapToNow={onSnapToNow}
      />,
    );
    fireEvent.keyDown(screen.getByTestId("tc-mini-timeline"), {
      key: "ArrowRight",
    });
    expect(onSnapToNow).toHaveBeenCalledTimes(1);
    expect(onScrub).not.toHaveBeenCalled();
  });

  it("Escape snaps to now", () => {
    const onSnapToNow = vi.fn();
    render(
      <TcMiniTimeline
        beads={sampleBeads}
        scrubbedTo={1}
        onScrub={() => {}}
        onSnapToNow={onSnapToNow}
      />,
    );
    fireEvent.keyDown(screen.getByTestId("tc-mini-timeline"), {
      key: "Escape",
    });
    expect(onSnapToNow).toHaveBeenCalledTimes(1);
  });

  // === PR-3.7 (FR-3.14) — ⌘←/⌘→ step + ⌘L snap-to-now ===

  it("⌘← steps to the previous bead (Meta-modified arrow)", () => {
    const onScrub = vi.fn();
    render(
      <TcMiniTimeline
        beads={sampleBeads}
        scrubbedTo={2}
        onScrub={onScrub}
        onSnapToNow={() => {}}
      />,
    );
    fireEvent.keyDown(screen.getByTestId("tc-mini-timeline"), {
      key: "ArrowLeft",
      metaKey: true,
    });
    expect(onScrub).toHaveBeenCalledWith(1);
  });

  it("⌘→ from the last bead snaps to now", () => {
    const onScrub = vi.fn();
    const onSnapToNow = vi.fn();
    render(
      <TcMiniTimeline
        beads={sampleBeads}
        scrubbedTo={2}
        onScrub={onScrub}
        onSnapToNow={onSnapToNow}
      />,
    );
    fireEvent.keyDown(screen.getByTestId("tc-mini-timeline"), {
      key: "ArrowRight",
      metaKey: true,
    });
    expect(onSnapToNow).toHaveBeenCalledTimes(1);
    expect(onScrub).not.toHaveBeenCalled();
  });

  it("⌘L snaps to now", () => {
    const onSnapToNow = vi.fn();
    render(
      <TcMiniTimeline
        beads={sampleBeads}
        scrubbedTo={1}
        onScrub={() => {}}
        onSnapToNow={onSnapToNow}
      />,
    );
    fireEvent.keyDown(screen.getByTestId("tc-mini-timeline"), {
      key: "l",
      metaKey: true,
    });
    expect(onSnapToNow).toHaveBeenCalledTimes(1);
  });

  it("Ctrl+L snaps to now (non-mac chord)", () => {
    const onSnapToNow = vi.fn();
    render(
      <TcMiniTimeline
        beads={sampleBeads}
        scrubbedTo={1}
        onScrub={() => {}}
        onSnapToNow={onSnapToNow}
      />,
    );
    fireEvent.keyDown(screen.getByTestId("tc-mini-timeline"), {
      key: "l",
      ctrlKey: true,
    });
    expect(onSnapToNow).toHaveBeenCalledTimes(1);
  });
});
