// Pure wire → presentation adapters for the Connectors destination.
//
// Adapters are deliberately pure functions (no React, no fetch, no time
// helpers reaching for `Date.now()` — callers pass `now` so tests can
// drive deterministically). This file is the seam where the canonical
// `@0x-copilot/api-types/connectors` shapes turn into the small
// strings + tone-tokens the host-side route renders without dragging
// design-system imports through every callsite.
//
// Nothing here imports React. Tests assert pure behavior — see
// `__tests__/ConnectorsRoute.test.tsx` for component-level coverage.

import type {
  Connector,
  ConnectorAuditEntry,
  ConnectorListResponse,
  ConnectorStatus,
  ConnectorStreamEnvelope,
  Webhook,
} from "@0x-copilot/api-types";

// ---------------------------------------------------------------------------
// Status pill tone
// ---------------------------------------------------------------------------

export type StatusTone = "success" | "warning" | "danger" | "neutral";

/**
 * Map a `ConnectorStatus` to a tone token. The four tone values mirror
 * the design-system `<StatusPill>` API used by Routines / Inbox / Tools.
 *
 * * `connected`    → success
 * * `expired`      → warning (refresh-token expired; needs full re-auth)
 * * `error`        → danger (provider 4xx; needs reconnect)
 * * `disconnected` → neutral (the user disconnected; nothing to do)
 */
export function statusTone(status: ConnectorStatus): StatusTone {
  switch (status) {
    case "connected":
      return "success";
    case "expired":
      return "warning";
    case "error":
      return "danger";
    case "disconnected":
      return "neutral";
  }
}

/** Human-readable status label (frontend-only string; not on the wire). */
export function statusLabel(status: ConnectorStatus): string {
  switch (status) {
    case "connected":
      return "Connected";
    case "expired":
      return "Needs re-auth";
    case "error":
      return "Error";
    case "disconnected":
      return "Disconnected";
  }
}

// ---------------------------------------------------------------------------
// Last-sync formatting
// ---------------------------------------------------------------------------

/**
 * Relative-time formatter for `last_sync_at`. Pure: takes both `ts`
 * (ISO8601 string or null) and `now` (epoch ms) so the test can pin
 * "now" without monkey-patching `Date.now()`. Mirrors the
 * `formatRelativeTime` pattern from SP-1 — kept local because the
 * Connectors destination is not yet inside design-system.
 *
 * * null              → "Never synced"
 * * < 60s             → "just now"
 * * < 1h              → "Nm ago"
 * * < 24h             → "Nh ago"
 * * < 30d             → "Nd ago"
 * * older             → ISO date prefix
 * * unparsable        → "—"
 */
export function formatLastSync(
  ts: string | null | undefined,
  now: number,
): string {
  if (ts === null || ts === undefined || ts === "") {
    return "Never synced";
  }
  const parsed = Date.parse(ts);
  if (Number.isNaN(parsed)) {
    return "—";
  }
  const diff = now - parsed;
  if (diff < 60_000) return "just now";
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}m ago`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}h ago`;
  const days = Math.floor(diff / 86_400_000);
  if (days < 30) return `${days}d ago`;
  return ts.slice(0, 10);
}

// ---------------------------------------------------------------------------
// SSE envelope reducer — list-merge logic (pure)
// ---------------------------------------------------------------------------

/**
 * Apply one durable SSE envelope to the local connectors list. Pure
 * function so a test can drive it without a mounted component.
 *
 * Semantics (connectors-prd §4.9 event types):
 * * `connector.created`         → splice into the list if not present.
 * * `connector.status_changed`  → in-place status patch when present.
 * * `connector.scope_changed`   → caller refetches the affected row (the
 *                                  envelope's `connector` payload is the
 *                                  fresh row when present; we patch it).
 * * `connector.error_threshold` → no list mutation — affects UI badge
 *                                  rendering only.
 * * `heartbeat`                 → no-op.
 *
 * When the envelope carries a `connector` payload, we trust it as the
 * fresh row and replace. When it does not (status / threshold without a
 * payload), the consumer route refetches.
 */
