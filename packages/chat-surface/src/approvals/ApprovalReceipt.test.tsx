// Collapsed approval-receipt render contract (FR-1.20, FR-1.23). Hoisted
// with the component from apps/frontend.
//
// Guards: the per-kind glyph/label map, the undo button that only renders
// inside the 60s window, the "Undo requested" chip that wins when the user
// has already asked, and the "nothing past expiry" branch.

import { render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApprovalReceipt, type ApprovalReceiptKind } from "./ApprovalReceipt";

describe("ApprovalReceipt", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-05-07T19:30:00Z"));
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  const cases: Array<[ApprovalReceiptKind, string, string]> = [
    ["approved", "✓", "Approved"],
    ["rejected", "✕", "Denied"],
    ["forwarded", "↗", "Forwarded"],
    ["cancelled", "⏸", "Cancelled"],
    ["chain-approved", "✓", "Approved"],
    ["chain-rejected", "✕", "Denied"],
  ];

  it.each(cases)(
    "maps kind %s to its glyph and label",
    (kind, glyph, label) => {
      const { container } = render(
        <ApprovalReceipt kind={kind} title="List Linear issues" />,
      );
      const receipt = container.querySelector(".atlas-approval-receipt");
      expect(receipt?.getAttribute("role")).toBe("note");
      expect(receipt?.getAttribute("data-kind")).toBe(kind);
      expect(
        container.querySelector(".atlas-approval-receipt__glyph")?.textContent,
      ).toBe(glyph);
      expect(
        container.querySelector(".atlas-approval-receipt__label")?.textContent,
      ).toBe(label);
      expect(
        container.querySelector(".atlas-approval-receipt__title")?.textContent,
      ).toBe("List Linear issues");
    },
  );

  it("renders an undo button with a live countdown inside the window", () => {
    const { container } = render(
      <ApprovalReceipt
        kind="approved"
        title="Send Slack message"
        undoUntil={new Date("2026-05-07T19:30:30Z")}
      />,
    );
    const button = container.querySelector(".atlas-approval-receipt__undo");
    expect(button).not.toBeNull();
    expect(button?.textContent).toBe("Undo (30s)");
  });

  it("renders the 'Undo requested' chip instead of the button when requested", () => {
    const { container } = render(
      <ApprovalReceipt
        kind="approved"
        title="Send Slack message"
        undoUntil={new Date("2026-05-07T19:30:30Z")}
        undoRequestedAt={new Date("2026-05-07T19:30:05Z")}
      />,
    );
    expect(container.querySelector(".atlas-approval-receipt__undo")).toBeNull();
    expect(
      container.querySelector(".atlas-approval-receipt__undo-requested")
        ?.textContent,
    ).toBe("Undo requested");
  });

  it("renders neither button nor chip once the window has expired", () => {
    const { container } = render(
      <ApprovalReceipt
        kind="approved"
        title="Send Slack message"
        undoUntil={new Date("2026-05-07T19:29:59Z")}
      />,
    );
    expect(container.querySelector(".atlas-approval-receipt__undo")).toBeNull();
    expect(
      container.querySelector(".atlas-approval-receipt__undo-requested"),
    ).toBeNull();
  });
});
