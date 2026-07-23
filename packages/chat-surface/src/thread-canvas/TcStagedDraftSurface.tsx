// Staged-draft surface (Generative Surfaces v2, PRD-D1). 🎨
//
// The message-archetype draft body a write stages onto. Renders directly from a
// `LedgerStagedWrite` (folded from the ledger) plus the hydrated body text the
// host supplies for the latest revision. Free-form full-body edit is a textarea
// takeover on "Edit"; on submit the host POSTs `/revisions {base_rev, content}`
// and a NEW revision folds back with server-computed authorship spans, which
// this surface highlights as "edited by you". Rejected ⇒ dimmed body + Restore.
//
// Pure presentational: no port/clock/browser reads; every action is a host
// callback threaded through the composed `TcApproveBar`. Kit-only styling
// (design-system recipes + tokens); no raw font-size / letter-spacing.

import { useEffect, useState } from "react";
import type { CSSProperties, ReactElement, ReactNode } from "react";

import { Badge } from "@0x-copilot/design-system";

import { TcApproveBar } from "./TcApproveBar";
import type {
  LedgerAuthorshipSpan,
  LedgerStagedWrite,
} from "./ledgerProjection";

export interface TcStagedDraftSurfaceProps {
  readonly stage: LedgerStagedWrite;
  /** Hydrated body text of the latest revision (host reads the draft snapshot). */
  readonly bodyText: string;
  /** Submit a free-form full-body edit against `baseRev` (host POSTs `/revisions`). */
  readonly onSubmitEdit: (
    stageId: string,
    baseRev: number,
    contentText: string,
  ) => void;
  readonly onApprove: (stageId: string, rev: number) => void;
  readonly onReject: (stageId: string, rev: number) => void;
  readonly onRestore: (stageId: string) => void;
  readonly busy?: boolean;
}

const rootStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: "var(--space-sm)",
};

const headerStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: "var(--space-sm)",
  flexWrap: "wrap",
  padding: "var(--space-md) var(--space-md) 0",
};

const spacerStyle: CSSProperties = { flex: "1 1 auto" };

const bodyStyle: CSSProperties = {
  whiteSpace: "pre-wrap",
  padding: "0 var(--space-md)",
  margin: 0,
};

const rejectedBodyStyle: CSSProperties = { ...bodyStyle, opacity: 0.5 };

const editAreaStyle: CSSProperties = {
  width: "100%",
  minHeight: 120,
  resize: "vertical",
  margin: "0 var(--space-md)",
};

const editActionsStyle: CSSProperties = {
  display: "flex",
  gap: "var(--space-sm)",
  padding: "0 var(--space-md)",
};

const footerStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: "var(--space-sm)",
  padding: "6px var(--space-md)",
  borderTop: "1px solid var(--color-border-subtle)",
};

const spanMarkStyle: CSSProperties = {
  background: "var(--color-highlight, rgba(255, 214, 0, 0.28))",
  borderRadius: 2,
};

const failedWarningStyle: CSSProperties = {
  padding: "0 var(--space-md)",
  margin: 0,
  color: "var(--color-text-warning, var(--color-text-secondary))",
};

/** Split `text` into segments, wrapping only the user-authored spans in a
 *  highlighted mark ("edited by you"). Agent regions stay plain. Spans are
 *  clamped to the text length and processed in order; overlaps/out-of-range
 *  spans are skipped so a malformed span never throws. */
export function renderAuthorshipSpans(
  text: string,
  spans: readonly LedgerAuthorshipSpan[],
): ReactNode[] {
  const userSpans = spans
    .filter(
      (s) =>
        s.author === "user" &&
        s.start >= 0 &&
        s.end <= text.length &&
        s.end > s.start,
    )
    .slice()
    .sort((a, b) => a.start - b.start);
  const out: ReactNode[] = [];
  let cursor = 0;
  let key = 0;
  for (const span of userSpans) {
    if (span.start < cursor) continue; // skip overlap
    if (span.start > cursor) {
      out.push(<span key={key++}>{text.slice(cursor, span.start)}</span>);
    }
    out.push(
      <mark
        key={key++}
        style={spanMarkStyle}
        data-testid="tc-staged-edit-span"
        title="edited by you"
      >
        {text.slice(span.start, span.end)}
      </mark>,
    );
    cursor = span.end;
  }
  if (cursor < text.length) {
    out.push(<span key={key++}>{text.slice(cursor)}</span>);
  }
  return out;
}

