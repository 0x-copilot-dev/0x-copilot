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
import type { ReactElement, ReactNode } from "react";

import { TcApproveBar } from "./TcApproveBar";
import type {
  LedgerAuthorshipSpan,
  LedgerStagedWrite,
} from "./ledgerProjection";

export interface StagedMessagePresentation {
  readonly from?: string;
  readonly to?: string;
  readonly subject?: string;
  readonly quotedLabel?: string;
  readonly quotedBody?: string;
}

export interface TcStagedDraftSurfaceProps {
  readonly stage: LedgerStagedWrite;
  /** Hydrated body text of the latest revision (host reads the draft snapshot). */
  readonly bodyText: string;
  /** Safe, connector-neutral display metadata; absent fields are not invented. */
  readonly presentation?: StagedMessagePresentation;
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
        className="tc-review-draft__user-edit"
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

function paragraphRanges(text: string): readonly {
  readonly text: string;
  readonly start: number;
}[] {
  const ranges: { text: string; start: number }[] = [];
  const separator = /\n{2,}/g;
  let start = 0;
  for (const match of text.matchAll(separator)) {
    const end = match.index ?? start;
    ranges.push({ text: text.slice(start, end), start });
    start = end + match[0].length;
  }
  ranges.push({ text: text.slice(start), start });
  return ranges.filter((range) => range.text !== "");
}

function spansForParagraph(
  spans: readonly LedgerAuthorshipSpan[],
  start: number,
  length: number,
): readonly LedgerAuthorshipSpan[] {
  const end = start + length;
  return spans.flatMap((span) => {
    const overlapStart = Math.max(span.start, start);
    const overlapEnd = Math.min(span.end, end);
    if (overlapEnd <= overlapStart) return [];
    return [
      {
        start: overlapStart - start,
        end: overlapEnd - start,
        author: span.author,
      },
    ];
  });
}

export function TcStagedDraftSurface({
  stage,
  bodyText,
  presentation,
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
  const hasMessageMetadata =
    presentation?.from !== undefined ||
    presentation?.to !== undefined ||
    presentation?.subject !== undefined;

  return (
    <div
      className="tc-review-surface tc-review-surface--draft"
      data-testid="tc-staged-draft"
      data-state={stage.status}
    >
      <div className="tc-review-surface__scroll">
        <div
          className={`tc-review-draft__frame${
            isRejected ? " tc-review-draft__frame--rejected" : ""
          }`}
        >
          <article className="tc-review-draft__message">
            {hasMessageMetadata ? (
              <header className="tc-review-draft__metadata">
                {presentation?.from !== undefined ? (
                  <div className="tc-review-draft__metadata-row">
                    <span className="tc-review-draft__metadata-label">
                      From
                    </span>
                    <span>{presentation.from}</span>
                  </div>
                ) : null}
                {presentation?.to !== undefined ? (
                  <div className="tc-review-draft__metadata-row">
                    <span className="tc-review-draft__metadata-label">To</span>
                    <span>{presentation.to}</span>
                  </div>
                ) : null}
                {presentation?.subject !== undefined ? (
                  <div className="tc-review-draft__metadata-row">
                    <span className="tc-review-draft__metadata-label">
                      Subject
                    </span>
                    <strong>{presentation.subject}</strong>
                  </div>
                ) : null}
              </header>
            ) : null}

            {presentation?.quotedBody !== undefined ? (
              <blockquote className="tc-review-draft__quote">
                {presentation.quotedLabel !== undefined ? (
                  <cite>{presentation.quotedLabel}</cite>
                ) : null}
                <p>{presentation.quotedBody}</p>
              </blockquote>
            ) : null}

            <div className="tc-review-draft__content">
              <div className="tc-review-draft__header">
                <span
                  className="tc-review-draft__revision"
                  data-testid="tc-staged-draft-rev"
                >
                  {`DRAFT · rev ${stage.latestRev}`}
                </span>
                <span className="tc-review-draft__authorship">
                  <span aria-hidden="true" />
                  0xCopilot wrote
                </span>
              </div>

              {editing ? (
                <div className="tc-review-draft__editor-wrap">
                  <textarea
                    className="ui-input tc-review-draft__editor"
                    value={draft}
                    onChange={(e) => setDraft(e.target.value)}
                    disabled={busy}
                    data-testid="tc-staged-draft-editor"
                  />
                  <div className="tc-review-draft__editor-actions">
                    <button
                      type="button"
                      className="ui-button ui-button--sm ui-button--ghost"
                      disabled={busy}
                      onClick={() => {
                        setDraft(bodyText);
                        setEditing(false);
                      }}
                      data-testid="tc-staged-draft-cancel"
                    >
                      Cancel
                    </button>
                    <button
                      type="button"
                      className="ui-button ui-button--sm"
                      disabled={busy || draft === bodyText}
                      onClick={() =>
                        onSubmitEdit(stage.stageId, stage.latestRev, draft)
                      }
                      data-testid="tc-staged-draft-save"
                    >
                      Done editing
                    </button>
                  </div>
                </div>
              ) : (
                <div className="tc-review-draft__body-group">
                  {paragraphRanges(bodyText).map((paragraph, index, all) => (
                    <p
                      key={`${paragraph.start}-${index}`}
                      className={`tc-review-draft__body${
                        index === all.length - 1
                          ? " tc-review-draft__body--last"
                          : ""
                      }`}
                      data-testid={
                        index === 0 ? "tc-staged-draft-body" : undefined
                      }
                    >
                      {renderAuthorshipSpans(
                        paragraph.text,
                        spansForParagraph(
                          spans,
                          paragraph.start,
                          paragraph.text.length,
                        ),
                      )}
                    </p>
                  ))}
                </div>
              )}

              {applyFailed ? (
                <p
                  className="tc-review-draft__warning"
                  data-testid="tc-staged-draft-failed"
                  role="status"
                >
                  {`Apply refused — nothing was sent${
                    stage.applyFailureCode ? ` (${stage.applyFailureCode})` : ""
                  }.`}
                </p>
              ) : null}
            </div>
          </article>
        </div>
      </div>

      {/* The rev-pinned safety context remains mounted during editing. Its
          effect actions are suspended until the edit is saved or cancelled. */}
      {!isApplied ? (
        <TcApproveBar
          stage={stage}
          onApprove={onApprove}
          onReject={onReject}
          onRestore={onRestore}
          onEdit={editable ? () => setEditing(true) : undefined}
          busy={busy}
          suspended={editing}
        />
      ) : null}

      <footer className="tc-review-provenance">
        <span className="tc-review-provenance__kind">Message</span>
        <span
          className="tc-review-provenance__detail"
          data-testid="tc-staged-draft-access"
        >
          {`${stage.target.connector}.${stage.target.op} · ${
            isApplied ? "write · sent" : "write · held for approval"
          } · `}
          <span data-testid="tc-staged-draft-ledger-id">{stage.ledgerId}</span>
        </span>
        {isApplied ? (
          <span
            className="tc-review-provenance__result"
            data-testid="tc-staged-draft-applied"
          >
            Sent — exactly the revision you approved.
          </span>
        ) : isApproved ? (
          <span
            className="tc-review-provenance__result"
            data-testid="tc-staged-draft-decided"
          >
            Approved — held for send.
          </span>
        ) : null}
      </footer>
    </div>
  );
}
