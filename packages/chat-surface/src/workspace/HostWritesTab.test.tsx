// HostWritesTab — the safety properties, the receipt, and the layout contract.
//
// The layout half is asserted with `getComputedStyle` because jsdom runs no
// layout: a `toBeInTheDocument()` on the Undo button says nothing about whether
// a narrow rail clips it off the end of its row. This body carries its geometry
// inline (the sibling rail bodies do, deliberately — `atlas-*` class rules are
// declared only in the WEB app's stylesheet and never load on desktop), so the
// inline declarations ARE the real stylesheet for these nodes and
// `getComputedStyle` reads exactly what ships.

import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { HostWriteGroup } from "../destinations/run/hostWrites";
import { UNBOUND_HOST_WRITE_KEY } from "../destinations/run/hostWrites";
import { HostWritesTab } from "./HostWritesTab";

function group(over: Partial<HostWriteGroup> = {}): HostWriteGroup {
  const paths = over.entries?.map((e) => e.path) ?? ["/Users/x/notes.md"];
  return {
    key: "call_a",
    toolCallId: "call_a",
    entries: [
      {
        entry_id: "e1",
        tool_call_id: "call_a",
        sequence: 1,
        path: "/Users/x/notes.md",
        kind: "modified",
        prior_size: 9,
        revertible: true,
        captured_at: "2026-01-01T00:00:00Z",
      },
    ],
    pathCount: new Set(paths).size,
    undoable: true,
    firstSequence: 1,
    ...over,
  };
}

