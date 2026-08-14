// The ask is one row; the detail is on the canvas.
//
// The safety property worth pinning is the one the compact form BUYS: an
// irreversible write has no approve button in the feed at all. That is only
// expressible once the row is small enough for the choice of button to be the
// loudest thing on it — the old twelve-line card offered Approve next to a
// paragraph nobody reads while a run is streaming.

import { readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import type { ApprovalPresentation } from "../approvals/presentation";
import { TcWriteGateRow, type WriteGateGrantAlways } from "./TcWriteGateRow";

function row(overrides: Partial<Parameters<typeof TcWriteGateRow>[0]> = {}) {
  const props = {
    title: "Create an issue in Parth-test",
    connector: "linear",
    irreversible: false,
    onApprove: vi.fn(),
    onDecline: vi.fn(),
    onReview: vi.fn(),
    ...overrides,
  };
  render(<TcWriteGateRow {...props} />);
  return props;
}

describe("TcWriteGateRow — a reversible write", () => {
  it("asks in one line: what, where, and two buttons", () => {
    row();
    expect(screen.getByTestId("tc-write-gate-title").textContent).toBe(
      "Create an issue in Parth-test",
    );
    expect(screen.getByTestId("tc-write-gate-connector").textContent).toBe(
      "linear",
    );
    expect(screen.getByTestId("tc-write-gate-approve")).toBeTruthy();
    expect(screen.getByTestId("tc-write-gate-decline")).toBeTruthy();
  });

  it("approves and declines through the standard decision handlers", () => {
    // Approval rides `decision` on the /decision POST, never the free text the
    // question card would have collected — the gate only borrows the
    // ask_a_question WIRE shape, it is not a question.
    const props = row();
    screen.getByTestId("tc-write-gate-approve").click();
    expect(props.onApprove).toHaveBeenCalledTimes(1);
    screen.getByTestId("tc-write-gate-decline").click();
    expect(props.onDecline).toHaveBeenCalledTimes(1);
  });

  it("omits the connector label rather than printing an empty one", () => {
    row({ connector: null });
    expect(screen.queryByTestId("tc-write-gate-connector")).toBeNull();
  });
});

describe("TcWriteGateRow — an irreversible write", () => {
  it("offers no approve button — the canvas is the only way through", () => {
    const props = row({ irreversible: true });

    expect(screen.queryByTestId("tc-write-gate-approve")).toBeNull();
    screen.getByTestId("tc-write-gate-review").click();
    expect(props.onReview).toHaveBeenCalledTimes(1);
    expect(props.onApprove).not.toHaveBeenCalled();
  });

  it("still lets it be declined in one click", () => {
    // Declining is safe by definition. Making somebody open a canvas to say no
    // would push them toward leaving it parked instead.
    const props = row({ irreversible: true });
    screen.getByTestId("tc-write-gate-decline").click();
    expect(props.onDecline).toHaveBeenCalledTimes(1);
  });

  it("marks the risk on the row itself, not with a coloured panel", () => {
    row({ irreversible: true });
    expect(
      screen.getByTestId("tc-write-gate-row").getAttribute("data-risk"),
    ).toBe("high");
  });
});

describe("TcWriteGateRow — untrusted text", () => {
  it("renders a hostile title as a text node", () => {
    const hostile = "<img src=x onerror=alert(1)>";
    row({ title: hostile });
    const title = screen.getByTestId("tc-write-gate-title");
    expect(title.textContent).toBe(hostile);
    expect(title.querySelector("img")).toBeNull();
  });

  it("disables both actions while a decision is in flight", () => {
    row({ busy: true });
    expect(
      screen.getByTestId("tc-write-gate-approve").hasAttribute("disabled"),
    ).toBe(true);
    expect(
      screen.getByTestId("tc-write-gate-decline").hasAttribute("disabled"),
    ).toBe(true);
  });

  it("disables the irreversible lane's body approve once a decision is in flight", () => {
    // The check above runs on the default (reversible) fixture, so it proved
    // nothing about the arm where approving is the expensive one — and it
    // could not, because `tc-write-gate-body-approve` does not exist until the
    // card is expanded. This drives the sequence that actually produces it:
    // expand, click, and the host flips `busy` while the POST is in flight. A
    // body Approve that stays live there is a double-submitted write that
    // cannot be undone.
    const props = {
      title: "Delete the staging index",
      connector: "elastic",
      irreversible: true,
      params: [{ label: "count", value: "14" }],
      onApprove: vi.fn(),
      onDecline: vi.fn(),
    };
    const { rerender } = render(<TcWriteGateRow {...props} />);
    fireEvent.click(screen.getByTestId("tc-write-gate-review"));
    const approve = screen.getByTestId("tc-write-gate-body-approve");
    expect(approve.hasAttribute("disabled")).toBe(false);
    fireEvent.click(approve);
    expect(props.onApprove).toHaveBeenCalledTimes(1);

    rerender(<TcWriteGateRow {...props} busy />);
    // Frozen, not unmounted: the evidence stays on screen while the decision
    // lands, and the second click cannot happen.
    expect(screen.getByTestId("tc-write-gate-body-params")).toBeTruthy();
    for (const tid of [
      "tc-write-gate-body-approve",
      "tc-write-gate-decline",
      "tc-write-gate-review",
    ]) {
      expect(screen.getByTestId(tid).hasAttribute("disabled")).toBe(true);
    }
    fireEvent.click(screen.getByTestId("tc-write-gate-body-approve"));
    expect(props.onApprove).toHaveBeenCalledTimes(1);
  });
});

// THE STATE ATTRIBUTE THE STYLESHEET IS KEYED ON.
//
// `review-surfaces.css` selects `.tc-write-gate[data-open="true"]` to rotate the
// chevron and to lay out the body approve, and the live packaged-app journey
// (`tools/desktop-journeys/write-gate-inline/inline_gate.py`) reads the same
// attribute to decide whether the card opened. Nothing asserted it, so a
// refactor that kept `open` purely in React state would leave the card
// functionally correct, visually wrong, and the journey unable to tell.
describe("TcWriteGateRow — the open state is on the DOM, not only in React", () => {
  it("flips data-open on the card when the disclosure is used", () => {
    row({ params: [{ label: "repo", value: "Parth-test" }] });
    const card = () => screen.getByTestId("tc-write-gate");
    expect(card().getAttribute("data-open")).toBe("false");
    fireEvent.click(screen.getByTestId("tc-write-gate-review"));
    expect(card().getAttribute("data-open")).toBe("true");
    fireEvent.click(screen.getByTestId("tc-write-gate-review"));
    expect(card().getAttribute("data-open")).toBe("false");
    expect(screen.queryByTestId("tc-write-gate-body")).toBeNull();
  });
});

// The dead-control regression. `onReviewWriteGate` had no producer, so this
// button did nothing — and for an IRREVERSIBLE write it is the PRIMARY action,
// with Approve deliberately withheld until the payload has been seen. The
// safety design that refuses a blind approval had become a refusal to allow any
// approval: those gates could only be declined.
describe("TcWriteGateRow — Review must actually do something", () => {
  it("calls onReview for an irreversible write, where it is the only way forward", () => {
    const onReview = vi.fn();
    render(
      <TcWriteGateRow
        title="Delete the staging index"
        connector="elastic"
        irreversible
        onApprove={vi.fn()}
        onDecline={vi.fn()}
        onReview={onReview}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /review/i }));
    expect(onReview).toHaveBeenCalledTimes(1);
  });
});

