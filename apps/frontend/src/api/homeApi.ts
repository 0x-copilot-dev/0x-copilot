// Typed wrappers for the Phase 9 Home destination (P9-C).
//
// Surfaces:
//   1. `fetchHome(identity)` — GET /v1/home, returns the morning-briefing
//      `HomePayload` (sub-PRD §3.3).
//   2. `openHomeStream({...})` — SSE durable channel (sub-PRD §3.6); each
//      frame carries one `HomeStreamEnvelope`. Reconnect with the highest
//      received `sequence_no` via `?after_sequence=N` to resume without
//      replay (mirrors the pattern in `inboxApi`, `agentsApi`, `routinesApi`).
//
// Network rule (apps/frontend/CLAUDE.md): the app calls the **facade**
// only (`/v1/*`). The transport singleton in `./transport.ts` enforces
// this via the same-origin Vite proxy → `backend-facade`.
//
// Why a callback-style stream handle (not a raw `EventSource`):
// `getAppTransport().subscribeServerSentEvents` is the codebase's single
// SSE substrate — it carries the bearer, the 401 interceptor, and the
// desktop-webview substrate parity. Hand-rolling a `new EventSource(...)`
// would lose those guarantees. The handle exposes `close()` so callers
// can tear down on unmount, exactly matching `streamInboxEvents`,
// `streamProjectsEvents`, etc.

import type {
  HomeActivityEvent,
  HomeActivityRow,
  HomePayload,
  InFlightProject,
  TimelineEntry,
  TriageCounts,
} from "@0x-copilot/api-types";

import type { RequestIdentity } from "./config";
import { httpGet } from "./http";
import { getAppTransport } from "./transport";

// === Constants =============================================================

/** Single source of truth for the Home base path — never inline `/v1/home`. */
const HOME_BASE = "/v1/home";
const HOME_STREAM_PATH = `${HOME_BASE}/stream`;

/** SSE event name emitted by the backend (matches `backend_app/home/sse.py`). */
const SSE_EVENT_NAME = "home_activity";

// === HTTP ==================================================================

/**
 * GET /v1/home — morning-briefing aggregator (sub-PRD §3.3). Every
 * aggregated section in the response is wrapped in `SectionResult<T>` (or
 * the sibling `WhatsNewSection`) so a partial upstream outage surfaces as
 * `status: "error"` on one section instead of a top-level 5xx.
 */
export function fetchHome(identity: RequestIdentity): Promise<HomePayload> {
  return httpGet<HomePayload>(HOME_BASE, identity);
}

// === SSE envelope =========================================================

/**
 * Typed Home SSE frames. Phase 9 broadens the live channel from the
 * Phase 2 "activity-only" feed (`HomeActivityEvent` in api-types) to a
 * full delta channel that can push triage / timeline / whats-new updates
 * as well — see sub-PRD §3.6. Each variant maps to one prop on the
 * `HomePayload`; reducers in HomeRoute apply them.
 *
 * Backward-compat note: `home.activity_appended` is the canonical name
 * the redesign uses; the backend still emits the api-types-defined
 * `HomeActivityEvent` shape on the same SSE channel, so the parser
 * accepts both encodings and normalises to this envelope.
 */
export type HomeStreamEnvelope =
  | {
      readonly type: "home.triage_updated";
      readonly sequence_no: number;
      readonly triage: TriageCounts;
    }
  | {
      readonly type: "home.timeline_appended";
      readonly sequence_no: number;
      readonly entry: TimelineEntry;
    }
  | {
      readonly type: "home.whats_new_appended";
      readonly sequence_no: number;
      readonly row: HomeActivityRow;
    }
  | {
      readonly type: "home.activity_appended";
      readonly sequence_no: number;
      readonly row: HomeActivityRow;
    }
  | {
      readonly type: "home.in_flight_updated";
      readonly sequence_no: number;
      readonly project: InFlightProject;
    }
  | {
      readonly type: "home.heartbeat";
      readonly sequence_no: number;
    };

/** Closeable handle for a running Home SSE subscription. */
export interface HomeStream {
  close(): void;
}

export interface OpenHomeStreamOptions {
  readonly identity: RequestIdentity;
  /**
   * Highest `sequence_no` already applied; the backend replays everything
   * strictly greater. Mirrors the `Last-Event-ID` HTTP-level resume that
   * the browser's native `EventSource` would do — but routed through a
   * query param so the desktop-webview transport (which can't always set
   * `Last-Event-ID` from JS) shares the same path.
   */
  readonly afterSequence?: number;
  readonly onEvent: (envelope: HomeStreamEnvelope) => void;
  readonly onError: (err: Event) => void;
  readonly onOpen?: () => void;
}

/**
 * Open the Home SSE channel (sub-PRD §3.6). Reconnect policy is owned
 * caller-side (HomeRoute) — the wrapper exposes one connection attempt
 * + a stable error hook so tests can drive the timing deterministically.
 */
