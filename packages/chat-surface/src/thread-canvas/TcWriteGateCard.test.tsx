// The canvas half of "one row asks, the canvas shows".
//
// The regression worth naming: before this card existed, a parked WRITE
// rendered `TcGateCard` on the canvas — the OAuth connect card — because both
// gate kinds ride the same `gate.opened` event and a write gate reports
// `auth_state: insufficient`. The user saw "More access needed" above a
// **Connect** button for a connector that was already connected, while the
// actual decision sat in the chat column.

import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";

import { TcWriteGateCard, type TcWriteGateCardProps } from "./TcWriteGateCard";
import type { TcWriteGatePayloadProps } from "./TcWriteGatePayload";

function card(overrides: Partial<Parameters<typeof TcWriteGateCard>[0]> = {}) {
  const props = {
    title: "Create an issue in Parth-test",
    connector: "linear",
    params: [
      { label: "title", value: "Flaky MCP reconnect after sleep" },
      { label: "team", value: "Parth-test" },
    ],
    ledgerId: "r7f3·142",
    irreversible: false,
    onApprove: vi.fn(),
    onDecline: vi.fn(),
    ...overrides,
  };
  render(<TcWriteGateCard {...props} />);
  return props;
}

describe("TcWriteGateCard — the detail the row does not carry", () => {
  it("shows the payload the reviewer has to judge", () => {
    card();
    const params = screen.getByTestId("tc-write-gate-card-params");
    expect(params.textContent).toContain("title");
    expect(params.textContent).toContain("Flaky MCP reconnect after sleep");
    expect(params.textContent).toContain("Parth-test");
  });

  it("anchors the decision to the ledger id it will be recorded under", () => {
    card();
    expect(screen.getByTestId("tc-write-gate-card-ledger-id").textContent).toBe(
      "r7f3·142",
    );
  });

  it("offers Approve and Decline — never Connect", () => {
    // The whole point: this connector IS connected. A Connect button here is
    // the bug the card replaces.
    card();
    expect(screen.getByTestId("tc-write-gate-card-approve")).toBeTruthy();
    expect(screen.getByTestId("tc-write-gate-card-decline")).toBeTruthy();
    expect(screen.queryByText("Connect")).toBeNull();
    expect(screen.queryByText("More access needed")).toBeNull();
  });

  it("resolves through the standard decision handlers", () => {
    const props = card();
    screen.getByTestId("tc-write-gate-card-approve").click();
    expect(props.onApprove).toHaveBeenCalledTimes(1);
    screen.getByTestId("tc-write-gate-card-decline").click();
    expect(props.onDecline).toHaveBeenCalledTimes(1);
  });
});

describe("TcWriteGateCard — reversibility", () => {
  it("states the reversible case as a fact, not a caution", () => {
    card();
    expect(
      screen.getByTestId("tc-write-gate-card-reversibility").textContent,
    ).toBe("You can undo this from the connector if it's wrong.");
  });

  it("says plainly when it cannot be undone", () => {
    card({ irreversible: true });
    expect(
      screen.getByTestId("tc-write-gate-card-reversibility").textContent,
    ).toBe("This cannot be undone from here.");
    expect(
      screen.getByTestId("tc-write-gate-card").getAttribute("data-risk"),
    ).toBe("high");
  });
});

describe("TcWriteGateCard — degraded input", () => {
  it("renders without a params frame when the approval carried none", () => {
    // The gate can open before its approval projection lands. Blanking the
    // surface somebody is waiting on would be worse than a card with no table.
    card({ params: [] });
    expect(screen.queryByTestId("tc-write-gate-card-params")).toBeNull();
    expect(screen.getByTestId("tc-write-gate-card-title")).toBeTruthy();
  });

  it("drops the connector from the eyebrow rather than printing a separator", () => {
    card({ connector: null });
    expect(screen.getByTestId("tc-write-gate-card-eyebrow").textContent).toBe(
      "Waiting on you",
    );
  });

  it("renders hostile argument values as text", () => {
    const hostile = "<img src=x onerror=alert(1)>";
    card({ params: [{ label: "title", value: hostile }] });
    const params = screen.getByTestId("tc-write-gate-card-params");
    expect(params.textContent).toContain(hostile);
    expect(params.querySelector("img")).toBeNull();
  });
});

/* ── the Studio-canvas gap, closed (PRD-shell-execution §14.1) ──────────────
 *
 * The card rendered `TcWriteGatePayload` and passed it `params` and `ledgerId`
 * only. Every other field on that component is absent-means-omitted, so the
 * under-fed surface drew a SMALLER card rather than failing — which is why the
 * hole survived: a command ask on the canvas showed its title and an enabled
 * Approve, with the command itself only in the chat column.
 */

/** The `run_command` ask as `approvalProjection` freezes it: the write gate's
 *  wire shape carries no `arguments` and no presentation, so `command` is the
 *  only evidence on the card. */
function commandCard(command: string) {
  card({
    title: "Run `pytest -q` in my-project",
    connector: null,
    params: [],
    commandText: command,
    irreversible: true,
  });
}

