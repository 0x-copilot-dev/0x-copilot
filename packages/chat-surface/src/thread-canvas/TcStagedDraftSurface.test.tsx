import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import {
  TcStagedDraftSurface,
  renderAuthorshipSpans,
} from "./TcStagedDraftSurface";
import type {
  LedgerAuthorshipSpan,
  LedgerStageRevision,
  LedgerStagedWrite,
} from "./ledgerProjection";

function revision(
  rev: number,
  author: "agent" | "user",
  spans: readonly LedgerAuthorshipSpan[] = [],
): LedgerStageRevision {
  return {
    rev,
    author,
    proposalRef: `draft://d1/v${rev}`,
    diffRef: `draft://d1/v${rev - 1}..v${rev}`,
    authorshipSpans: spans,
    seq: rev,
    ledgerId: `rrun1·00${rev}`,
  };
}

function stage(overrides: Partial<LedgerStagedWrite> = {}): LedgerStagedWrite {
  const revisions = overrides.revisions ?? [revision(1, "agent")];
  return {
    stageId: "stage_1",
    surfaceId: "surf_1",
    draftId: "d1",
    target: { connector: "gmail", op: "send" },
    latestRev: overrides.latestRev ?? revisions[revisions.length - 1].rev,
    approvedRev: null,
    status: "staged",
    revisions,
    decisions: [],
    createdSeq: 2,
    lastSeq: 3,
    ledgerId: "rrun1·002",
    latestRevision: revisions[revisions.length - 1],
    ...overrides,
  };
}

describe("renderAuthorshipSpans", () => {
  it("wraps only user spans, leaving agent regions plain", () => {
    const nodes = renderAuthorshipSpans("Dear team, launch Monday.", [
      { start: 18, end: 24, author: "user" },
    ]);
    render(<p>{nodes}</p>);
    const marks = screen.getAllByTestId("tc-staged-edit-span");
    expect(marks).toHaveLength(1);
    expect(marks[0]).toHaveTextContent("Monday");
  });

  it("skips out-of-range / overlapping spans without throwing", () => {
    const nodes = renderAuthorshipSpans("short", [
      { start: 2, end: 99, author: "user" }, // out of range
      { start: 0, end: 3, author: "agent" }, // agent, not marked
    ]);
    render(<p data-testid="wrap">{nodes}</p>);
    expect(screen.queryByTestId("tc-staged-edit-span")).toBeNull();
    expect(screen.getByTestId("wrap")).toHaveTextContent("short");
  });
});

describe("TcStagedDraftSurface", () => {
  const body = "Dear team, launch Monday.";

  it("renders the body with the rev badge and 'edited by you' spans", () => {
    render(
      <TcStagedDraftSurface
        stage={stage({
          latestRev: 2,
          revisions: [
            revision(1, "agent"),
            revision(2, "user", [{ start: 18, end: 24, author: "user" }]),
          ],
        })}
        bodyText={body}
        onSubmitEdit={vi.fn()}
        onApprove={vi.fn()}
        onReject={vi.fn()}
        onRestore={vi.fn()}
      />,
    );
    expect(screen.getByTestId("tc-staged-draft-rev")).toHaveTextContent(
      "rev 2",
    );
    expect(screen.getByTestId("tc-staged-edit-span")).toHaveTextContent(
      "Monday",
    );
    expect(screen.getByTestId("tc-staged-draft-access")).toHaveTextContent(
      "write · held",
    );
  });

  it("edit takeover submits base_rev + new content", () => {
    const onSubmitEdit = vi.fn();
    render(
      <TcStagedDraftSurface
        stage={stage({ latestRev: 1 })}
        bodyText={body}
        onSubmitEdit={onSubmitEdit}
        onApprove={vi.fn()}
        onReject={vi.fn()}
        onRestore={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByTestId("tc-staged-draft-edit"));
    const editor = screen.getByTestId("tc-staged-draft-editor");
    fireEvent.change(editor, {
      target: { value: "Dear team, launch Tuesday." },
    });
    fireEvent.click(screen.getByTestId("tc-staged-draft-save"));
    expect(onSubmitEdit).toHaveBeenCalledWith(
      "stage_1",
      1, // base_rev = current latest
      "Dear team, launch Tuesday.",
    );
  });

  it("rejected surface dims the body and offers Restore", () => {
    const onRestore = vi.fn();
    render(
      <TcStagedDraftSurface
        stage={stage({ status: "rejected" })}
        bodyText={body}
        onSubmitEdit={vi.fn()}
        onApprove={vi.fn()}
        onReject={vi.fn()}
        onRestore={onRestore}
      />,
    );
    // No Edit affordance once out of the staged state.
    expect(screen.queryByTestId("tc-staged-draft-edit")).toBeNull();
    fireEvent.click(screen.getByTestId("tc-approve-bar-restore"));
    expect(onRestore).toHaveBeenCalledWith("stage_1");
  });

  it("approved surface holds — no edit, decided note shown", () => {
    render(
      <TcStagedDraftSurface
        stage={stage({ status: "approved", approvedRev: 1 })}
        bodyText={body}
        onSubmitEdit={vi.fn()}
        onApprove={vi.fn()}
        onReject={vi.fn()}
        onRestore={vi.fn()}
      />,
    );
    expect(screen.queryByTestId("tc-staged-draft-edit")).toBeNull();
    expect(screen.getByTestId("tc-staged-draft-decided")).toHaveTextContent(
      "held for send",
    );
  });
});
