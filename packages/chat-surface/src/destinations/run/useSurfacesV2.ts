// useSurfacesV2 — Generative Surfaces v2 content hydration (PRD-B1 §3).
//
// The client ledger fold (`projectLedger`) gives named tabs from events alone,
// but ledger events carry `payload_ref`, not content (SDR §5). Materialized
// surface content comes from PRD-A3's SurfaceStore endpoint,
// `GET /v1/agent/runs/{run_id}/surfaces`, fetched through the Transport port
// (substrate rule: no bare `fetch` in the package).
//
// This is the direct precedent of `useRunSources`: a Transport-fed GET
// hydration hook that projects its result. It re-fetches when `lastLedgerSeq`
// advances (a new surface event landed), coalescing concurrent advances into
// exactly one follow-up, and fails soft — an HTTP error never throws into React.
//
// NOTE (integration, 2026-07-23 → B2): B1 shipped this hook forward-compatible
// against a metadata-only `/surfaces` response; PRD-B2 delivered the content —
// `list_run_surfaces` now enriches each `SurfaceSnapshot` with its materialized
// `state` (`{spec?, data}`), resolved server-side from the run's v1 surface
// envelopes (`SurfaceContentProjection`). `snapshotToPayload` reads that `state`
// (its first structural branch), so hydration flows through exactly as designed
// — zero client change was needed. A surface with no content event yet carries
// `state: null`, and `stateFor` returns `undefined`, so the surface degrades to
// its honest skeleton / tier-3 floor rather than a fabricated body.

import { useEffect, useRef, useState } from "react";

import type {
  RunSurfacesResponse,
  SurfaceSnapshot,
} from "@0x-copilot/api-types";
import type { Transport } from "@0x-copilot/chat-transport";

import type { SurfacePayload } from "../../thread-canvas/eventProjector";

export interface UseSurfacesV2Result {
  /** Keyed by `surfaceId`; `undefined` = not yet hydrated (mount shows its
   *  existing skeleton / tier-3 state). */
  readonly stateFor: (surfaceId: string) => SurfacePayload | undefined;
  readonly status: "idle" | "loading" | "ready" | "error";
}

const EMPTY = new Map<string, SurfacePayload>();

/**
 * Adapt one A3 `SurfaceSnapshot` into the `SurfacePayload` envelope shape the
 * renderers read (`{spec?, data}`). A3 snapshots are metadata-only today, so
 * this reads a materialized `state`/`data`/`payload` structurally (forward
 * compatibility) and returns `undefined` when none is present — an honest
 * "not yet hydrated" signal, never a fabricated body.
 */
function snapshotToPayload(
  snapshot: SurfaceSnapshot,
): SurfacePayload | undefined {
  const raw = snapshot as unknown as Record<string, unknown>;
  const state = raw.state;
  if (state !== null && typeof state === "object") {
    return state as SurfacePayload;
  }
  const data = raw.data;
  if (data !== undefined) {
    return { data } as SurfacePayload;
  }
  const payload = raw.payload;
  if (payload !== null && typeof payload === "object") {
    return payload as SurfacePayload;
  }
  return undefined;
}

export function useSurfacesV2(
  transport: Transport,
  runId: string | null,
  lastLedgerSeq: number,
  enabled: boolean,
): UseSurfacesV2Result {
  const [byId, setById] = useState<ReadonlyMap<string, SurfacePayload>>(EMPTY);
  const [status, setStatus] = useState<UseSurfacesV2Result["status"]>("idle");

  // Hook-lifetime refs (survive effect re-runs, unlike a per-effect closure):
  //   mounted        — false after unmount; gates every setState.
  //   inFlight       — a request is currently outstanding.
  //   requestedSeq   — the highest seq that has asked to be hydrated.
  //   fetchedSeq     — the seq the last COMPLETED fetch resolved for.
  const mountedRef = useRef(true);
  const inFlightRef = useRef(false);
  const requestedSeqRef = useRef(0);
  const fetchedSeqRef = useRef(0);
  const runIdRef = useRef<string | null>(runId);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  useEffect(() => {
    // A run switch invalidates prior hydration entirely.
    if (runIdRef.current !== runId) {
      runIdRef.current = runId;
      fetchedSeqRef.current = 0;
      requestedSeqRef.current = 0;
      setById(EMPTY);
      setStatus("idle");
    }

    if (!enabled || runId === null || lastLedgerSeq <= 0) {
      return;
    }
    requestedSeqRef.current = Math.max(requestedSeqRef.current, lastLedgerSeq);

    const runFetch = (): void => {
      // Nothing newer than what we've already fetched → stop.
      if (requestedSeqRef.current <= fetchedSeqRef.current) return;
      const targetSeq = requestedSeqRef.current;
      inFlightRef.current = true;
      if (mountedRef.current) setStatus("loading");
      void transport
        .request<RunSurfacesResponse>({
          method: "GET",
          path: `/v1/agent/runs/${runId}/surfaces`,
        })
        .then((res) => {
          fetchedSeqRef.current = targetSeq;
          if (!mountedRef.current || runIdRef.current !== runId) return;
          const next = new Map<string, SurfacePayload>();
          for (const snapshot of res.surfaces ?? []) {
            const payload = snapshotToPayload(snapshot);
            if (payload !== undefined) next.set(snapshot.surface_id, payload);
          }
          setById(next);
          setStatus("ready");
        })
        .catch(() => {
          // Fail soft — tabs still render from the event fold; the surface
          // column shows its tier-3 state. Mark the seq ATTEMPTED so `finally`
          // does not re-fire it (no retry storm); a later seq advance
          // (`requestedSeq > fetchedSeq`) retries. PRD-B1 §3.
          fetchedSeqRef.current = targetSeq;
          if (mountedRef.current && runIdRef.current === runId) {
            setStatus("error");
          }
        })
        .finally(() => {
          inFlightRef.current = false;
          // A newer seq arrived while this was in flight → exactly one
          // coalesced follow-up (guarded by the requested>fetched check).
          if (mountedRef.current && runIdRef.current === runId) {
            runFetch();
          }
        });
    };

    // One request at a time; a mid-flight advance is picked up by the
    // in-flight request's `finally` (coalescing).
    if (!inFlightRef.current) {
      runFetch();
    }
  }, [transport, runId, lastLedgerSeq, enabled]);

  const stateFor = (surfaceId: string): SurfacePayload | undefined =>
    byId.get(surfaceId);

  return { stateFor, status };
}