describe("TcWriteGateCard — the command ask", () => {
  it("shows the exact command the decision is made over", () => {
    commandCard("pytest -q");
    expect(screen.getByTestId("tc-write-gate-card-command").textContent).toBe(
      "pytest -q",
    );
  });

  it("keeps the bytes a multi-line command was written in", () => {
    // What runs is `/bin/sh -c "<command>"` with the bytes the model sent. A
    // surface that re-flowed them would be showing a different command from the
    // one being approved — the card/process disagreement this lane exists to
    // prevent.
    const multi = 'set -e\nprintf "a\\n"\nls -la';
    commandCard(multi);
    const block = screen.getByTestId("tc-write-gate-card-command");
    expect(block.textContent).toBe(multi);
    expect(block.tagName.toLowerCase()).toBe("pre");
  });

  it("carries the no-undo line the row carries", () => {
    commandCard("rm -rf build");
    expect(screen.getByTestId("tc-write-gate-card-no-undo").textContent).toBe(
      "Changes made by a command can't be undone from here.",
    );
  });

  it("replaces the reversibility line rather than stacking on it", () => {
    // Two "cannot be undone" sentences three lines apart reads as a bug and
    // trains people to skim the one that matters.
    commandCard("rm -rf build");
    expect(screen.queryByTestId("tc-write-gate-card-reversibility")).toBeNull();
  });

  it("renders a hostile command as text", () => {
    const hostile = 'echo "<img src=x onerror=alert(1)>"';
    commandCard(hostile);
    const block = screen.getByTestId("tc-write-gate-card-command");
    expect(block.textContent).toBe(hostile);
    expect(block.querySelector("img")).toBeNull();
  });

  it("wraps an unbreakable token instead of widening the card", () => {
    // `.tc-write-gate__command` is `white-space: pre-wrap`, which breaks at
    // spaces and nowhere else. The row's expanded body has had a per-CONTAINER
    // `overflow-wrap: anywhere` since a 138-character token measured a 356px
    // column out to 1022px; this card renders the same payload OUTSIDE that
    // container, so it needs — and now has — its own copy of the rule.
    //
    // Asserted against the real stylesheet, because the rule is the fix: jsdom
    // performs no layout, so the resolved cascade is what is knowable here.
    const sheet = document.createElement("style");
    sheet.textContent = readFileSync(
      resolve(
        typeof import.meta.dirname === "string"
          ? import.meta.dirname
          : dirname(fileURLToPath(import.meta.url)),
        "review-surfaces.css",
      ),
      "utf-8",
    );
    document.head.appendChild(sheet);
    try {
      commandCard(`curl https://example.test/${"a".repeat(400)}`);
      expect(
        getComputedStyle(screen.getByTestId("tc-write-gate-card-command"))
          .overflowWrap,
      ).toBe("anywhere");
    } finally {
      sheet.remove();
    }
  });
});

describe("TcWriteGateCard — the shaped evidence", () => {
  it("renders the batch the canvas used to drop", () => {
    // Not a command-lane fix. `presentation` had the same hole, so a rows card
    // showed twelve payees in the chat column and a params-less heading on the
    // canvas.
    card({
      params: [],
      presentation: {
        layout: "rows",
        rows: [
          {
            rowId: "r1",
            label: "Mira Patel",
            value: "$1,200.00",
            note: null,
            initials: "MP",
            status: "pending",
            decidable: false,
          },
        ],
        preview: null,
        approveLabel: null,
        rejectLabel: null,
        provenance: null,
      },
    });
    const rows = screen.getByTestId("tc-write-gate-card-rows");
    expect(rows.textContent).toContain("Mira Patel");
    expect(rows.textContent).toContain("$1,200.00");
  });
});

describe("TcWriteGateCard — prop parity with the payload", () => {
  it("accepts every field the shared payload renders", () => {
    // A TYPE assertion, enforced by `npm run typecheck` rather than at runtime,
    // because the failure this guards is a compile-time omission that produces
    // no error: every payload field is absent-means-omitted, so a card that
    // stopped forwarding one would keep rendering — smaller, and silently.
    // That is exactly how `presentation` and `commandText` came to be missing.
    //
    // `testIdPrefix` is excluded: it is the one prop that is deliberately
    // per-surface, since both surfaces can be mounted at once for one gate.
    type PayloadEvidence = Exclude<
      keyof TcWriteGatePayloadProps,
      "testIdPrefix"
    >;
    type Missing = Exclude<PayloadEvidence, keyof TcWriteGateCardProps>;
    // `[Missing] extends [never]`, tuple-wrapped: a bare `extends never`
    // distributes and is `never` for BOTH answers, so it would pass whatever
    // the card is missing. Annotating with a conditional type is what makes
    // this a compile error rather than a green tautology — `= true` does not
    // typecheck against `false`.
    const parity: [Missing] extends [never] ? true : false = true;
    expect(parity).toBe(true);
  });
});
