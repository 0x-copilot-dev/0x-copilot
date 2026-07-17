// InboxRoute — data binder for the Inbox destination.
//
// Mirrors the P2-C HomeRoute / P3-C TodosRoute patterns:
//   1. Fetches `GET /v1/inbox` + `GET /v1/inbox/unread_count` via
//      `inboxApi` and owns loading / error state.
//   2. Opens the `/v1/inbox/stream` SSE channel (sub-PRD §3.6) and merges
//      durable item envelopes (`item_created` / `item_updated` /
//      `item_deleted`) into the local payload, tracking the highest
//      `sequence_no` for reconnect resume.
//   3. On every refresh — initial load, SSE delta, polling tick —
//      pushes the unread count into `BadgePort.setBadge("inbox", n)`
//      (sub-PRD §3.6, §14). The web host's `WebBadgePort` is a no-op;
//      desktop substrates light up the OS dock / tray icon with the
//      same call.
//   4. Fires `NotificationPort.notify(...)` on `item_created` events
//      with `priority=high` — gated on `isAvailable()` so the web
//      no-op stays silent until permission is granted (sub-PRD §14).
//   5. Polling fallback: when SSE cannot establish, polls
//      `/v1/inbox/unread_count` every 60s so the badge stays warm
//      even behind a proxy that strips SSE (sub-PRD §3.6 degraded mode).
//   6. Renders the package-shipped `<InboxDestination>` (presentational
//      shell) inside a host-side `<section>` matching the HomeRoute /
//      TodosRoute data attributes.
//
// Why a feature-level wrapper, not props on `<InboxDestination>` today:
// the current `<InboxDestination>` in chat-surface is a Wave-1 seed
// that does its own fetch + renders a placeholder. Phase 4 Impl-B
// rewrites it into a controlled component that accepts the full Inbox
// payload as props (sub-PRD §15.1 Impl-B). Until then, InboxRoute owns
// the data flow + ports wiring without forcing a breaking change on
// the package boundary. The orchestrator rewires the prop hand-off at
// merge — see `TODO(merge)` markers.

import { useEffect, useRef, useState, type ReactElement } from "react";

import { InboxDestination } from "@0x-copilot/chat-surface";

import type { RequestIdentity } from "../../api/config";
import {
  fetchInbox,
  fetchUnreadCount,
  streamInboxEvents,
} from "../../api/inboxApi";
import type {
  InboxItem,
  InboxStreamEnvelope,
  ListInboxResponse,
  InboxUnreadCount,
} from "../../api/_inbox-stub";
import { usePort } from "../../ports";
import { errorMessage } from "../../utils/errors";

/** Reconnect backoff bounds (mirrors HomeRoute / sub-PRD §3.6). */
const RECONNECT_BACKOFF_MIN_MS = 1_000;
const RECONNECT_BACKOFF_MAX_MS = 30_000;

/**
 * Polling cadence for the unread-count fallback (sub-PRD §3.6 degraded
 * mode). Only fires while the SSE channel has not established a
 * connection — once SSE opens, the polling timer is cancelled.
 */
const POLL_INTERVAL_MS = 60_000;

interface InboxRouteProps {
  readonly identity: RequestIdentity;
}

type ViewState =
  | { readonly kind: "loading" }
  | { readonly kind: "error"; readonly message: string }
  | {
      readonly kind: "ready";
      readonly items: ReadonlyArray<InboxItem>;
      readonly unreadCount: number;
      readonly highestSequenceNo: number;
    };

/**
 * Apply one durable SSE envelope to the local item list. Mirrors the
 * shape of `applyActivityEvent` in HomeRoute but supports the three
 * inbox event types (sub-PRD §3.6).
 *
 * Pure function so a test can drive it without a mounted component.
 */
export function applyInboxEnvelope(
  items: ReadonlyArray<InboxItem>,
  envelope: InboxStreamEnvelope,
): ReadonlyArray<InboxItem> {
  const idx = items.findIndex((i) => i.id === envelope.item.id);
  if (envelope.event_type === "item_deleted") {
    if (idx === -1) return items;
    return items.slice(0, idx).concat(items.slice(idx + 1));
  }
  if (envelope.event_type === "item_updated") {
    if (idx === -1) return items;
    const next = items.slice();
    next[idx] = envelope.item;
    return next;
  }
  // item_created: prepend if new; treat as updated when the producer
  // re-emits an existing id (idempotency at the wire — sub-PRD §7.4).
  if (idx !== -1) {
    const next = items.slice();
    next[idx] = envelope.item;
    return next;
  }
  return [envelope.item, ...items];
}

