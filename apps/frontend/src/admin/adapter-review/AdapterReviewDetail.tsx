// Adapter candidate detail (Phase 7C).
//
// Three-pane layout:
//   - Left: read-only candidate source (plain <pre>; no syntax-highlight
//     dep yet — the reviewer is reading anonymized JS, not editing it).
//   - Middle: layout template + the synthetic state the preview is
//     mounted against. Explicit so reviewers know what the candidate is
//     being tested on.
//   - Right: the iframe-sandboxed preview. Switches between "current"
//     and "diff" via the in-pane mode toggle.
//
// Footer: notes textarea + approve / reject / request-changes buttons.
// Each button fires the corresponding decision; on success, the
// candidate is re-fetched so the new decision shows in ``history``.

import type { ReactElement } from "react";
import { useEffect, useMemo, useState } from "react";

import { Badge, Button, Card, Field } from "@enterprise-search/design-system";

import { errorMessage } from "../../utils/errors";

import { AdapterPreview, type PreviewMode } from "./AdapterPreview";
import {
  decideAdapterReviewCandidate,
  getAdapterReviewCandidate,
} from "./adapterReviewApi";
import { syntheticStateFor } from "./SyntheticStateFactory";
import type {
  AdapterReviewCandidateDetail,
  AdapterReviewDecisionResponse,
  DecisionAction,
} from "./types";

export interface AdapterReviewDetailProps {
  readonly candidateId: string;
  readonly onBack: () => void;
}

