// One projector. Many consumers.
//
// Source: chats-canvas-prd.md §3.2 (binding 2026-05-17).
//
// `useEventProjector` is the React-facing entry point to `eventProjector.ts`.
// ThreadCanvas mounts ONCE per conversation; this hook runs the reducer
// ONCE per `events` reference change, then exposes the four
// consumer-shaped slices the canvas needs (surface, swimlanes, chat,
// timeline). Every consumer reads its slice and does not re-project.
//
// Why a hook (not just a `useMemo` inline in ThreadCanvas):
//   1. Single call-site for the projection — DRY across modes.
//   2. The four consumer-shape transforms (e.g. timeline beads, surface
//      payloads) live next to the projector contract, not scattered in
//      the canvas body.
//   3. Tests can assert the projector ran ONCE for N consumers by
//      counting hook re-runs against a `useRef` (see `ThreadCanvas.test`).
//
// The hook is keyed by referential equality on `events`. Callers
// (ChatScreen) feed an append-only array that grows by reference change
// per SSE chunk — exactly what `project()` is designed for. Consumers
// downstream of this hook get cheap `useMemo` invalidation by event-list
// identity.

import { useMemo } from "react";

import type { RuntimeEventEnvelope } from "@enterprise-search/api-types";

import {
  project,
  selectors,
  type ActivityEntry,
  type ChatEntry,
  type ProjectedState,
  type SurfacePayload,
  type TimelineBead,
} from "./eventProjector";
import type { Approval } from "./_approvals-stub";

/**
 * Consumer-shape for `TcSurfaceMount`. Carries the per-surface state map
 * + a `hasActiveSurfaces` flag used by Auto mode to pick its layout.
 */
export interface SurfaceConsumer {
  /** All surface uris with at least one recorded payload. */
  readonly uris: readonly string[];
  /** Read a specific surface payload (or `undefined` if none yet). */
  readonly payloadFor: (uri: string) => SurfacePayload | undefined;
  /** Convenience: true iff at least one surface has a payload recorded. */
  readonly hasActiveSurfaces: boolean;
}

/** Consumer-shape for `TcSwimlanes`. Reads all beads in ascending order. */
export interface SwimlanesConsumer {
  readonly beads: readonly TimelineBead[];
}

/** Consumer-shape for `TcChat`. Chat-entry list + pending-approval count. */
export interface ChatConsumer {
  readonly entries: readonly ChatEntry[];
  readonly pendingApprovals: readonly Approval[];
}

/** Consumer-shape for `TcMiniTimeline`. */
export interface TimelineConsumer {
  readonly beads: readonly TimelineBead[];
  readonly lastSequenceNo: number;
}

/** Consumer-shape for the Activity tab (RightRailTabs in P1-B3). */
export interface ActivityConsumer {
  readonly entries: readonly ActivityEntry[];
}

export interface EventProjection {
  readonly state: ProjectedState;
  readonly surface: SurfaceConsumer;
  readonly swimlanes: SwimlanesConsumer;
  readonly chat: ChatConsumer;
  readonly timeline: TimelineConsumer;
  readonly activity: ActivityConsumer;
}

const EMPTY_EVENTS: readonly RuntimeEventEnvelope[] = [];

/**
 * Project an ordered runtime event list into the consumer shapes the
 * thread canvas needs. The single `project()` call is memoized by
 * `events` identity; consumers are recomputed in `useMemo` so a hook
 * re-render does not allocate new selector arrays unless the projection
 * itself changed.
 */
export function useEventProjector(
  events?: readonly RuntimeEventEnvelope[],
): EventProjection {
  const source = events ?? EMPTY_EVENTS;
  const state = useMemo<ProjectedState>(() => project(source), [source]);

  const surface = useMemo<SurfaceConsumer>(() => {
    const uris = Array.from(state.surfaceState.keys());
    const payloadFor = (uri: string): SurfacePayload | undefined =>
      selectors.surfaceFor(state, uri);
    return {
      uris,
      payloadFor,
      hasActiveSurfaces: uris.length > 0,
    };
  }, [state]);

  const swimlanes = useMemo<SwimlanesConsumer>(
    () => ({ beads: state.beads }),
    [state],
  );

  const chat = useMemo<ChatConsumer>(
    () => ({
      entries: state.chat,
      pendingApprovals: selectors.pendingApprovals(state),
    }),
    [state],
  );

  const timeline = useMemo<TimelineConsumer>(
    () => ({
      beads: state.beads,
      lastSequenceNo: state.lastSequenceNo,
    }),
    [state],
  );

  const activity = useMemo<ActivityConsumer>(
    () => ({ entries: state.activity }),
    [state],
  );

  return { state, surface, swimlanes, chat, timeline, activity };
}
