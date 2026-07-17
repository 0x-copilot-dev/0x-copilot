// MemoryProposalsRoute — `/memory/proposals` data binder. Lists
// pending auto-extraction proposals (sub-PRD §9.1) and owns the
// accept / reject mutations (§4.2). Server pre-filters by caller's
// user_id; we always request `status=pending` for the queue view.

import { useCallback, useEffect, useState, type ReactElement } from "react";

import type {
  MemoryProposal,
  MemoryProposalListResponse,
} from "@0x-copilot/api-types";

import type { RequestIdentity } from "../../api/config";
import {
  acceptMemoryProposal,
  fetchMemoryProposals,
  rejectMemoryProposal,
} from "../../api/memoryApi";
import { errorMessage } from "../../utils/errors";

type ViewState =
  | { readonly kind: "loading" }
  | { readonly kind: "error"; readonly message: string }
  | {
      readonly kind: "ready";
      readonly proposals: ReadonlyArray<MemoryProposal>;
    };

interface MemoryProposalsRouteProps {
  readonly identity: RequestIdentity;
  readonly onClose: () => void;
}

export function MemoryProposalsRoute({
  identity,
  onClose,
}: MemoryProposalsRouteProps): ReactElement {
  const [state, setState] = useState<ViewState>({ kind: "loading" });
  const [pendingError, setPendingError] = useState<string | null>(null);

  const load = useCallback((): (() => void) => {
    let cancelled = false;
    setState({ kind: "loading" });

    fetchMemoryProposals(identity, { status: "pending" })
      .then((res: MemoryProposalListResponse) => {
        if (cancelled) return;
        setState({ kind: "ready", proposals: res.proposals });
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setState({
          kind: "error",
          message: errorMessage(err, "Could not load proposals."),
        });
      });

    return () => {
      cancelled = true;
    };
  }, [identity]);

  useEffect(() => load(), [load]);

  const dropProposal = useCallback((id: string): void => {
    setState((prev) =>
      prev.kind === "ready"
        ? { ...prev, proposals: prev.proposals.filter((p) => p.id !== id) }
        : prev,
    );
  }, []);

  async function handleAccept(id: string): Promise<void> {
    setPendingError(null);
    try {
      await acceptMemoryProposal(identity, id);
      dropProposal(id);
    } catch (err) {
      setPendingError(errorMessage(err, "Could not accept proposal."));
    }
  }

  async function handleReject(id: string): Promise<void> {
    setPendingError(null);
    try {
      await rejectMemoryProposal(identity, id);
      dropProposal(id);
    } catch (err) {
      setPendingError(errorMessage(err, "Could not reject proposal."));
    }
  }

  return (
    <section
      aria-label="Memory proposals"
      data-testid="memory-proposals-route"
      data-state={state.kind}
      style={paneStyle}
    >
      <header style={headerStyle}>
        <button
          type="button"
          data-testid="memory-proposals-close"
          onClick={onClose}
          style={backButtonStyle}
        >
          ← Back to Memory
        </button>
        <h2 style={{ margin: 0, fontSize: 18 }}>Pending proposals</h2>
      </header>
      {pendingError !== null && (
        <div
          role="status"
          data-testid="memory-proposals-pending-error"
          style={{
            marginBottom: 12,
            padding: 12,
            border: "1px solid var(--color-border-strong)",
            borderRadius: 8,
            background: "var(--color-surface)",
            fontSize: 13,
          }}
        >
          {pendingError}
        </div>
      )}
      {state.kind === "loading" ? (
        <div data-testid="memory-proposals-loading">Loading…</div>
      ) : state.kind === "error" ? (
        <div role="alert" data-testid="memory-proposals-error">
          {state.message}
        </div>
      ) : state.proposals.length === 0 ? (
        <div
          data-testid="memory-proposals-empty"
          style={{ color: "var(--color-text-muted)" }}
        >
          No pending proposals.
        </div>
      ) : (
        <ul
          data-testid="memory-proposals-list"
          style={{ listStyle: "none", margin: 0, padding: 0 }}
        >
          {state.proposals.map((p) => (
            <li
              key={p.id}
              data-testid="memory-proposals-row"
              data-proposal-id={p.id}
              style={rowStyle}
            >
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 14, fontWeight: 600 }}>
                  {p.proposed_title}
                </div>
                <div style={{ fontSize: 12, color: "var(--color-text-muted)" }}>
                  {p.proposed_kind} · {p.proposed_at}
                </div>
              </div>
              <button
                type="button"
                data-testid="memory-proposals-accept"
                data-proposal-id={p.id}
                onClick={() => {
                  void handleAccept(p.id);
                }}
              >
                Accept
              </button>
              <button
                type="button"
                data-testid="memory-proposals-reject"
                data-proposal-id={p.id}
                onClick={() => {
                  void handleReject(p.id);
                }}
              >
                Reject
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

const paneStyle = {
  height: "100%",
  padding: 16,
  boxSizing: "border-box",
  overflow: "auto",
  background: "var(--color-bg)",
  color: "var(--color-text)",
} as const;

const headerStyle = {
  display: "flex",
  alignItems: "center",
  gap: 12,
  marginBottom: 12,
} as const;

const backButtonStyle = {
  background: "transparent",
  border: "none",
  color: "var(--color-accent)",
  cursor: "pointer",
  fontSize: 13,
  padding: 0,
} as const;

const rowStyle = {
  padding: "10px 0",
  borderBottom: "1px solid var(--color-border)",
  display: "flex",
  gap: 12,
  alignItems: "center",
} as const;
