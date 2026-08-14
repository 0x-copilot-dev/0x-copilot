// The run-scoped Approve on the ask card — where it is, when it is drawn, and
// the two states in which it must not exist at all.
//
// The control's whole risk is that it widens what one click covers. So the
// assertions below are mostly negative, and each names the failure it prevents.

import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { TcWriteGateRow } from "./TcWriteGateRow";

const here =
  typeof import.meta.dirname === "string"
    ? import.meta.dirname
    : dirname(fileURLToPath(import.meta.url));

let sheet: HTMLStyleElement | null = null;

function mountRealCss(): void {
  sheet = document.createElement("style");
  sheet.textContent = readFileSync(
    resolve(here, "review-surfaces.css"),
    "utf-8",
  );
  document.head.appendChild(sheet);
}

afterEach(() => {
  sheet?.remove();
  sheet = null;
});

function expand(): void {
  fireEvent.click(screen.getByTestId("tc-write-gate-review"));
}

describe("TcWriteGateRow — the run-scoped Approve", () => {
  it("is NOT in the header: the collapsed card offers once, and only once", () => {
    render(
      <TcWriteGateRow
        title="Create an issue in Parth-test"
        connector="linear"
        access="WRITE"
        onApprove={vi.fn()}
        onApproveAlways={vi.fn()}
        onDecline={vi.fn()}
      />,
    );
    // The card's rule for an irreversible write generalises: a decision that
    // covers more than the call in front of you is not a one-click decision.
    expect(screen.queryByTestId("tc-write-gate-always")).toBeNull();
    const header = document.querySelector(".tc-write-gate__hd");
    expect(header).not.toBeNull();
    expect(
      within(header as HTMLElement).getByTestId("tc-write-gate-approve"),
    ).toBeTruthy();
    expect(
      within(header as HTMLElement).queryByTestId("tc-write-gate-always"),
    ).toBeNull();
  });

  it("appears in the body once expanded, and reports the scope in words", () => {
    render(
      <TcWriteGateRow
        title="Create an issue in Parth-test"
        connector="linear"
        access="WRITE"
        onApprove={vi.fn()}
        onApproveAlways={vi.fn()}
        onDecline={vi.fn()}
      />,
    );
    expand();
    const body = document.querySelector(".tc-write-gate__body");
    expect(body).not.toBeNull();
    const always = within(body as HTMLElement).getByTestId(
      "tc-write-gate-always",
    );
    expect(always.textContent).toBe("Approve, and don't ask again this run");
    // `allow_always` reads as "forever"; this lane's rule expires with the run,
    // and that difference IS the consent. It has to be on the card, not only in
    // a comment.
    expect(
      within(body as HTMLElement).getByTestId("tc-write-gate-body-scope-note")
        .textContent,
    ).toBe("Applies to this call only, and ends when the run does.");
  });

  it("is not drawn at all when no host is listening — never drawn inert", () => {
    render(
      <TcWriteGateRow
        title="Create an issue in Parth-test"
        onApprove={vi.fn()}
        onDecline={vi.fn()}
      />,
    );
    expand();
    // A scope button that posts into the void is the defect this whole binding
    // exists to close; a disabled one is the same defect with a nicer cursor.
    expect(screen.queryByTestId("tc-write-gate-always")).toBeNull();
    expect(screen.queryByTestId("tc-write-gate-body-scope-note")).toBeNull();
  });

  it("is NEVER drawn for an irreversible write, expanded or not", () => {
    render(
      <TcWriteGateRow
        title="Delete 14 issues"
        connector="linear"
        access="WRITE"
        irreversible
        params={[{ label: "count", value: "14" }]}
        onApprove={vi.fn()}
        onApproveAlways={vi.fn()}
        onDecline={vi.fn()}
      />,
    );
    expect(screen.queryByTestId("tc-write-gate-always")).toBeNull();
    expand();
    // The server already withholds `allow_always` for a destructive op. This is
    // the second enforcement point on purpose: an advance yes to a class of
    // deletes is exactly what the PDP's destructive rung exists to prevent, and
    // a safety property with one enforcement point is one deploy from none.
    expect(screen.queryByTestId("tc-write-gate-always")).toBeNull();
    // …while the payload-gated body approve is still the ONLY way to approve it.
    expect(screen.getByTestId("tc-write-gate-body-approve")).toBeTruthy();
  });

  it("fires its own handler, not the plain approve", () => {
    const onApprove = vi.fn();
    const onApproveAlways = vi.fn();
    render(
      <TcWriteGateRow
        title="Create an issue in Parth-test"
        onApprove={onApprove}
        onApproveAlways={onApproveAlways}
        onDecline={vi.fn()}
      />,
    );
    expand();
    fireEvent.click(screen.getByTestId("tc-write-gate-always"));
    // Same terminal decision, different REACH — and the host has to post a
    // different body for it, so one handler could not have served both.
    expect(onApproveAlways).toHaveBeenCalledTimes(1);
    expect(onApprove).not.toHaveBeenCalled();
  });

  it("carries a testid that neither Approve nor Decline can select", () => {
    render(
      <TcWriteGateRow
        title="Create an issue in Parth-test"
        approveTestId="tc-chat-approval-approve-a1"
        declineTestId="tc-chat-approval-reject-a1"
        alwaysApproveTestId="tc-chat-approval-always-a1"
        onApprove={vi.fn()}
        onApproveAlways={vi.fn()}
        onDecline={vi.fn()}
      />,
    );
    expand();
    // Five live journeys press Approve by the `tc-chat-approval-approve-`
    // PREFIX and one presses the exact reject id. Neither may ever land on the
    // control that widens the decision to the whole run — the property is
    // carried by the SHAPE of the name, as it is for `…-body-approve-<id>`.
    expect(
      document.querySelectorAll('[data-testid^="tc-chat-approval-approve-"]'),
    ).toHaveLength(1);
    expect(
      document.querySelector('[data-testid="tc-chat-approval-approve-a1"]'),
    ).not.toBe(screen.getByTestId("tc-chat-approval-always-a1"));
    expect(
      document.querySelector('[data-testid="tc-chat-approval-reject-a1"]'),
    ).not.toBe(screen.getByTestId("tc-chat-approval-always-a1"));
  });

  it("is a ghost, and wraps — the wider decision is never the louder one", () => {
    mountRealCss();
    render(
      <TcWriteGateRow
        title="Create an issue in Parth-test"
        onApprove={vi.fn()}
        onApproveAlways={vi.fn()}
        onDecline={vi.fn()}
      />,
    );
    expand();
    const always = screen.getByTestId("tc-write-gate-always");
    // Ghost, not primary: a run-scoped rule is the convenient answer, and
    // drawing it as the recommended one is how people stop reading the cards.
    expect(always.className).toContain("ui-button--ghost");
    expect(always.className).not.toContain("ui-button--primary");
    const style = globalThis.getComputedStyle(always);
    // The label is long on purpose. Left unwrappable it would set the BODY's
    // min-content width, which becomes the grid track, which stretches the
    // header past a frame that is `overflow: hidden` — clipping the decision
    // controls out of reach. That failure has shipped on this card before.
    expect(style.whiteSpace).toBe("normal");
    expect(style.maxWidth).toBe("100%");
    expect(style.overflowWrap).toBe("anywhere");
  });
});