describe("HostWritesTab — an undo is never one click from rest", () => {
  it("draws no posting control until the row is armed", () => {
    render(<HostWritesTab groups={[group()]} onUndo={vi.fn()} />);
    expect(screen.getByTestId("host-writes-undo-call_a")).toBeInTheDocument();
    expect(screen.queryByTestId("host-writes-confirm-call_a")).toBeNull();
    expect(screen.queryByTestId("host-writes-armed-call_a")).toBeNull();
  });

  it("does not post on the arming click", () => {
    const onUndo = vi.fn();
    render(<HostWritesTab groups={[group()]} onUndo={onUndo} />);
    fireEvent.click(screen.getByTestId("host-writes-undo-call_a"));
    expect(onUndo).not.toHaveBeenCalled();
    expect(
      screen.getByTestId("host-writes-confirm-call_a"),
    ).toBeInTheDocument();
  });

  // The write gate's lesson, transplanted: the property is carried by the SHAPE
  // of the name, not only by the branch that renders the button. A journey that
  // pressed every `[data-testid^=host-writes-undo-]` must reach the ARM and
  // nothing else, or "no blind undo" passes over a control one click away.
  it("keeps the posting control out of the arm's testid prefix", () => {
    const armed = (): NodeListOf<Element> =>
      document.querySelectorAll('[data-testid^="host-writes-undo-"]');
    render(<HostWritesTab groups={[group()]} onUndo={vi.fn()} />);

    // At rest the prefix matches exactly the arm.
    expect(armed()).toHaveLength(1);
    expect(armed()[0]).toBe(screen.getByTestId("host-writes-undo-call_a"));

    fireEvent.click(screen.getByTestId("host-writes-undo-call_a"));
    // Armed, the posting control exists and the prefix reaches NOTHING — the
    // arm has given way to it, and it is not named under the arm's prefix. A
    // journey pressing every `host-writes-undo-*` therefore cannot post.
    expect(
      screen.getByTestId("host-writes-confirm-call_a"),
    ).toBeInTheDocument();
    expect(armed()).toHaveLength(0);
    expect(
      screen
        .getByTestId("host-writes-confirm-call_a")
        .getAttribute("data-testid")!
        .startsWith("host-writes-undo-"),
    ).toBe(false);
  });

  // Two groups parked at once is a drawn state. An unscoped name would be
  // ambiguous in it, and an ambiguous selector is a decision that never happens.
  it("scopes every deciding control by its group", () => {
    render(
      <HostWritesTab
        groups={[group(), group({ key: "call_b", toolCallId: "call_b" })]}
        onUndo={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByTestId("host-writes-undo-call_a"));
    fireEvent.click(screen.getByTestId("host-writes-undo-call_b"));
    expect(
      document.querySelectorAll('[data-testid^="host-writes-confirm-"]'),
    ).toHaveLength(2);
    const rowA = screen.getByTestId("host-writes-group-call_a");
    expect(
      within(rowA).getByTestId("host-writes-confirm-call_a"),
    ).toBeInTheDocument();
    expect(within(rowA).queryByTestId("host-writes-confirm-call_b")).toBeNull();
  });

  it("posts the group it was armed on, and only on the confirm", () => {
    const onUndo = vi.fn();
    const target = group();
    render(<HostWritesTab groups={[target]} onUndo={onUndo} />);
    fireEvent.click(screen.getByTestId("host-writes-undo-call_a"));
    fireEvent.click(screen.getByTestId("host-writes-confirm-call_a"));
    expect(onUndo).toHaveBeenCalledTimes(1);
    expect(onUndo).toHaveBeenCalledWith(target);
  });

  it("lets the reader back out without posting", () => {
    const onUndo = vi.fn();
    render(<HostWritesTab groups={[group()]} onUndo={onUndo} />);
    fireEvent.click(screen.getByTestId("host-writes-undo-call_a"));
    fireEvent.click(screen.getByTestId("host-writes-cancel-call_a"));
    expect(onUndo).not.toHaveBeenCalled();
    expect(screen.queryByTestId("host-writes-confirm-call_a")).toBeNull();
    expect(screen.getByTestId("host-writes-undo-call_a")).toBeInTheDocument();
  });

  it("names every file the undo would touch, in full", () => {
    render(
      <HostWritesTab
        groups={[
          group({
            pathCount: 2,
            entries: [
              {
                entry_id: "e1",
                tool_call_id: "call_a",
                sequence: 1,
                path: "/Users/x/Documents/quarterly-plan.md",
                kind: "modified",
                prior_size: 9,
                revertible: true,
                captured_at: "2026-01-01T00:00:00Z",
              },
              {
                entry_id: "e2",
                tool_call_id: "call_a",
                sequence: 2,
                path: "/Users/x/Documents/notes/appendix.md",
                kind: "created",
                prior_size: 0,
                revertible: true,
                captured_at: "2026-01-01T00:00:00Z",
              },
            ],
          }),
        ]}
        onUndo={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByTestId("host-writes-undo-call_a"));
    const armed = screen.getByTestId("host-writes-armed-call_a");
    expect(armed.textContent).toContain("/Users/x/Documents/quarterly-plan.md");
    expect(armed.textContent).toContain("/Users/x/Documents/notes/appendix.md");
    // And it says what cannot be recovered — the journal captured the state
    // BEFORE the agent wrote, and nothing captured what is there now.
    expect(armed.textContent).toContain("cannot be undone");
  });
});

describe("HostWritesTab — what it refuses to offer", () => {
  // `tool_call_id: null` is reachable only through a whole-run revert, which no
  // control on this surface performs. The row is still listed: hiding a change
  // to the user's disk because it cannot be undone here would be worse.
  it("lists an unattributed write but offers no control for it", () => {
    render(
      <HostWritesTab
        groups={[
          group({
            key: UNBOUND_HOST_WRITE_KEY,
            toolCallId: null,
            undoable: false,
          }),
        ]}
        onUndo={vi.fn()}
      />,
    );
    const row = screen.getByTestId(
      `host-writes-group-${UNBOUND_HOST_WRITE_KEY}`,
    );
    expect(row).toBeInTheDocument();
    expect(row.textContent).toContain("No tool call");
    expect(
      document.querySelectorAll('[data-testid^="host-writes-undo-"]'),
    ).toHaveLength(0);
  });

  it("offers nothing when the host wired no callback", () => {
    render(<HostWritesTab groups={[group()]} />);
    expect(screen.queryByTestId("host-writes-undo-call_a")).toBeNull();
    expect(screen.getByTestId("host-writes-group-call_a")).toBeInTheDocument();
  });

  it("states a read failure rather than implying the run changed nothing", () => {
    render(<HostWritesTab groups={[]} error="Couldn't read it." />);
    expect(screen.getByTestId("host-writes-tab-error")).toHaveTextContent(
      "Couldn't read it.",
    );
    expect(screen.queryByTestId("host-writes-tab-empty")).toBeNull();
  });
});

describe("HostWritesTab — the receipt, because an undo is auditable", () => {
  const summary = {
    rows: [
      {
        path: "/Users/x/notes.md",
        kind: "modified" as const,
        status: "restored",
        undone: true,
        detail: null,
      },
      {
        path: "/Users/x/linked.md",
        kind: "modified" as const,
        status: "refused",
        undone: false,
        detail: "target is a symlink",
      },
    ],
    undone: 1,
    total: 2,
    complete: false,
    headline: "Partly undone — 1 of 2 files put back.",
  };

  it("prints one row per path with the server's own status word", () => {
    render(
      <HostWritesTab
        groups={[group()]}
        states={{ call_a: "reverted" }}
        reports={{ call_a: summary }}
        onUndo={vi.fn()}
      />,
    );
    const receipt = screen.getByTestId("host-writes-receipt-call_a");
    expect(receipt).toHaveAttribute("data-complete", "false");
    expect(receipt.textContent).toContain("restored");
    expect(receipt.textContent).toContain("refused");
    expect(receipt.textContent).toContain("target is a symlink");
    expect(receipt.textContent).toContain("Partly undone");
  });

  it("withdraws the control once the server has answered, so the same undo is not re-posted", () => {
    render(
      <HostWritesTab
        groups={[group()]}
        states={{ call_a: "reverted" }}
        reports={{ call_a: summary }}
        onUndo={vi.fn()}
      />,
    );
    expect(screen.queryByTestId("host-writes-undo-call_a")).toBeNull();
    expect(screen.getByTestId("host-writes-group-call_a")).toHaveAttribute(
      "data-state",
      "reverted",
    );
  });

  it("shows a transport failure verbatim rather than a card that stopped moving", () => {
    render(
      <HostWritesTab
        groups={[group()]}
        states={{ call_a: "failed" }}
        failures={{ call_a: "The undo did not complete." }}
        onUndo={vi.fn()}
      />,
    );
    expect(screen.getByTestId("host-writes-failure-call_a")).toHaveTextContent(
      "The undo did not complete.",
    );
  });
});

// The header layout rule, applied to the item most likely to break it: a row
// carrying an absolute host path AND the control that acts on it. The row's
// title/path column clips, so if the actions box were shrinkable the Undo
// button is what a narrow rail would eat — an undo nobody can reach.
describe("HostWritesTab — the path cannot clip the control", () => {
  const long = group({
    entries: [
      {
        entry_id: "e1",
        tool_call_id: "call_a",
        sequence: 1,
        // No break opportunity in the longest segment — the shape that sets a
        // container's min-content width if nothing stops it.
        path: `/Volumes/Archive/${"x".repeat(160)}/report.md`,
        kind: "modified",
        prior_size: 9,
        revertible: true,
        captured_at: "2026-01-01T00:00:00Z",
      },
    ],
  });

  it("makes the path column the thing that gives way, never the actions", () => {
    render(<HostWritesTab groups={[long]} onUndo={vi.fn()} />);
    const undo = screen.getByTestId("host-writes-undo-call_a");
    const actions = undo.parentElement!;
    const main = actions.previousElementSibling!;
    expect(globalThis.getComputedStyle(actions).flexShrink).toBe("0");
    expect(globalThis.getComputedStyle(main).flexShrink).toBe("1");
    expect(globalThis.getComputedStyle(main).minWidth).toBe("0px");
  });

  it("truncates the path in the ROW and never in the confirmation", () => {
    render(<HostWritesTab groups={[long]} onUndo={vi.fn()} />);
    const undo = screen.getByTestId("host-writes-undo-call_a");
    const subtitle = undo.parentElement!.previousElementSibling!.children[1]!;
    expect(globalThis.getComputedStyle(subtitle).textOverflow).toBe("ellipsis");

    fireEvent.click(undo);
    // Consent to an ellipsis is not consent: the confirmation's copy of the
    // path wraps rather than truncates, and carries no ellipsis at all.
    const path = within(
      screen.getByTestId("host-writes-armed-call_a"),
    ).getByRole("listitem");
    const style = globalThis.getComputedStyle(path);
    expect(style.overflowWrap).toBe("anywhere");
    expect(style.textOverflow).not.toBe("ellipsis");
    expect(path.textContent).toBe(long.entries[0]!.path);
  });
});