// Expanding in place. The safety rule this must preserve is an ORDERING rule,
// not a location rule: an approve control for an irreversible write must not be
// reachable in ONE click from the collapsed row. Approving after the payload
// has rendered is the thing the old design was protecting; refusing it there
// too is what turned Review into a dead end.
describe("TcWriteGateRow — expand in place", () => {
  const PARAMS = [
    { label: "repo", value: "Parth-test" },
    { label: "title", value: "Flaky MCP reconnect" },
  ];

  function row(over: Record<string, unknown> = {}) {
    return render(
      <TcWriteGateRow
        title="Delete the staging index"
        connector="elastic"
        irreversible
        params={PARAMS}
        ledgerId="r7f3·142"
        onApprove={vi.fn()}
        onDecline={vi.fn()}
        onReview={vi.fn()}
        {...over}
      />,
    );
  }

  it("shows no payload until it is expanded", () => {
    row();
    expect(screen.queryByTestId("tc-write-gate-body")).toBeNull();
    expect(screen.queryByTestId("tc-write-gate-body-params")).toBeNull();
  });

  it("reveals the params, the reversibility line and the audit anchor", () => {
    row();
    fireEvent.click(screen.getByTestId("tc-write-gate-review"));
    expect(
      screen.getByTestId("tc-write-gate-body-params").textContent,
    ).toContain("Parth-test");
    expect(
      screen.getByTestId("tc-write-gate-body-reversibility").textContent,
    ).toBe("This cannot be undone from here.");
    expect(screen.getByTestId("tc-write-gate-body-ledger-id").textContent).toBe(
      "r7f3·142",
    );
  });

  it("omits the audit anchor rather than guessing when there is no ledger row", () => {
    row({ ledgerId: undefined });
    fireEvent.click(screen.getByTestId("tc-write-gate-review"));
    expect(screen.queryByTestId("tc-write-gate-body-ledger-id")).toBeNull();
  });

  // The ordering rule, both halves.
  it("offers no approve control at all while collapsed", () => {
    row();
    expect(screen.queryByTestId("tc-write-gate-approve")).toBeNull();
    expect(screen.queryByTestId("tc-write-gate-body-approve")).toBeNull();
  });

  it("offers approve in the body once the payload is on screen", () => {
    const onApprove = vi.fn();
    row({ onApprove });
    fireEvent.click(screen.getByTestId("tc-write-gate-review"));
    fireEvent.click(screen.getByTestId("tc-write-gate-body-approve"));
    expect(onApprove).toHaveBeenCalledTimes(1);
    // Still never one click from the collapsed row.
    expect(screen.queryByTestId("tc-write-gate-approve")).toBeNull();
  });

  // A gate can open before its approval projection lands, so an empty frame is
  // reachable — and approving over one IS the blind approval the rule forbids.
  it("withholds approve when expanding reveals an empty payload", () => {
    row({ params: [] });
    fireEvent.click(screen.getByTestId("tc-write-gate-review"));
    expect(screen.getByTestId("tc-write-gate-body")).toBeTruthy();
    expect(screen.queryByTestId("tc-write-gate-body-approve")).toBeNull();
    expect(screen.queryByTestId("tc-write-gate-approve")).toBeNull();
  });

  it("keeps decline one click in every state", () => {
    const onDecline = vi.fn();
    row({ onDecline });
    fireEvent.click(screen.getByTestId("tc-write-gate-decline"));
    fireEvent.click(screen.getByTestId("tc-write-gate-review"));
    fireEvent.click(screen.getByTestId("tc-write-gate-decline"));
    expect(onDecline).toHaveBeenCalledTimes(2);
  });

  it("toggles without needing a host — the dead-control shape cannot return", () => {
    render(
      <TcWriteGateRow
        title="Delete the staging index"
        connector="elastic"
        irreversible
        params={PARAMS}
        onApprove={vi.fn()}
        onDecline={vi.fn()}
        onReview={() => {
          throw new Error("host threw");
        }}
      />,
    );
    // Even a host that explodes must not stop the row from opening. Written
    // against a THROWING host rather than a missing one because the original
    // regression was a callback that silently did nothing — a row whose
    // behaviour is contingent on the host is the shape to keep out, in any of
    // its forms.
    fireEvent.click(screen.getByTestId("tc-write-gate-review"));
    expect(screen.getByTestId("tc-write-gate-body")).toBeTruthy();
  });

  it("lets a reversible write be inspected without giving up its one-click approve", () => {
    const onApprove = vi.fn();
    row({ irreversible: false, onApprove });
    expect(screen.getByTestId("tc-write-gate-approve")).toBeTruthy();
    fireEvent.click(screen.getByTestId("tc-write-gate-review"));
    expect(screen.getByTestId("tc-write-gate-body-params")).toBeTruthy();
    fireEvent.click(screen.getByTestId("tc-write-gate-approve"));
    expect(onApprove).toHaveBeenCalledTimes(1);
  });
});

// THE LAYOUT RULE, pinned. "Expanding adds a body underneath and moves
// nothing" is what makes a card that grows under the cursor safe to click on —
// otherwise the button you were reaching for slides out from under you at the
// exact moment you commit. jsdom has no layout engine, so what is asserted is
// the thing layout is DERIVED from: the header's controls, their order, and
// their labels are identical in both states. A width-varying label ("Review →"
// → "Hide") is precisely how this used to break.
describe("TcWriteGateRow — the header is identical collapsed and expanded", () => {
  function headerActions(): readonly string[] {
    const header = screen.getByTestId("tc-write-gate-row");
    return [...header.querySelectorAll("button")].map(
      (button) =>
        `${button.getAttribute("data-testid") ?? ""}:${(button.textContent ?? "").trim()}`,
    );
  }

  it("keeps the same controls, in the same order, with the same labels — reversible", () => {
    row({ params: [{ label: "repo", value: "Parth-test" }] });
    const collapsed = headerActions();
    fireEvent.click(screen.getByTestId("tc-write-gate-review"));
    expect(screen.getByTestId("tc-write-gate-body")).toBeTruthy();
    expect(headerActions()).toEqual(collapsed);
  });

  it("keeps the same controls, in the same order, with the same labels — irreversible", () => {
    row({ irreversible: true, params: [{ label: "count", value: "14" }] });
    const collapsed = headerActions();
    fireEvent.click(screen.getByTestId("tc-write-gate-review"));
    // The body approve appears BELOW the header, never inside it.
    expect(screen.getByTestId("tc-write-gate-body-approve")).toBeTruthy();
    expect(headerActions()).toEqual(collapsed);
  });
});