/**
 * Count rows whose `status === "unread"`. Computed client-side from the
 * loaded list so SSE deltas update the badge before the next
 * `unread_count` poll lands.
 */
export function computeUnreadCount(items: ReadonlyArray<InboxItem>): number {
  let count = 0;
  for (const item of items) {
    if (item.status === "unread") count += 1;
  }
  return count;
}

export function InboxRoute({ identity }: InboxRouteProps): ReactElement {
  const badgePort = usePort("badge");
  const notificationPort = usePort("notification");
  const [state, setState] = useState<ViewState>({ kind: "loading" });
  const [reloadToken, setReloadToken] = useState(0);

  // ---- Initial fetch (list + unread count) -------------------------
  useEffect(() => {
    let cancelled = false;
    setState({ kind: "loading" });

    Promise.all([
      fetchInbox(identity, { limit: 50 }),
      fetchUnreadCount(identity),
    ])
      .then(([list, unread]: [ListInboxResponse, InboxUnreadCount]) => {
        if (cancelled) return;
        setState({
          kind: "ready",
          items: list.items,
          unreadCount: unread.unread,
          highestSequenceNo: 0,
        });
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        setState({
          kind: "error",
          message: errorMessage(error, "Could not load inbox."),
        });
      });

    return () => {
      cancelled = true;
    };
  }, [identity, reloadToken]);

  // ---- BadgePort wiring --------------------------------------------
  //
  // Sub-PRD §3.6 + §14: the destination pushes the unread count on
  // every refresh — initial load, SSE delta, polling tick. The web
  // host's implementation is a no-op (cross-audit §1.2); desktop
  // substrates update the OS dock / tray icon with the same call.
  const unreadCount = state.kind === "ready" ? state.unreadCount : 0;
  useEffect(() => {
    badgePort.setBadge("inbox", unreadCount);
  }, [badgePort, unreadCount]);

  // ---- SSE subscription with exponential-backoff reconnect ---------
  //
  // The stream is bound to (identity) — stable for the lifetime of an
  // effect cycle, so the reconnect loop lives inside one useEffect.
  // The backoff doubles on every failure, clamps at 30s, and resets to
  // 1s on the next successful `onOpen`. Mirrors HomeRoute's pattern.
  //
  // SSE liveness is tracked via `sseAliveRef` so the polling-fallback
  // effect can read it without re-creating the polling loop on every
  // SSE event.
  const backoffRef = useRef(RECONNECT_BACKOFF_MIN_MS);
  const sseAliveRef = useRef(false);
  const [sseAlive, setSseAlive] = useState(false);
  useEffect(() => {
    if (state.kind !== "ready") {
      return;
    }
    let cancelled = false;
    let activeHandle: { close(): void } | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    backoffRef.current = RECONNECT_BACKOFF_MIN_MS;

    function open(): void {
      if (cancelled) return;
      // Read the highest seen seq from the latest state snapshot via
      // setState's updater so reconnect resumes from the right point
      // even after several deltas have landed since the last open.
      let afterSequence = 0;
      setState((prev) => {
        if (prev.kind === "ready") afterSequence = prev.highestSequenceNo;
        return prev;
      });

      activeHandle = streamInboxEvents({
        identity,
        afterSequence: afterSequence > 0 ? afterSequence : undefined,
        onOpen: () => {
          backoffRef.current = RECONNECT_BACKOFF_MIN_MS;
          sseAliveRef.current = true;
          setSseAlive(true);
        },
        onEvent: (envelope) => {
          if (cancelled) return;
          setState((prev) => {
            if (prev.kind !== "ready") return prev;
            const items = applyInboxEnvelope(prev.items, envelope);
            const highestSequenceNo = Math.max(
              prev.highestSequenceNo,
              envelope.sequence_no,
            );
            return {
              kind: "ready",
              items,
              unreadCount: computeUnreadCount(items),
              highestSequenceNo,
            };
          });
          // NotificationPort.notify is gated on `isAvailable()` so the
          // web no-op (permission not granted) stays silent. Only
          // `item_created` with `priority=high` fires the native
          // notification (sub-PRD §14 — body excluded for privacy).
          if (
            envelope.event_type === "item_created" &&
            envelope.item.priority === "high" &&
            notificationPort.isAvailable()
          ) {
            const senderName =
              envelope.item.sender.kind === "agent"
                ? envelope.item.sender.agent_name
                : envelope.item.sender.kind === "system"
                  ? "System"
                  : "Teammate";
            notificationPort.notify({
              title: senderName,
              body: envelope.item.subject,
              destination: "inbox",
              ref: { kind: "inbox_item", id: envelope.item.id },
              priority: "high",
            });
          }
        },
        onError: () => {
          if (cancelled) return;
          activeHandle?.close();
          activeHandle = null;
          sseAliveRef.current = false;
          setSseAlive(false);
          const delay = backoffRef.current;
          backoffRef.current = Math.min(
            backoffRef.current * 2,
            RECONNECT_BACKOFF_MAX_MS,
          );
          reconnectTimer = setTimeout(open, delay);
        },
      });
    }

    open();

    return () => {
      cancelled = true;
      sseAliveRef.current = false;
      if (reconnectTimer !== null) {
        clearTimeout(reconnectTimer);
      }
      activeHandle?.close();
    };
    // `state.kind` gates open(); we depend on it (not the full `state`
    // object) so an SSE-driven merge does NOT tear down + reopen the
    // stream. Notification + badge wiring lives in separate effects.
  }, [identity, state.kind, notificationPort]);

  // ---- Polling fallback (sub-PRD §3.6 degraded mode) ---------------
  //
  // When SSE has not established a connection, poll `/v1/inbox/unread_count`
  // every 60s so the badge stays warm. The poll is cancelled the moment
  // SSE opens (sseAlive flips true) — single source of truth for the
  // unread count is the SSE-merged item list once the channel is live.
  useEffect(() => {
    if (state.kind !== "ready") return;
    if (sseAlive) return;
    let cancelled = false;
    const timer = setInterval(() => {
      if (cancelled) return;
      fetchUnreadCount(identity)
        .then((count) => {
          if (cancelled) return;
          setState((prev) =>
            prev.kind === "ready"
              ? { ...prev, unreadCount: count.unread }
              : prev,
          );
        })
        .catch(() => {
          // Polling errors stay silent — the next tick retries, and the
          // SSE reconnect loop will eventually win. A surfaced toast on
          // every transient blip would be noise.
        });
    }, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [identity, state.kind, sseAlive]);

  // ---- Render -------------------------------------------------------
  if (state.kind === "error") {
    return (
      <section
        aria-label="Inbox destination"
        data-testid="inbox-route"
        data-state="error"
        style={{
          height: "100%",
          width: "100%",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          padding: 24,
          boxSizing: "border-box",
          backgroundColor: "var(--color-bg)",
          color: "var(--color-text)",
        }}
      >
        <div
          role="alert"
          data-testid="inbox-route-error"
          style={{
            border: "1px solid var(--color-border)",
            borderRadius: 12,
            backgroundColor: "var(--color-surface)",
            padding: 32,
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            gap: 12,
            maxWidth: 480,
          }}
        >
          <div style={{ fontSize: 14, fontWeight: 600 }}>
            Could not load inbox
          </div>
          <div
            style={{ fontSize: 13, color: "var(--color-text-muted)" }}
            data-testid="inbox-route-error-message"
          >
            {state.message}
          </div>
          <button
            type="button"
            data-testid="inbox-route-retry"
            onClick={() => setReloadToken((t) => t + 1)}
            style={{
              height: 32,
              padding: "0 14px",
              borderRadius: 8,
              border: "1px solid var(--color-border-strong)",
              backgroundColor: "transparent",
              color: "var(--color-accent)",
              fontSize: 13,
              fontWeight: 600,
              cursor: "pointer",
            }}
          >
            Retry
          </button>
        </div>
      </section>
    );
  }

  // Loading + ready both render <InboxDestination />. Today (Wave 1
  // seed) `<InboxDestination>` does its own fetch + shows its own
  // skeleton, so passing the payload through is a no-op — but the
  // wrapper is here so Phase 4 Impl-B's controlled rewrite of
  // InboxDestination can accept `items` + `unreadCount` + `onRetry`
  // without any App.tsx-level rewiring.
  //
  // TODO(merge): once Impl-B's InboxDestination accepts props, pass:
  //   <InboxDestination
  //     items={state.kind === "ready" ? state.items : null}
  //     unreadCount={unreadCount}
  //     identity={identity}
  //     onRetry={() => setReloadToken((t) => t + 1)}
  //   />
  return (
    <section
      aria-label="Inbox destination"
      data-testid="inbox-route"
      data-state={state.kind}
      data-unread-count={unreadCount}
      data-sse-alive={sseAlive ? "true" : "false"}
      style={{ height: "100%", width: "100%", overflow: "auto" }}
    >
      <InboxDestination />
    </section>
  );
}