export function TcStagedDraftSurface({
  stage,
  bodyText,
  onSubmitEdit,
  onApprove,
  onReject,
  onRestore,
  busy = false,
}: TcStagedDraftSurfaceProps): ReactElement {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(bodyText);

  // Re-seed the editor when a new revision folds in (rev bump / restore).
  useEffect(() => {
    setDraft(bodyText);
    setEditing(false);
  }, [bodyText, stage.latestRev]);

  const isRejected = stage.status === "rejected";
  // PRD-D2: `applied` is terminal (the CommitEngine sent exactly the approved
  // rev); `failed` folds status back to `staged` with `applyResult === "failed"`,
  // so the bar returns for a fresh approve while the warning line stays visible.
  const isApplied = stage.status === "applied";
  const isApproved = stage.status === "approved";
  const applyFailed = stage.applyResult === "failed";
  const latest = stage.latestRevision;
  const spans = latest?.authorshipSpans ?? [];
  const editable = stage.status === "staged" && !applyFailed && !busy;

  return (
    <div className="ui-card" style={rootStyle} data-testid="tc-staged-draft">
      <div style={headerStyle}>
        <span className="ui-section-label" data-testid="tc-staged-draft-title">
          {stage.target.connector !== "" ? stage.target.connector : "Draft"}
        </span>
        <Badge tone="warning" data-testid="tc-staged-draft-rev">
          {`rev ${stage.latestRev}`}
        </Badge>
        <span style={spacerStyle} aria-hidden="true" />
        {editable && !editing ? (
          <button
            type="button"
            className="ui-button"
            onClick={() => setEditing(true)}
            data-testid="tc-staged-draft-edit"
          >
            Edit
          </button>
        ) : null}
      </div>

      {editing ? (
        <>
          <textarea
            className="ui-input"
            style={editAreaStyle}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            disabled={busy}
            data-testid="tc-staged-draft-editor"
          />
          <div style={editActionsStyle}>
            <button
              type="button"
              className="ui-button ui-button--primary"
              disabled={busy || draft === bodyText}
              onClick={() =>
                onSubmitEdit(stage.stageId, stage.latestRev, draft)
              }
              data-testid="tc-staged-draft-save"
            >
              Save as rev {stage.latestRev + 1}
            </button>
            <button
              type="button"
              className="ui-button"
              disabled={busy}
              onClick={() => {
                setDraft(bodyText);
                setEditing(false);
              }}
              data-testid="tc-staged-draft-cancel"
            >
              Cancel
            </button>
          </div>
        </>
      ) : (
        <p
          className="ui-body"
          style={isRejected ? rejectedBodyStyle : bodyStyle}
          data-testid="tc-staged-draft-body"
        >
          {renderAuthorshipSpans(bodyText, spans)}
        </p>
      )}

      {applyFailed ? (
        <p
          className="ui-caption"
          style={failedWarningStyle}
          data-testid="tc-staged-draft-failed"
          role="status"
        >
          {`Apply refused — nothing was sent${
            stage.applyFailureCode ? ` (${stage.applyFailureCode})` : ""
          }.`}
        </p>
      ) : null}

      <div style={footerStyle}>
        <Badge
          tone={isApplied ? "success" : "warning"}
          data-testid="tc-staged-draft-access"
        >
          {isApplied ? "write · sent" : "write · held"}
        </Badge>
        <span className="ui-mono-caps" data-testid="tc-staged-draft-ledger-id">
          {stage.ledgerId}
        </span>
        <span style={spacerStyle} aria-hidden="true" />
        {isApplied ? (
          <span className="ui-caption" data-testid="tc-staged-draft-applied">
            Sent — exactly the revision you approved.
          </span>
        ) : isApproved ? (
          <span className="ui-caption" data-testid="tc-staged-draft-decided">
            Approved — held for send.
          </span>
        ) : null}
      </div>

      {/* Terminal `applied` drops the approve bar (nothing left to decide); a
          failed apply keeps it so a fresh approve can retry. */}
      {!editing && !isApplied ? (
        <TcApproveBar
          stage={stage}
          onApprove={onApprove}
          onReject={onReject}
          onRestore={onRestore}
          busy={busy}
        />
      ) : null}
    </div>
  );
}