describe("TcWriteGateRow — the disclosure", () => {
  it("is a labelled chevron on a reversible ask, and says which way it goes", () => {
    // Expanding is OPTIONAL here — the decision can be made without it — so the
    // control is quiet furniture beside the buttons that matter.
    row();
    const chevron = screen.getByTestId("tc-write-gate-review");
    expect(chevron.getAttribute("aria-expanded")).toBe("false");
    expect(chevron.getAttribute("aria-label")).toBe("Show what it will send");
    fireEvent.click(chevron);
    expect(
      screen.getByTestId("tc-write-gate-review").getAttribute("aria-expanded"),
    ).toBe("true");
    expect(
      screen.getByTestId("tc-write-gate-review").getAttribute("aria-label"),
    ).toBe("Hide what it will send");
  });

  it("stays a NAMED button on an irreversible ask, where it is the only way through", () => {
    // A 22px unlabelled glyph as the single path forward is the dead-control
    // shape in another costume: Approve is withheld until the payload has been
    // seen, so this button is the primary action and has to look like one.
    row({ irreversible: true });
    const review = screen.getByRole("button", { name: /review/i });
    expect(review.getAttribute("data-testid")).toBe("tc-write-gate-review");
    expect(review.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(review);
    expect(screen.getByRole("button", { name: /review/i })).toBeTruthy();
  });
});

// The generalisation: everything gate-shaped is optional, so an ordinary tool
// approval — no connector, no ledger row, no canvas detail, no arguments —
// renders through the same card without a hole in it.
describe("TcWriteGateRow — an ordinary approval", () => {
  it("renders with nothing but a title and two decisions", () => {
    const onApprove = vi.fn();
    const onDecline = vi.fn();
    render(
      <TcWriteGateRow
        title="Send the weekly digest"
        onApprove={onApprove}
        onDecline={onDecline}
      />,
    );
    expect(screen.getByTestId("tc-write-gate-title").textContent).toBe(
      "Send the weekly digest",
    );
    expect(screen.queryByTestId("tc-write-gate-connector")).toBeNull();
    expect(screen.queryByTestId("tc-write-gate-chip")).toBeNull();
    expect(
      screen.getByTestId("tc-write-gate-row").getAttribute("data-risk"),
    ).toBe("normal");
    screen.getByTestId("tc-write-gate-approve").click();
    expect(onApprove).toHaveBeenCalledTimes(1);
  });

  it("expands with no host listening at all", () => {
    // `onReview` lost its last producer, so absent is the NORMAL case now — not
    // a degraded one. The card must not depend on it in any form.
    render(
      <TcWriteGateRow
        title="Send the weekly digest"
        onApprove={vi.fn()}
        onDecline={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByTestId("tc-write-gate-review"));
    expect(screen.getByTestId("tc-write-gate-body")).toBeTruthy();
    // Nothing to show, so nothing is framed — an empty frame reads as "it will
    // send nothing", which is a different claim from "we do not have it".
    expect(screen.queryByTestId("tc-write-gate-body-params")).toBeNull();
    expect(screen.queryByTestId("tc-write-gate-body-ledger-id")).toBeNull();
  });
});

describe("TcWriteGateRow — the meta line", () => {
  it("reads connector · access when both are known", () => {
    row({ access: "write" });
    expect(screen.getByTestId("tc-write-gate-connector").textContent).toBe(
      "linear · write",
    );
  });

  it("lower-cases the projection's enum rather than reaching for text-transform", () => {
    // `category.access` arrives as the projection's enum — READ or WRITE, and
    // nothing when the payload stated no axis. The kit's rule is that a status
    // label is lowercase AT THE SOURCE, so a screen reader hears what is on
    // screen — which a CSS transform cannot deliver.
    row({ access: "WRITE" });
    expect(screen.getByTestId("tc-write-gate-connector").textContent).toBe(
      "linear · write",
    );
  });

  it("drops the access half rather than printing a dangling separator", () => {
    row({ access: null });
    expect(screen.getByTestId("tc-write-gate-connector").textContent).toBe(
      "linear",
    );
  });

  it("shows nothing at all when there is no connector to attribute it to", () => {
    row({ connector: null, access: "write" });
    expect(screen.queryByTestId("tc-write-gate-connector")).toBeNull();
  });
});

describe("TcWriteGateRow — the irreversible chip", () => {
  it("says so in plain text, which is the only non-visual signal of risk", () => {
    // The dot is aria-hidden and `data-risk` is not an ARIA attribute, so
    // without this a screen-reader user cannot tell a destructive ask from an
    // ordinary one until they expand it.
    row({ irreversible: true });
    expect(screen.getByTestId("tc-write-gate-chip").textContent).toBe(
      "can't be undone",
    );
  });

  it("is absent on a reversible ask", () => {
    row();
    expect(screen.queryByTestId("tc-write-gate-chip")).toBeNull();
  });
});

describe("TcWriteGateRow — the reason line", () => {
  it("shows why the agent is asking, once expanded", () => {
    row({ reason: "This writes outside the chat." });
    expect(screen.queryByTestId("tc-write-gate-body-reason")).toBeNull();
    fireEvent.click(screen.getByTestId("tc-write-gate-review"));
    expect(screen.getByTestId("tc-write-gate-body-reason").textContent).toBe(
      "This writes outside the chat.",
    );
  });

  it("is omitted when there is no reason rather than printing an empty line", () => {
    row({ reason: "" });
    fireEvent.click(screen.getByTestId("tc-write-gate-review"));
    expect(screen.queryByTestId("tc-write-gate-body-reason")).toBeNull();
  });
});

// THE SHAPE THE BACKEND PROJECTED.
//
// `presentation` is the server's answer to "what kind of thing is this" —
// projected from the exact arguments the connector will receive, never from a
// parallel model-authored description. When the consolidated card dropped it,
// three things went with it, and only one of them was cosmetic: a batch of
// payees rendered as a params frame that did not contain the batch (a
// list-of-mappings is skipped by `buildParams`), a draft rendered as an
// untruncated `<dd>`, and every approve button said "Approve" over calls the
// backend had labelled "Approve & send" and "Approve & sign".
//
// The null case is asserted just as hard as the populated ones: the write-gate
// lane rides a wire shape that carries NO presentation, so `null` has to render
// byte-for-byte what this card rendered before shapes existed.

function shape(
  overrides: Partial<ApprovalPresentation> = {},
): ApprovalPresentation {
  return {
    layout: "params",
    approveLabel: null,
    rejectLabel: null,
    provenance: null,
    rows: [],
    preview: null,
    ...overrides,
  };
}

const DRAFT =
  "Launch Week is here.\n\nOver the next 7 days we're shipping one thing a day.";

describe("TcWriteGateRow — the approve verb the backend promised", () => {
  it("uses the projected verb on the header button, not a neutral Approve", () => {
    // "Approve & send" is the producer telling the reader what the click does.
    // It is the ONE field set on every presentation the backend emits, so
    // dropping it degrades every shaped approval at once.
    render(
      <TcWriteGateRow
        title="Post to #launch-aurora"
        presentation={shape({ approveLabel: "Approve & send" })}
        onApprove={vi.fn()}
        onDecline={vi.fn()}
      />,
    );
    expect(screen.getByTestId("tc-write-gate-approve").textContent).toBe(
      "Approve & send",
    );
  });

  it("uses the SAME verb on the body button — two controls, one promise", () => {
    // The irreversible lane approves from the body, the reversible lane from
    // the header. If the verb were applied to only one of them, the two
    // controls would disagree about what a click does — and which one you get
    // depends on a severity flag, not on the action.
    render(
      <TcWriteGateRow
        title="Sign the payout batch"
        irreversible
        params={[{ label: "wallet", value: "0x8f42" }]}
        presentation={shape({ approveLabel: "Approve & sign" })}
        onApprove={vi.fn()}
        onDecline={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByTestId("tc-write-gate-review"));
    expect(screen.getByTestId("tc-write-gate-body-approve").textContent).toBe(
      "Approve & sign",
    );
  });

  it("falls back to the neutral Approve when the wire named no verb", () => {
    render(
      <TcWriteGateRow
        title="Create an issue"
        presentation={shape({ approveLabel: null })}
        onApprove={vi.fn()}
        onDecline={vi.fn()}
      />,
    );
    expect(screen.getByTestId("tc-write-gate-approve").textContent).toBe(
      "Approve",
    );
  });

  it("keeps Decline as the refusal verb — the wire never names one", () => {
    // `reject_label` is set by no producer path, so this default is what has
    // always painted. Pinned so the prop cannot become a back door for
    // reverting the card's deliberate "Decline" copy.
    render(
      <TcWriteGateRow
        title="Post to #launch-aurora"
        presentation={shape({ approveLabel: "Approve & send" })}
        onApprove={vi.fn()}
        onDecline={vi.fn()}
      />,
    );
    expect(screen.getByTestId("tc-write-gate-decline").textContent).toBe(
      "Decline",
    );
  });

  it("does not move the buttons — a verb is state-invariant", () => {
    // The layout rule, re-pinned with a presentation in play: `approveLabel`
    // does not change with `open`, so the header stays byte-identical.
    render(
      <TcWriteGateRow
        title="Post to #launch-aurora"
        params={[{ label: "channel", value: "#launch-aurora" }]}
        presentation={shape({
          layout: "preview",
          approveLabel: "Approve & send",
          preview: { text: DRAFT, meta: "14 words · 71 characters" },
        })}
        onApprove={vi.fn()}
        onDecline={vi.fn()}
      />,
    );
    const header = screen.getByTestId("tc-write-gate-row");
    const labels = (): readonly string[] =>
      [...header.querySelectorAll("button")].map(
        (button) =>
          `${button.getAttribute("data-testid") ?? ""}:${(button.textContent ?? "").trim()}`,
      );
    const collapsed = labels();
    fireEvent.click(screen.getByTestId("tc-write-gate-review"));
    expect(screen.getByTestId("tc-write-gate-body-preview")).toBeTruthy();
    expect(labels()).toEqual(collapsed);
  });
});

describe("TcWriteGateRow — the preview shape", () => {
  function previewRow(over: Record<string, unknown> = {}) {
    return render(
      <TcWriteGateRow
        title="Post to #launch-aurora"
        connector="slack"
        params={[{ label: "channel", value: "#launch-aurora" }]}
        presentation={shape({
          layout: "preview",
          approveLabel: "Approve & send",
          preview: { text: DRAFT, meta: "14 words · 71 characters" },
        })}
        onApprove={vi.fn()}
        onDecline={vi.fn()}
        {...over}
      />,
    );
  }

  it("shows the draft itself, verbatim, once expanded", () => {
    // A params table can say the post is going to #launch-aurora. Only the
    // draft can say whether it should be sent.
    previewRow();
    expect(screen.queryByTestId("tc-write-gate-body-preview")).toBeNull();
    fireEvent.click(screen.getByTestId("tc-write-gate-review"));
    expect(
      screen.getByTestId("tc-write-gate-body-preview").textContent,
    ).toContain(DRAFT);
  });

  it("carries the volumetric meta WITH the draft, never as an extra", () => {
    // The frame scrolls and the producer truncates at 2000 characters, so this
    // line is what keeps a partial draft honest about how much there is.
    previewRow();
    fireEvent.click(screen.getByTestId("tc-write-gate-review"));
    expect(
      screen.getByTestId("tc-write-gate-body-preview-meta").textContent,
    ).toBe("14 words · 71 characters");
  });

  it("omits the meta rather than printing an empty line", () => {
    previewRow({
      presentation: shape({
        layout: "preview",
        preview: { text: DRAFT, meta: null },
      }),
    });
    fireEvent.click(screen.getByTestId("tc-write-gate-review"));
    expect(screen.getByTestId("tc-write-gate-body-preview")).toBeTruthy();
    expect(screen.queryByTestId("tc-write-gate-body-preview-meta")).toBeNull();
  });

  it("keeps the remaining arguments alongside the draft", () => {
    // The draft is the message; the params are where it is going. The
    // projection drops only the argument the preview already renders in full.
    previewRow();
    fireEvent.click(screen.getByTestId("tc-write-gate-review"));
    expect(
      screen.getByTestId("tc-write-gate-body-params").textContent,
    ).toContain("#launch-aurora");
  });

  it("renders the draft as a text node, never as markup", () => {
    // It originates in a model completion and may itself have come from tool
    // output.
    previewRow({
      presentation: shape({
        layout: "preview",
        preview: { text: "<img src=x onerror=alert(1)>", meta: null },
      }),
    });
    fireEvent.click(screen.getByTestId("tc-write-gate-review"));
    const preview = screen.getByTestId("tc-write-gate-body-preview");
    expect(preview.querySelector("img")).toBeNull();
    expect(preview.textContent).toContain("<img src=x onerror=alert(1)>");
  });
});

describe("TcWriteGateRow — the rows shape", () => {
  const BATCH: ApprovalPresentation = shape({
    layout: "rows",
    approveLabel: "Approve & sign",
    rows: [
      {
        label: "Mira Patel",
        value: "2,400 USDC",
        note: "design",
        initials: "MP",
        rowId: "p1",
        status: "pending",
        decidable: true,
      },
      {
        label: "leo.eth",
        value: "1,150 USDC",
        note: null,
        initials: "LE",
        rowId: "p2",
        status: "pending",
        decidable: true,
      },
    ],
  });

  function batchRow(over: Record<string, unknown> = {}) {
    return render(
      <TcWriteGateRow
        title="Sign the payout batch"
        connector="safe"
        presentation={BATCH}
        onApprove={vi.fn()}
        onDecline={vi.fn()}
        {...over}
      />,
    );
  }

  it("draws the batch as line items, not as a params dump", () => {
    // This is the loss that was ABSENCE rather than restyling: `buildParams`
    // keeps only primitive top-level arguments, so the list of mappings the
    // batch came from never reaches the params frame at all.
    batchRow();
    fireEvent.click(screen.getByTestId("tc-write-gate-review"));
    const rows = screen.getByTestId("tc-write-gate-body-rows");
    expect(rows.querySelectorAll("li")).toHaveLength(2);
    expect(rows.textContent).toContain("Mira Patel");
    expect(rows.textContent).toContain("2,400 USDC");
    expect(rows.textContent).toContain("leo.eth");
    expect(rows.textContent).toContain("1,150 USDC");
  });

  it("carries each row's monogram and note when it has them", () => {
    batchRow();
    fireEvent.click(screen.getByTestId("tc-write-gate-review"));
    const rows = screen.getByTestId("tc-write-gate-body-rows");
    expect(rows.textContent).toContain("MP");
    expect(rows.textContent).toContain("design");
  });

  it("hides the monogram from assistive tech — the label is the name", () => {
    batchRow();
    fireEvent.click(screen.getByTestId("tc-write-gate-review"));
    const monogram = screen
      .getByTestId("tc-write-gate-body-rows")
      .querySelector(".tc-write-gate__avatar");
    expect(monogram?.getAttribute("aria-hidden")).toBe("true");
  });

  it("renders read-only — there is no wire for a per-row decision", () => {
    // The host seam is `onApprove(approvalId)` → one `/decision` POST with no
    // per-row field. A per-row button would post the whole batch under a label
    // that says it posts one payee.
    batchRow();
    fireEvent.click(screen.getByTestId("tc-write-gate-review"));
    const rows = screen.getByTestId("tc-write-gate-body-rows");
    expect(rows.querySelectorAll("button")).toHaveLength(0);
  });

  it("counts as payload seen even with zero params", () => {
    // The safety interaction. `payloadSeen` used to be `params.length > 0`,
    // and a rows batch projects to ZERO params — so gating on params alone
    // would withhold Approve over a card that shows every line item it is
    // about to sign.
    const onApprove = vi.fn();
    batchRow({ irreversible: true, params: [], onApprove });
    fireEvent.click(screen.getByTestId("tc-write-gate-review"));
    fireEvent.click(screen.getByTestId("tc-write-gate-body-approve"));
    expect(onApprove).toHaveBeenCalledTimes(1);
    // And still never one click from the collapsed row.
    expect(screen.queryByTestId("tc-write-gate-approve")).toBeNull();
  });

  it("still withholds approve when the shape has nothing to draw", () => {
    // Attribution is not evidence: a presentation carrying only a verb and a
    // provenance line puts nothing on screen to consent to.
    render(
      <TcWriteGateRow
        title="Sign the payout batch"
        irreversible
        params={[]}
        presentation={shape({
          approveLabel: "Approve & sign",
          provenance: "Launch Week ops · Safe 3-of-5",
        })}
        onApprove={vi.fn()}
        onDecline={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByTestId("tc-write-gate-review"));
    expect(screen.getByTestId("tc-write-gate-body")).toBeTruthy();
    expect(screen.queryByTestId("tc-write-gate-body-approve")).toBeNull();
  });
});

describe("TcWriteGateRow — provenance", () => {
  // Unreachable from any real payload today — the projector passes
  // `provenance=` on none of its three return paths. Pinned as a degrading
  // line, NOT as evidence that the field arrives.
  it("prints which run and which account, in the body", () => {
    render(
      <TcWriteGateRow
        title="Sign the payout batch"
        presentation={shape({ provenance: "Launch Week ops · Safe 3-of-5" })}
        onApprove={vi.fn()}
        onDecline={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByTestId("tc-write-gate-review"));
    expect(
      screen.getByTestId("tc-write-gate-body-provenance").textContent,
    ).toBe("Launch Week ops · Safe 3-of-5");
  });

  it("is omitted when the wire carried none", () => {
    render(
      <TcWriteGateRow
        title="Sign the payout batch"
        presentation={shape({ approveLabel: "Approve & sign" })}
        onApprove={vi.fn()}
        onDecline={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByTestId("tc-write-gate-review"));
    expect(screen.queryByTestId("tc-write-gate-body-provenance")).toBeNull();
  });
});

describe("TcWriteGateRow — no presentation at all", () => {
  // The write-gate lane rides the `ask_a_question` wire shape, which carries
  // no presentation and no arguments. It is the MOST common ask, so "null
  // changes nothing" is the load-bearing case, not the edge case.
  function bare(presentation: ApprovalPresentation | null | undefined) {
    const { container, unmount } = render(
      <TcWriteGateRow
        title="Create an issue in Parth-test"
        connector="linear"
        access="WRITE"
        reason="This writes outside the chat."
        params={[{ label: "repo", value: "Parth-test" }]}
        ledgerId="r7f3·142"
        onApprove={vi.fn()}
        onDecline={vi.fn()}
        {...(presentation === undefined ? {} : { presentation })}
      />,
    );
    fireEvent.click(screen.getByTestId("tc-write-gate-review"));
    // React's `useId` for the accessible description is unique PER MOUNT, so
    // two renders of identical props differ by that value alone. Normalising it
    // is the point of this test surviving, not a weakening of it: the claim is
    // "null and undefined produce the same DOM", and an id React guarantees to
    // differ is the one thing that cannot be part of that claim.
    const html = container.innerHTML.replace(/_r_[0-9a-z]+_/g, "_rID_");
    unmount();
    return html;
  }

  it("renders exactly what an omitted presentation renders", () => {
    expect(bare(null)).toBe(bare(undefined));
  });

  it("adds no empty frame and no placeholder", () => {
    render(
      <TcWriteGateRow
        title="Create an issue in Parth-test"
        params={[{ label: "repo", value: "Parth-test" }]}
        presentation={null}
        onApprove={vi.fn()}
        onDecline={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByTestId("tc-write-gate-review"));
    expect(screen.queryByTestId("tc-write-gate-body-rows")).toBeNull();
    expect(screen.queryByTestId("tc-write-gate-body-preview")).toBeNull();
    expect(screen.queryByTestId("tc-write-gate-body-provenance")).toBeNull();
    expect(screen.getByTestId("tc-write-gate-approve").textContent).toBe(
      "Approve",
    );
    expect(screen.getByTestId("tc-write-gate-decline").textContent).toBe(
      "Decline",
    );
  });
});

// ── the header may clip anything EXCEPT the decision ────────────────────────
//
// The frame is `overflow: hidden`, so a row that does not fit is cut at its
// END — where the buttons are. The header's one unbounded string is the meta:
// its vendor half is an MCP server slug, arbitrary length, chosen by whoever
// registered the server. As `flex: none` with `white-space: nowrap` and no cap
// it could not give way, so a long slug in a narrow chat column pushed Approve
// and Decline past the clip — an approval nobody can act on, which is strictly
// worse than an approval nobody can fully read.
//
// jsdom runs no layout engine, so "at 240px the button is still on screen" is
// not measurable here and a fixed width in this file would be theatre. What
// decides the outcome IS measurable: flexbox resolves an over-wide line by
// shrink weight (factor × basis), and jsdom resolves the shorthands off the
// real stylesheet. So these read the shipped CSS through the real card's DOM
// and pin the ORDER in which the row gives way. A `flex: none` on the meta —
// the regression — fails the first assertion.
describe("TcWriteGateRow — the header's shrink order", () => {
  const here =
    typeof import.meta.dirname === "string"
      ? import.meta.dirname
      : dirname(fileURLToPath(import.meta.url));

  let sheet: HTMLStyleElement | null = null;

  function renderWithRealCss(): void {
    sheet = document.createElement("style");
    sheet.textContent = readFileSync(
      resolve(here, "review-surfaces.css"),
      "utf-8",
    );
    document.head.appendChild(sheet);
    render(
      <TcWriteGateRow
        title="Create an issue in Parth-test"
        // A real slug shape, long enough that no column fits it whole.
        connector="acme-corp-internal-tooling-linear-production-us-east-1"
        access="WRITE"
        presentation={{
          layout: "params",
          rows: [],
          preview: null,
          approveLabel: "Approve & sign",
          rejectLabel: null,
          provenance: null,
        }}
        onApprove={vi.fn()}
        onDecline={vi.fn()}
      />,
    );
  }

  afterEach(() => {
    sheet?.remove();
    sheet = null;
  });

  it("lets the unbounded connector slug ellipsise, so it cannot push anything", () => {
    renderWithRealCss();
    const meta = screen.getByTestId("tc-write-gate-connector");
    const style = globalThis.getComputedStyle(meta);
    // Shrinkable and floorless: the only item on the row that carries a string
    // nobody in this repo controls has to be the one that yields.
    expect(style.flexShrink).toBe("1");
    expect(style.minWidth).toBe("0px");
    // …and yields by ellipsising rather than by wrapping the row taller.
    expect(style.overflow).toBe("hidden");
    expect(style.textOverflow).toBe("ellipsis");
    expect(style.whiteSpace).toBe("nowrap");
  });

  it("never shrinks the decision controls, whatever else is on the row", () => {
    renderWithRealCss();
    const actions = document.querySelector(".tc-write-gate__actions");
    expect(actions).not.toBeNull();
    const style = globalThis.getComputedStyle(actions as Element);
    expect(style.flexShrink).toBe("0");
    // The guarantee is only worth having if it covers the controls themselves:
    // both live inside the box that cannot shrink.
    expect(actions?.contains(screen.getByTestId("tc-write-gate-approve"))).toBe(
      true,
    );
    expect(actions?.contains(screen.getByTestId("tc-write-gate-decline"))).toBe(
      true,
    );
    // The approve verb comes off the wire, so its width is not ours either —
    // which is exactly why the row must absorb width elsewhere.
    expect(screen.getByTestId("tc-write-gate-approve").textContent).toBe(
      "Approve & sign",
    );
  });

  it("gives the title zero shrink weight, so it takes the leftover instead of competing", () => {
    renderWithRealCss();
    const style = globalThis.getComputedStyle(
      screen.getByTestId("tc-write-gate-title"),
    );
    // `flex: 1` ⇒ basis 0 ⇒ shrink contribution (factor × basis) of zero: the
    // title occupies what is left after the fixed items, and ellipsises there.
    expect(style.flexBasis).toBe("0%");
    expect(style.minWidth).toBe("0px");
    expect(style.textOverflow).toBe("ellipsis");
  });

  it("wraps every string in the body, so none of them can push the controls out of reach", () => {
    // The row's shrink order above protects the HEADER from the header's own
    // content. This protects it from the BODY's, which is a different failure
    // and the one that actually shipped: the card is a grid, so an unbreakable
    // token in the body sets the body's min-content width, that becomes the
    // TRACK width, and the header — a grid item in the same track — stretches
    // to it and overflows a frame that is `overflow: hidden`. The decision
    // controls end up clipped out of reach, on a card whose entire job is to
    // collect a decision. Every string down here is authored by the model or
    // the connector, so none of them may be trusted to contain a space.
    sheet = document.createElement("style");
    sheet.textContent = readFileSync(
      resolve(here, "review-surfaces.css"),
      "utf-8",
    );
    document.head.appendChild(sheet);
    const unbreakable = "x".repeat(138);
    render(
      <TcWriteGateRow
        title="Create an issue in Parth-test"
        connector="linear"
        reason={`Copilot is asking before it writes ${unbreakable}`}
        params={[{ label: "title", value: unbreakable }]}
        presentation={{
          layout: "preview",
          rows: [],
          preview: { text: unbreakable, meta: unbreakable },
          approveLabel: null,
          rejectLabel: null,
          provenance: unbreakable,
        }}
        onApprove={vi.fn()}
        onDecline={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByTestId("tc-write-gate-review"));

    const body = document.querySelector(".tc-write-gate__body");
    expect(body).not.toBeNull();
    // Asserted over EVERY descendant rather than a list of classes, because the
    // rule is per-container for exactly that reason: the next element added to
    // this body inherits the protection instead of having to remember it.
    const nodes = [body as Element, ...(body as Element).querySelectorAll("*")];
    expect(nodes.length).toBeGreaterThan(4);
    for (const node of nodes) {
      expect(globalThis.getComputedStyle(node).overflowWrap).toBe("anywhere");
    }
  });

  it("keeps the risk chip unshrinkable — it is the only non-visual risk signal", () => {
    sheet = document.createElement("style");
    sheet.textContent = readFileSync(
      resolve(here, "review-surfaces.css"),
      "utf-8",
    );
    document.head.appendChild(sheet);
    render(
      <TcWriteGateRow
        title="Delete the production database"
        connector="acme-corp-internal-tooling-linear-production-us-east-1"
        access="WRITE"
        irreversible
        onApprove={vi.fn()}
        onDecline={vi.fn()}
      />,
    );
    const style = globalThis.getComputedStyle(
      screen.getByTestId("tc-write-gate-chip"),
    );
    // Fixed-length copy, so it costs the row nothing to keep it whole — and
    // ellipsising "can't be undone" is not a trade worth making.
    expect(style.flexShrink).toBe("0");
  });

  // The desktop CSS-shadowing trap: a host sheet re-declaring a package-owned
  // class name wins the cascade and would silently restore `flex: none` here,
  // with every assertion above still green.
  it("owns these rules itself — no host stylesheet re-declares them", () => {
    const root = join(here, "..", "..", "..", "..");
    for (const hostSheet of [
      join(root, "apps", "frontend", "src", "styles.css"),
      join(root, "apps", "desktop", "renderer", "desktop.css"),
    ]) {
      let css = "";
      try {
        css = readFileSync(hostSheet, "utf8");
      } catch {
        continue; // sheet absent in this checkout — nothing to shadow
      }
      expect(
        css.includes("tc-write-gate"),
        `${hostSheet} must not own the ask card's class names`,
      ).toBe(false);
    }
  });
});

// THE ANNOUNCEMENT, NOT THE MARKUP.
//
// Asserted through `toHaveAccessibleDescription` rather than by querying the
// hidden span, because the span existing proves nothing about what is
// announced.
//
// WHAT THIS DOES NOT COVER, stated so nobody reads it as more than it is: it
// does NOT pin the choice of `aria-describedby` over `aria-description`.
// Swapping them keeps all three tests green — jest-dom resolves both — so the
// reason for that choice is real-world AT support, which jsdom does not model,
// and it is recorded in the component instead. Do not "simplify" it to
// `aria-description` on the strength of a green suite here; the last time that
// was tried the computed description in a real browser was measurably empty.
describe("TcWriteGateRow — what a screen reader is told", () => {
  it("names the ask and says the run is waiting on it", () => {
    row();
    const card = screen.getByTestId("tc-write-gate");
    expect(card).toHaveAccessibleName(
      "Approval: Create an issue in Parth-test",
    );
    expect(card).toHaveAccessibleDescription(/paused on this decision/i);
    expect(card).toHaveAccessibleDescription(/nothing runs until you/i);
  });

  it("makes irreversibility audible, not just visible", () => {
    // `data-risk` is not an ARIA attribute and the risk dot is `aria-hidden`,
    // so before this the only non-visual carrier of "this cannot be undone" was
    // the chip's text — one node, easily missed, and absent entirely from the
    // card's own description.
    row({ irreversible: true });
    expect(screen.getByTestId("tc-write-gate")).toHaveAccessibleDescription(
      /cannot be undone/i,
    );
  });

  it("does not describe a reversible write as undoable-only-via-the-payload", () => {
    // The reversible arm must NOT inherit the destructive sentence; a card that
    // over-warns on every ordinary write teaches people to ignore the warning.
    row();
    expect(screen.getByTestId("tc-write-gate")).not.toHaveAccessibleDescription(
      /cannot be undone/i,
    );
  });
});

// ONCE vs ALWAYS — the durable arm.
//
// Two arms settle the same ask through different machinery: "once" is the
// header's Approve and a `/decision` POST, "always" is an OS confirm that mints
// a grant outliving the run. The properties worth pinning are the ones that stop
// the second from being reachable as if it were the first — it must not appear
// unasked, it must not be reachable from the collapsed card, it must NAME the
// folder, and its testid must sit outside the prefix five live journeys press to
// mean "Approve".
describe("TcWriteGateRow — the durable grant arm", () => {
  const SCOPE = {
    path: "/Users/ada/Documents/reports",
    folderName: "reports",
    mode: "read_only" as const,
    reason: "to summarise the quarter",
  };

  function grantArm(overrides: Partial<WriteGateGrantAlways> = {}) {
    return {
      request: SCOPE,
      state: "pending" as const,
      failureMessage: null,
      onGrant: vi.fn(),
      onCancel: vi.fn(),
      ...overrides,
    };
  }

  it("draws nothing at all when the wire offered no durable option", () => {
    // The default, and nearly every ask. An arm that appeared without the
    // producer offering one would be a folder grant nobody was asked for.
    row();
    fireEvent.click(screen.getByTestId("tc-write-gate-review"));
    expect(screen.queryByTestId("tc-write-gate-grant")).toBeNull();
    expect(screen.queryByTestId("tc-write-gate-grant-always")).toBeNull();
  });

  it("is unreachable from the collapsed card — the once arm is what one click buys", () => {
    // The header stays the once arm alone. Expanding is what licenses the
    // decision that outlives the run, the same shape as the irreversible lane's
    // withheld Approve and for the same reason.
    row({ grantAlways: grantArm() });
    expect(screen.queryByTestId("tc-write-gate-grant-always")).toBeNull();
    // …and the once arm is still right there, one click, unchanged.
    expect(screen.getByTestId("tc-write-gate-approve")).toBeTruthy();
  });

  it("names the folder in full, next to the control that hands it over", () => {
    // The producer withholds the whole option rather than offer it over a path
    // its own card would truncate ("consent to an ellipsis is not consent"), so
    // a control that did not print the path would undo that upstream care.
    row({ grantAlways: grantArm() });
    fireEvent.click(screen.getByTestId("tc-write-gate-review"));
    expect(screen.getByTestId("tc-write-gate-grant-path").textContent).toBe(
      "/Users/ada/Documents/reports",
    );
    const body = document.querySelector(".tc-write-gate__body");
    expect(body?.contains(screen.getByTestId("tc-write-gate-grant"))).toBe(
      true,
    );
  });

  it("hands the host the SCOPE, and only on a real press", () => {
    // Held locally rather than read back off the returned props: `row` merges
    // through `Partial<TcWriteGateRowProps>`, so `grantAlways` is optional
    // there and a `!` would be asserting away the very thing under test.
    const arm = grantArm();
    const props = row({ grantAlways: arm });
    fireEvent.click(screen.getByTestId("tc-write-gate-review"));
    expect(arm.onGrant).not.toHaveBeenCalled();
    screen.getByTestId("tc-write-gate-grant-always").click();
    expect(arm.onGrant).toHaveBeenCalledTimes(1);
    // The durable arm never resolves through the once POST — that would resume
    // the run without a grant, which is the defect the whole path exists to
    // prevent (an ungranted read answered with an empty listing and a tick).
    expect(props.onApprove).not.toHaveBeenCalled();
  });

  it("keeps its testid outside the prefix that means 'Approve'", () => {
    // Five live desktop journeys press Approve by
    // `[data-testid^=tc-chat-approval-approve-]`. This button does something
    // strictly larger, so the SHAPE of its name — not only the branch that
    // renders it — has to keep it out of their reach.
    row({
      grantAlways: grantArm(),
      approveTestId: "tc-chat-approval-approve-a1",
      grantAlwaysTestId: "tc-chat-approval-grant-always-a1",
    });
    fireEvent.click(screen.getByTestId("tc-write-gate-review"));
    const pressedByJourneys = document.querySelectorAll(
      "[data-testid^=tc-chat-approval-approve-]",
    );
    expect(pressedByJourneys).toHaveLength(1);
    expect(pressedByJourneys[0]).toBe(
      screen.getByTestId("tc-chat-approval-approve-a1"),
    );
  });

  it("shows a dialog in flight as cancellable, not as a dead button", () => {
    const arm = grantArm({ state: "granting" });
    row({ grantAlways: arm });
    fireEvent.click(screen.getByTestId("tc-write-gate-review"));
    expect(screen.queryByTestId("tc-write-gate-grant-always")).toBeNull();
    screen.getByTestId("tc-write-gate-grant-cancel").click();
    expect(arm.onCancel).toHaveBeenCalledTimes(1);
  });

  it("shows the host's failure verbatim and leaves the ask answerable", () => {
    // A failure rendered as "nothing happened" reads to the user as a decision
    // they made. The message is the only thing that says whether to retry.
    row({
      grantAlways: grantArm({
        state: "failed",
        failureMessage: "The disk is no longer mounted.",
      }),
    });
    fireEvent.click(screen.getByTestId("tc-write-gate-review"));
    expect(screen.getByTestId("tc-write-gate-grant-failure").textContent).toBe(
      "The disk is no longer mounted.",
    );
    // The same control IS the retry — one verb, one name, one width.
    expect(screen.getByTestId("tc-write-gate-grant-always")).toBeTruthy();
    // …and the run is still declinable and approvable-once meanwhile.
    expect(screen.getByTestId("tc-write-gate-decline")).toBeTruthy();
    expect(screen.getByTestId("tc-write-gate-approve")).toBeTruthy();
  });

  it("stops offering a folder it has already handed over", () => {
    row({ grantAlways: grantArm({ state: "granted" }) });
    fireEvent.click(screen.getByTestId("tc-write-gate-review"));
    expect(screen.queryByTestId("tc-write-gate-grant-always")).toBeNull();
    expect(screen.queryByTestId("tc-write-gate-grant-cancel")).toBeNull();
    // The folder is still named — it is what a revoke would be about.
    expect(screen.getByTestId("tc-write-gate-grant-path").textContent).toBe(
      "/Users/ada/Documents/reports",
    );
  });

  it("withholds the control when the ask named no access, and says why", () => {
    // `grantAccessLabel` returns null rather than guess, so a scope with an
    // unknown mode gets an explained refusal instead of a button handing over
    // access nobody can name. Same rule the folder card follows.
    row({
      grantAlways: grantArm({ request: { ...SCOPE, mode: null } }),
    });
    fireEvent.click(screen.getByTestId("tc-write-gate-review"));
    expect(
      screen.getByTestId("tc-write-gate-grant-unknown-access"),
    ).toBeTruthy();
    expect(
      screen.getByTestId("tc-write-gate-grant-always").hasAttribute("disabled"),
    ).toBe(true);
  });

  it("states the access it would grant, and the two mechanism promises", () => {
    row({ grantAlways: grantArm() });
    fireEvent.click(screen.getByTestId("tc-write-gate-review"));
    const note = document.querySelector(".tc-write-gate__grant-note");
    expect(note?.textContent).toBe(
      "Read-only · this folder only · revoke anytime",
    );
  });
});

// The header layout rule, applied to the thing most likely to break it next.
//
// The frame is `overflow: hidden` and clips at its END, where the decision
// controls are. That is why the durable arm is in the BODY: its subject is a
// host folder name of arbitrary length, and an unbounded item in the header
// makes Approve the thing that disappears in a narrow column. jsdom runs no
// layout, so what is asserted is the CONTRACT that decides the outcome — read
// through the real stylesheet, against the real card's DOM.
describe("TcWriteGateRow — the durable arm cannot clip the decision", () => {
  const here =
    typeof import.meta.dirname === "string"
      ? import.meta.dirname
      : dirname(fileURLToPath(import.meta.url));

  let sheet: HTMLStyleElement | null = null;

  function renderWithRealCss(): void {
    sheet = document.createElement("style");
    sheet.textContent = readFileSync(
      resolve(here, "review-surfaces.css"),
      "utf-8",
    );
    document.head.appendChild(sheet);
    render(
      <TcWriteGateRow
        title="Read the quarterly reports"
        connector="linear"
        access="READ"
        grantAlways={{
          // A real host path with no break opportunity in its longest segment —
          // the shape that set the body's min-content width and stretched the
          // header past the clip the last time this failed.
          request: {
            path: `/Volumes/Archive/${"x".repeat(138)}/reports`,
            folderName: "reports",
            mode: "read_only",
            reason: null,
          },
          state: "pending",
          failureMessage: null,
          onGrant: vi.fn(),
          onCancel: vi.fn(),
        }}
        onApprove={vi.fn()}
        onDecline={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByTestId("tc-write-gate-review"));
  }

  afterEach(() => {
    sheet?.remove();
    sheet = null;
  });

  it("adds NOTHING to the header — the row is the same items with and without it", () => {
    renderWithRealCss();
    const header = document.querySelector(".tc-write-gate__hd");
    expect(header).not.toBeNull();
    expect(header?.contains(screen.getByTestId("tc-write-gate-grant"))).toBe(
      false,
    );
    // The actions box is still exactly Decline + Approve + the disclosure.
    const actions = document.querySelector(".tc-write-gate__actions");
    expect(actions?.querySelectorAll("button")).toHaveLength(3);
    expect(globalThis.getComputedStyle(actions as Element).flexShrink).toBe(
      "0",
    );
  });

  it("lets the unbreakable path wrap rather than set the card's width", () => {
    renderWithRealCss();
    // Inherited from the per-container body rule, which exists so a block added
    // later gets the protection without having to remember it. Asserting it on
    // the path node is what proves the new block actually inherited it.
    expect(
      globalThis.getComputedStyle(
        screen.getByTestId("tc-write-gate-grant-path"),
      ).overflowWrap,
    ).toBe("anywhere");
  });

  it("keeps the durable control at its intrinsic width, never full-bleed", () => {
    renderWithRealCss();
    const actions = document.querySelector(".tc-write-gate__grant-actions");
    expect(actions).not.toBeNull();
    // A full-bleed durable action would outweigh the header's Approve by area
    // alone — the recommended choice is never the one that outlives the run.
    expect(globalThis.getComputedStyle(actions as Element).justifySelf).toBe(
      "start",
    );
  });

  it("owns its own class names — no host stylesheet re-declares them", () => {
    // The desktop CSS-shadowing trap: a host sheet re-declaring a package-owned
    // name wins the cascade, with every assertion above still green.
    const root = join(here, "..", "..", "..", "..");
    for (const hostSheet of [
      join(root, "apps", "frontend", "src", "styles.css"),
      join(root, "apps", "desktop", "renderer", "desktop.css"),
    ]) {
      let css = "";
      try {
        css = readFileSync(hostSheet, "utf8");
      } catch {
        continue; // sheet absent in this checkout — nothing to shadow
      }
      expect(
        css.includes("tc-write-gate__grant"),
        `${hostSheet} must not own the durable arm's class names`,
      ).toBe(false);
    }
  });
});
