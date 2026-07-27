/* Real staged-draft and staged-table components for v3 computed-style parity. */
import { fireEvent, render, screen, cleanup } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it } from "vitest";
import { copyFileSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import {
  TcStagedDraftSurface,
  TcStagedTableSurface,
  type LedgerStageRevision,
  type LedgerStagedRow,
  type LedgerStagedWrite,
} from "@0x-copilot/chat-surface";

const HERE = (path: string): string =>
  fileURLToPath(new URL(path, import.meta.url));
const REPO = (path: string): string => HERE(`../../../${path}`);
const LIVE = (path: string): string =>
  HERE(`../surfaces/generative-surfaces-v3/live/${path}`);
const noop = (): void => {};

const BODY =
  "Hi Priya — good news: the checkout fix (ENG-142) is in review and on track to ship Thursday, Feb 12, ahead of your Friday webinar.\n\nDana's PR covers the session-refresh path, and we'll confirm here the moment it's deployed.\n\nI'll send release notes once it's live. — Alex";
const MESSAGE_PRESENTATION = {
  from: "alex@northbeam.co · via 0xCopilot",
  to: "Priya Shah <priya@harborline.io>",
  subject: "Re: Checkout fix — timeline?",
  quotedLabel: "Yesterday 4:12 PM — Priya wrote",
  quotedBody:
    "Hi Alex — any update on the checkout regression? We're running a customer webinar Friday and I'd love to say the fix is live. — P",
} as const;

function revision(): LedgerStageRevision {
  return {
    rev: 1,
    author: "agent",
    proposalRef: "draft://gmail/reply/v1",
    diffRef: "draft://gmail/reply/v0..v1",
    authorshipSpans: [],
    seq: 4,
    ledgerId: "rv3·004",
  };
}

function draftStage(): LedgerStagedWrite {
  const latest = revision();
  return {
    stageId: "stage_v3_draft",
    surfaceId: "surface_v3_draft",
    draftId: "draft_v3_reply",
    target: {
      connector: "gmail.drafts",
      op: "create → messages.send",
    },
    latestRev: 1,
    approvedRev: null,
    status: "staged",
    revisions: [latest],
    decisions: [],
    createdSeq: 3,
    lastSeq: 4,
    ledgerId: "gv-02",
    latestRevision: latest,
    applyResult: null,
    applyFailureCode: null,
    rows: null,
    rowCounts: null,
  };
}

function row(
  rowKey: string,
  title: string,
  stance: "will_apply" | "held",
  outcome: "applied" | "failed" | null = null,
  holdReason: string | null = null,
): LedgerStagedRow {
  return {
    rowKey,
    title,
    changes: [{ field: "stage", old: "Negotiation", new: "Closed-Lost" }],
    stance,
    agentHoldReason: holdReason,
    decidedBy: holdReason === null ? null : "agent",
    applyOutcome: outcome,
  };
}

function tableStage(partial: boolean): LedgerStagedWrite {
  const titles = [
    "Meridian Health — renewal",
    "Anchor Logistics — seat expansion",
    "Bluepine — platform pilot",
    "Corsair Labs — annual",
    "Halcyon Media — upsell",
    "Northwind Retail — POS rollout",
    "Osprey Financial — API tier",
    "Tessellate — starter plan",
  ];
  const rows = partial
    ? titles.map((title, index) =>
        index === 0 || index === 5
          ? row(`opp-${index + 1}`, title, "will_apply", "failed")
          : index === 4 || index === 6
            ? row(
                `opp-${index + 1}`,
                title,
                "held",
                null,
                index === 4
                  ? "Contact replied 12d ago"
                  : "Renewal call yesterday",
              )
            : row(`opp-${index + 1}`, title, "will_apply", "applied"),
      )
    : titles.map((title, index) =>
        index === 2
          ? row(`opp-${index + 1}`, title, "held")
          : index === 4 || index === 6
            ? row(
                `opp-${index + 1}`,
                title,
                "held",
                null,
                index === 4
                  ? "Contact replied 12d ago"
                  : "Renewal call yesterday",
              )
            : row(`opp-${index + 1}`, title, "will_apply"),
      );
  return {
    stageId: "stage_v3_bulk",
    surfaceId: "surface_v3_bulk",
    draftId: "",
    target: {
      connector: "Salesforce",
      op: "opportunities.update",
    },
    latestRev: 1,
    approvedRev: partial ? 1 : null,
    status: partial ? "partially_applied" : "staged",
    revisions: [],
    decisions: [],
    createdSeq: 8,
    lastSeq: 9,
    ledgerId: "rv3·008",
    latestRevision: null,
    applyResult: partial ? "partial" : null,
    applyFailureCode: null,
    rows,
    rowCounts: {
      total: rows.length,
      willApply: rows.filter((item) => item.stance === "will_apply").length,
      held: rows.filter((item) => item.stance === "held").length,
      applied: rows.filter((item) => item.applyOutcome === "applied").length,
      failed: rows.filter((item) => item.applyOutcome === "failed").length,
    },
  };
}

function html(state: string, body: string): string {
  return `<!doctype html>
<html lang="en" data-theme="dark" data-density="comfortable">
  <head>
    <meta charset="utf-8" />
    <title>design-parity · Generative Surfaces v3 · ${state} · LIVE</title>
    <link rel="icon" href="data:," />
    <link rel="stylesheet" href="./styles.css" />
    <style>
      html, body { margin: 0; min-height: 100%; background: var(--color-bg); }
      *, *::before, *::after { animation: none !important; }
      #parity-frame { box-sizing: border-box; display: flex; width: 795px; height: 508.562px; padding: 22px; }
    </style>
  </head>
  <body><div id="parity-frame" data-state="${state}">${body}</div></body>
</html>`;
}

function persist(state: string, container: HTMLElement): void {
  writeFileSync(LIVE(`${state}.html`), html(state, container.innerHTML));
}

describe("live Generative Surfaces v3 review fixtures", () => {
  beforeAll(() => {
    mkdirSync(LIVE(""), { recursive: true });
    mkdirSync(LIVE("fonts"), { recursive: true });
    writeFileSync(
      LIVE("styles.css"),
      [
        readFileSync(REPO("packages/design-system/src/styles.css"), "utf8"),
        readFileSync(
          REPO("packages/chat-surface/src/thread-canvas/review-surfaces.css"),
          "utf8",
        ),
      ].join("\n"),
    );
    for (const font of [
      "instrument-sans-latin.woff2",
      "instrument-sans-latin-ext-italic.woff2",
      "instrument-sans-latin-ext.woff2",
      "instrument-sans-latin-italic.woff2",
      "jetbrains-mono-latin.woff2",
      "jetbrains-mono-latin-ext.woff2",
      "space-grotesk-latin.woff2",
      "space-grotesk-latin-ext.woff2",
    ]) {
      copyFileSync(
        REPO(`packages/design-system/src/fonts/${font}`),
        LIVE(`fonts/${font}`),
      );
    }
  });
  afterEach(() => cleanup());

  it("renders draft-held", () => {
    const { container } = render(
      <TcStagedDraftSurface
        stage={draftStage()}
        bodyText={BODY}
        presentation={MESSAGE_PRESENTATION}
        onSubmitEdit={noop}
        onApprove={noop}
        onReject={noop}
        onRestore={noop}
      />,
    );
    expect(screen.getByTestId("tc-approve-bar")).not.toBeNull();
    persist("draft-held", container);
  });

  it("renders draft-edit", () => {
    const { container } = render(
      <TcStagedDraftSurface
        stage={draftStage()}
        bodyText={BODY}
        presentation={MESSAGE_PRESENTATION}
        onSubmitEdit={noop}
        onApprove={noop}
        onReject={noop}
        onRestore={noop}
      />,
    );
    fireEvent.click(screen.getByTestId("tc-staged-draft-edit"));
    const editor = screen.getByTestId(
      "tc-staged-draft-editor",
    ) as HTMLTextAreaElement;
    fireEvent.change(editor, {
      target: {
        value:
          "Dana's PR covers the session-refresh path, and we'll confirm the Thursday release window.",
      },
    });
    persist("draft-edit", container);
  });

  it("renders bulk-review", () => {
    const { container } = render(
      <TcStagedTableSurface
        stage={tableStage(false)}
        title="8 opportunities → Closed-Lost"
        summary="5 approved · 1 stale · 2 held"
        reviewNotice="1 row is stale — re-stage it before it can apply. Held rows stay untouched."
        onRowDecision={noop}
        onApply={noop}
      />,
    );
    expect(screen.getByTestId("tc-bulk-apply-bar")).not.toBeNull();
    persist("bulk-review", container);
  });

  it("renders bulk-partial", () => {
    const { container } = render(
      <TcStagedTableSurface
        stage={tableStage(true)}
        title="8 opportunities → Closed-Lost"
        onRowDecision={noop}
        onApply={noop}
      />,
    );
    expect(screen.getAllByTestId("tc-table-row-outcome")).toHaveLength(6);
    persist("bulk-partial", container);
  });
});