export function applyConnectorEnvelope(
  items: ReadonlyArray<Connector>,
  envelope: ConnectorStreamEnvelope,
): ReadonlyArray<Connector> {
  if (envelope.event_type === "heartbeat") {
    return items;
  }
  if (envelope.event_type === "connector.error_threshold") {
    return items;
  }
  const incoming = envelope.connector;
  if (incoming === undefined) {
    return items;
  }
  const idx = items.findIndex((c) => c.id === incoming.id);
  if (idx === -1) {
    if (envelope.event_type === "connector.created") {
      return [incoming, ...items];
    }
    return items;
  }
  const next = items.slice();
  next[idx] = incoming;
  return next;
}

// ---------------------------------------------------------------------------
// ACL-filtered presentation
// ---------------------------------------------------------------------------

/**
 * Partition the list response into the lanes the destination renders.
 * "Connected" includes `connected` only — the `expired` / `error` /
 * `disconnected` lanes get their own affordance ("Reconnect").
 */
export interface PresentableConnectors {
  readonly connected: ReadonlyArray<Connector>;
  readonly attention: ReadonlyArray<Connector>;
  readonly disconnected: ReadonlyArray<Connector>;
  readonly available: ReadonlyArray<{
    readonly slug: string;
    readonly display_name: string;
    readonly description: string;
    readonly icon_hint?: string;
  }>;
}

export function partitionConnectors(
  res: ConnectorListResponse,
): PresentableConnectors {
  const connected: Connector[] = [];
  const attention: Connector[] = [];
  const disconnected: Connector[] = [];
  for (const c of res.connectors) {
    if (c.status === "connected") {
      connected.push(c);
    } else if (c.status === "error" || c.status === "expired") {
      attention.push(c);
    } else {
      disconnected.push(c);
    }
  }
  return {
    connected,
    attention,
    disconnected,
    available: res.available.map((a) => ({
      slug: a.slug,
      display_name: a.display_name,
      description: a.description,
      icon_hint: a.icon_hint,
    })),
  };
}

// ---------------------------------------------------------------------------
// Audit entry → row presentation
// ---------------------------------------------------------------------------

export interface AuditRowPresentation {
  readonly id: string;
  readonly ts: string;
  readonly endpoint: string;
  readonly status: ConnectorAuditEntry["status"];
  readonly tone: StatusTone;
  readonly bytesReadLabel: string;
  readonly callerLabel: string;
}

export function presentAuditEntry(
  entry: ConnectorAuditEntry,
): AuditRowPresentation {
  return {
    id: entry.id,
    ts: entry.ts,
    endpoint: entry.endpoint,
    status: entry.status,
    tone:
      entry.status === "ok"
        ? "success"
        : entry.status === "auth_required"
          ? "warning"
          : "danger",
    bytesReadLabel:
      entry.bytes_read === null ? "—" : formatBytes(entry.bytes_read),
    callerLabel:
      entry.caller.kind === "agent"
        ? `Agent ${entry.caller.id}`
        : entry.caller.kind === "tool"
          ? `Tool ${entry.caller.id}`
          : entry.caller.kind === "routine"
            ? `Routine ${entry.caller.id}`
            : entry.caller.kind === "project"
              ? `Project ${entry.caller.id}`
              : entry.caller.kind === "connector"
                ? `Connector ${entry.caller.id}`
                : `${entry.caller.kind} ${entry.caller.id}`,
  };
}

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

// ---------------------------------------------------------------------------
// Webhook URL masking (matches the chat-surface RoutineDetail pattern)
// ---------------------------------------------------------------------------

/**
 * Produce a redacted URL for display in webhook lists. Keeps the host +
 * scheme readable but elides the path / token. Pure helper — does NOT
 * touch the response's plaintext secret (which never lands on a list
 * fetch in the first place).
 */
export function maskWebhookUrl(webhook: Webhook): string {
  try {
    const url = new URL(webhook.url);
    return `${url.protocol}//${url.host}/…`;
  } catch {
    return "…";
  }
}
