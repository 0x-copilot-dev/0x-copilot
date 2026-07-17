// MemoryDetailRoute — `/memory/<id>` data binder. Fetches the row +
// renders body / tags / metadata. Owner edits (PATCH / DELETE) ride
// through this surface. The presentation is intentionally minimal —
// when the P12-B2 `<MemoryEditor>` chat-surface component lands, it
// slots in here as the renderer; this route's contract is just
// "load + own the mutation calls".

import { useEffect, useState, type ReactElement } from "react";

import type { MemoryItem, MemoryItemId } from "@0x-copilot/api-types";

import type { RequestIdentity } from "../../api/config";
import {
  deleteMemory as apiDeleteMemory,
  fetchMemoryItem,
} from "../../api/memoryApi";
import { errorMessage } from "../../utils/errors";

type ViewState =
  | { readonly kind: "loading" }
  | { readonly kind: "error"; readonly message: string }
  | { readonly kind: "ready"; readonly item: MemoryItem };

interface MemoryDetailRouteProps {
  readonly identity: RequestIdentity;
  readonly memoryItemId: MemoryItemId;
  readonly onClose: () => void;
  readonly onDeleted: (id: MemoryItemId) => void;
}

export function MemoryDetailRoute({
  identity,
  memoryItemId,
  onClose,
  onDeleted,
}: MemoryDetailRouteProps): ReactElement {
  const [state, setState] = useState<ViewState>({ kind: "loading" });
  const [pendingError, setPendingError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setState({ kind: "loading" });

    void (async () => {
      try {
        const item = await fetchMemoryItem(identity, memoryItemId);
        if (!cancelled) setState({ kind: "ready", item });
      } catch (err) {
        if (!cancelled) {
          setState({
            kind: "error",
            message: errorMessage(err, "Could not load memory."),
          });
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [identity, memoryItemId]);

  async function handleDelete(): Promise<void> {
    setPendingError(null);
    try {
      await apiDeleteMemory(identity, memoryItemId);
      onDeleted(memoryItemId);
    } catch (err) {
      setPendingError(errorMessage(err, "Could not delete memory."));
    }
  }

  return (
    <section
      aria-label="Memory detail"
      data-testid="memory-detail-route"
      data-memory-id={memoryItemId}
      data-state={state.kind}
      style={paneStyle}
    >
      <header style={headerStyle}>
        <button
          type="button"
          data-testid="memory-detail-close"
          onClick={onClose}
          style={backButtonStyle}
        >
          ← Back to Memory
        </button>
      </header>
      {state.kind === "loading" ? (
        <div data-testid="memory-detail-loading">Loading…</div>
      ) : state.kind === "error" ? (
        <div role="alert" data-testid="memory-detail-error">
          {state.message}
        </div>
      ) : (
        <div data-testid="memory-detail-body">
          <h2 style={{ margin: "0 0 4px 0" }}>{state.item.title}</h2>
          <div style={{ color: "var(--color-text-muted)", fontSize: 13 }}>
            {state.item.kind} · {state.item.scope}
            {state.item.tags.length > 0
              ? ` · #${state.item.tags.join(" #")}`
              : ""}
          </div>
          <pre
            data-testid="memory-detail-body-text"
            style={{
              whiteSpace: "pre-wrap",
              fontFamily: "inherit",
              marginTop: 12,
              fontSize: 13,
            }}
          >
            {state.item.body}
          </pre>
          {pendingError !== null && (
            <div
              role="status"
              data-testid="memory-detail-pending-error"
              style={{
                marginTop: 12,
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
          <div style={{ marginTop: 16 }}>
            <button
              type="button"
              data-testid="memory-detail-delete"
              onClick={() => {
                void handleDelete();
              }}
            >
              Delete
            </button>
          </div>
        </div>
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