export function openHomeStream({
  identity,
  afterSequence,
  onEvent,
  onError,
  onOpen,
}: OpenHomeStreamOptions): HomeStream {
  return getAppTransport().subscribeServerSentEvents({
    path: HOME_STREAM_PATH,
    query: homeSseQueryFor(identity, afterSequence),
    eventName: SSE_EVENT_NAME,
    onOpen,
    onError: (_err) => onError(streamErrorEvent()),
    onMessage: (data) => {
      let parsed: unknown;
      try {
        parsed = JSON.parse(data) as unknown;
      } catch {
        // Malformed JSON: the stream contract is JSON-per-frame; drop
        // the frame rather than crash the route. The caller has
        // `onError` for the broader "stream broken" signal — a single
        // bad frame doesn't end the connection.
        return;
      }
      const envelope = parseHomeEnvelope(parsed);
      if (envelope !== null) {
        onEvent(envelope);
      }
    },
  });
}

// === Helpers ===============================================================

function homeSseQueryFor(
  identity: RequestIdentity,
  afterSequence: number | undefined,
): Record<string, string> {
  const out: Record<string, string> = {
    org_id: identity.orgId,
    user_id: identity.userId,
  };
  if (afterSequence !== undefined) {
    out.after_sequence = String(afterSequence);
  }
  return out;
}

/**
 * Loose structural parser. Two shapes are accepted on the same SSE
 * channel:
 *   (a) the new `HomeStreamEnvelope` directly (canonical Phase 9 shape).
 *   (b) the legacy `HomeActivityEvent` shape from api-types (current
 *       backend emitter) — normalised into the
 *       `home.activity_appended` / `home.heartbeat` variants.
 *
 * Unknown shapes are dropped (returns null) so a Phase 10 envelope kind
 * never crashes a Phase 9 client.
 */
function parseHomeEnvelope(value: unknown): HomeStreamEnvelope | null {
  if (value === null || typeof value !== "object") {
    return null;
  }
  const v = value as Record<string, unknown>;

  // (a) new envelope: discriminator on `type`
  if (typeof v.type === "string" && typeof v.sequence_no === "number") {
    return parseTypedEnvelope(v as Record<string, unknown>);
  }

  // (b) legacy `HomeActivityEvent`: discriminator on `event_type`
  if (
    typeof v.event_type === "string" &&
    typeof v.sequence_no === "number" &&
    typeof v.event_id === "string"
  ) {
    return normaliseLegacyActivityEvent(v as unknown as HomeActivityEvent);
  }

  return null;
}

function parseTypedEnvelope(
  v: Record<string, unknown>,
): HomeStreamEnvelope | null {
  const type = v.type as string;
  const seq = v.sequence_no as number;
  switch (type) {
    case "home.heartbeat":
      return { type: "home.heartbeat", sequence_no: seq };
    case "home.triage_updated":
      if (v.triage !== undefined && typeof v.triage === "object") {
        return {
          type: "home.triage_updated",
          sequence_no: seq,
          triage: v.triage as TriageCounts,
        };
      }
      return null;
    case "home.timeline_appended":
      if (v.entry !== undefined && typeof v.entry === "object") {
        return {
          type: "home.timeline_appended",
          sequence_no: seq,
          entry: v.entry as TimelineEntry,
        };
      }
      return null;
    case "home.whats_new_appended":
      if (v.row !== undefined && typeof v.row === "object") {
        return {
          type: "home.whats_new_appended",
          sequence_no: seq,
          row: v.row as HomeActivityRow,
        };
      }
      return null;
    case "home.activity_appended":
      if (v.row !== undefined && typeof v.row === "object") {
        return {
          type: "home.activity_appended",
          sequence_no: seq,
          row: v.row as HomeActivityRow,
        };
      }
      return null;
    case "home.in_flight_updated":
      if (v.project !== undefined && typeof v.project === "object") {
        return {
          type: "home.in_flight_updated",
          sequence_no: seq,
          project: v.project as InFlightProject,
        };
      }
      return null;
    default:
      return null;
  }
}

function normaliseLegacyActivityEvent(
  ev: HomeActivityEvent,
): HomeStreamEnvelope | null {
  if (ev.event_type === "heartbeat") {
    return { type: "home.heartbeat", sequence_no: ev.sequence_no };
  }
  if (
    (ev.event_type === "activity_added" ||
      ev.event_type === "activity_updated") &&
    ev.row !== undefined
  ) {
    return {
      type: "home.activity_appended",
      sequence_no: ev.sequence_no,
      row: ev.row,
    };
  }
  return null;
}

// The onError signature mirrors EventSource's bare Event — callers only
// react to "stream broken" and reconnect. Matches `streamInboxEvents`.
function streamErrorEvent(): Event {
  if (typeof Event === "function") {
    return new Event("error");
  }
  return { type: "error" } as unknown as Event;
}