export function AdapterReviewDetail({
  candidateId,
  onBack,
}: AdapterReviewDetailProps): ReactElement {
  const [candidate, setCandidate] =
    useState<AdapterReviewCandidateDetail | null>(null);
  const [status, setStatus] = useState<"loading" | "idle" | "error">("loading");
  const [errorText, setErrorText] = useState<string | null>(null);
  const [notes, setNotes] = useState<string>("");
  const [decisionStatus, setDecisionStatus] = useState<
    "idle" | "submitting" | "submitted" | "error"
  >("idle");
  const [decisionMessage, setDecisionMessage] = useState<string | null>(null);
  const [mode, setMode] = useState<PreviewMode>("diff");

  useEffect(() => {
    let cancelled = false;
    setStatus("loading");
    setErrorText(null);
    getAdapterReviewCandidate(candidateId).then(
      (result) => {
        if (cancelled) return;
        setCandidate(result);
        setStatus("idle");
      },
      (err: unknown) => {
        if (cancelled) return;
        setErrorText(errorMessage(err, "Failed to load candidate."));
        setStatus("error");
      },
    );
    return () => {
      cancelled = true;
    };
  }, [candidateId]);

  const syntheticState = useMemo(
    () =>
      candidate !== null ? syntheticStateFor(candidate.layout_template) : null,
    [candidate],
  );

  const decide = async (action: DecisionAction): Promise<void> => {
    if (candidate === null) return;
    setDecisionStatus("submitting");
    setDecisionMessage(null);
    try {
      const response: AdapterReviewDecisionResponse =
        await decideAdapterReviewCandidate(candidate.candidate_id, {
          action,
          notes,
        });
      setDecisionStatus("submitted");
      setDecisionMessage(`Decision recorded: ${response.action}`);
      // Re-fetch so the new decision shows in the history pane.
      const refreshed = await getAdapterReviewCandidate(candidate.candidate_id);
      setCandidate(refreshed);
      setNotes("");
    } catch (err: unknown) {
      setDecisionStatus("error");
      setDecisionMessage(errorMessage(err, "Failed to record decision."));
    }
  };

  if (status === "loading") {
    return (
      <p data-testid="adapter-review-detail-loading">Loading candidate…</p>
    );
  }
  if (status === "error" || candidate === null || syntheticState === null) {
    return (
      <section data-testid="adapter-review-detail">
        <Button
          variant="ghost"
          onClick={onBack}
          data-testid="adapter-review-back"
        >
          ← Back to queue
        </Button>
        <p
          role="alert"
          data-testid="adapter-review-detail-error"
          style={{ color: "var(--color-text-danger)" }}
        >
          {errorText ?? "Candidate not available."}
        </p>
      </section>
    );
  }

  return (
    <section
      data-testid="adapter-review-detail"
      style={{ display: "flex", flexDirection: "column", gap: 12 }}
    >
      <header
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          gap: 8,
        }}
      >
        <div>
          <Button
            variant="ghost"
            onClick={onBack}
            data-testid="adapter-review-back"
          >
            ← Back to queue
          </Button>
          <h2 style={{ margin: "4px 0" }}>{candidate.candidate_id}</h2>
          <div
            style={{ display: "flex", gap: 8, alignItems: "center" }}
            data-testid="adapter-review-meta"
          >
            <Badge tone="neutral">{candidate.scheme}</Badge>
            <Badge tone="accent">{candidate.layout_template}</Badge>
            <Badge tone="neutral">
              origin: {candidate.origin_tenant_redacted}
            </Badge>
            <Badge tone="neutral">{candidate.generator_model}</Badge>
            <Badge tone="neutral">submitted {candidate.submitted_at}</Badge>
          </div>
        </div>
        <Badge tone="accent" data-testid="adapter-review-status">
          {candidate.status}
        </Badge>
      </header>

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "1fr 1fr 1fr",
          gap: 12,
          minHeight: 360,
        }}
      >
        <Card
          data-testid="adapter-review-source-pane"
          style={{
            display: "flex",
            flexDirection: "column",
            gap: 8,
            overflow: "hidden",
          }}
        >
          <header style={{ display: "flex", justifyContent: "space-between" }}>
            <strong>Candidate source</strong>
            <Badge tone="neutral">read-only</Badge>
          </header>
          <pre
            data-testid="adapter-review-source"
            style={{
              margin: 0,
              padding: 8,
              fontFamily: "ui-monospace, SFMono-Regular, 'SF Mono', monospace",
              fontSize: "var(--font-size-xs)",
              lineHeight: "var(--line-height-snug)",
              background: "var(--color-surface-muted)",
              borderRadius: 6,
              overflow: "auto",
              maxHeight: 320,
              whiteSpace: "pre-wrap",
              overflowWrap: "anywhere",
            }}
          >
            {candidate.candidate_source}
          </pre>
        </Card>

        <Card
          data-testid="adapter-review-state-pane"
          style={{
            display: "flex",
            flexDirection: "column",
            gap: 8,
            overflow: "hidden",
          }}
        >
          <header style={{ display: "flex", justifyContent: "space-between" }}>
            <strong>Synthetic state ({candidate.layout_template})</strong>
            <Badge tone="warning">synthetic only</Badge>
          </header>
          <p
            style={{
              margin: 0,
              fontSize: "var(--font-size-xs)",
              color: "var(--color-text-muted)",
            }}
          >
            Reviewers never see tenant-private data. The preview is mounted
            against the values below — no real customer fields are attached.
          </p>
          <pre
            data-testid="adapter-review-state"
            style={{
              margin: 0,
              padding: 8,
              fontFamily: "ui-monospace, SFMono-Regular, 'SF Mono', monospace",
              fontSize: "var(--font-size-xs)",
              background: "var(--color-surface-muted)",
              borderRadius: 6,
              overflow: "auto",
              maxHeight: 320,
              whiteSpace: "pre-wrap",
              overflowWrap: "anywhere",
            }}
          >
            {JSON.stringify(
              mode === "diff" ? syntheticState.diff : syntheticState.current,
              null,
              2,
            )}
          </pre>
          <div
            style={{ display: "flex", gap: 8 }}
            data-testid="adapter-review-mode-toggle"
          >
            <Button
              size="sm"
              variant={mode === "current" ? "primary" : "secondary"}
              onClick={() => setMode("current")}
              data-testid="adapter-review-mode-current"
            >
              Current
            </Button>
            <Button
              size="sm"
              variant={mode === "diff" ? "primary" : "secondary"}
              onClick={() => setMode("diff")}
              data-testid="adapter-review-mode-diff"
            >
              Diff
            </Button>
          </div>
        </Card>

        <AdapterPreview
          candidateSource={candidate.candidate_source}
          state={syntheticState}
          mode={mode}
        />
      </div>

      <Card
        data-testid="adapter-review-decisions"
        style={{ display: "flex", flexDirection: "column", gap: 8 }}
      >
        <Field label="Reviewer notes">
          <textarea
            data-testid="adapter-review-notes"
            value={notes}
            onChange={(event) => setNotes(event.target.value)}
            rows={3}
            style={{
              width: "100%",
              padding: 8,
              border: "1px solid var(--color-border)",
              borderRadius: 6,
              background: "var(--color-surface)",
              color: "var(--color-text)",
              fontFamily: "inherit",
              fontSize: "var(--font-size-sm)",
            }}
          />
        </Field>
        <div style={{ display: "flex", gap: 8 }}>
          <Button
            variant="primary"
            disabled={decisionStatus === "submitting"}
            onClick={() => void decide("approve")}
            data-testid="adapter-review-approve"
          >
            Approve
          </Button>
          <Button
            variant="secondary"
            disabled={decisionStatus === "submitting"}
            onClick={() => void decide("request-changes")}
            data-testid="adapter-review-request-changes"
          >
            Request changes
          </Button>
          <Button
            variant="danger"
            disabled={decisionStatus === "submitting"}
            onClick={() => void decide("reject")}
            data-testid="adapter-review-reject"
          >
            Reject
          </Button>
        </div>
        {decisionMessage !== null ? (
          <p
            role="status"
            data-testid="adapter-review-decision-message"
            style={{
              margin: 0,
              color:
                decisionStatus === "error"
                  ? "var(--color-text-danger)"
                  : "var(--color-text-muted)",
            }}
          >
            {decisionMessage}
          </p>
        ) : null}
      </Card>

      <Card
        data-testid="adapter-review-history"
        style={{ display: "flex", flexDirection: "column", gap: 8 }}
      >
        <strong>Decision history</strong>
        {candidate.history.length === 0 ? (
          <p style={{ margin: 0, color: "var(--color-text-muted)" }}>
            No prior decisions on this candidate.
          </p>
        ) : (
          <ol style={{ margin: 0, paddingLeft: 16 }}>
            {candidate.history.map((entry, idx) => (
              <li
                key={`${entry.decided_at}-${idx}`}
                data-testid={`adapter-review-history-${idx}`}
              >
                <strong>{entry.action}</strong>
                {` by ${entry.decided_by_user_id} at ${entry.decided_at}`}
                {entry.notes ? ` — ${entry.notes}` : null}
              </li>
            ))}
          </ol>
        )}
      </Card>
    </section>
  );
}
