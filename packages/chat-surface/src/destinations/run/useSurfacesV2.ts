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
// B3: the endpoint enriches a snapshot only through its declared
// `surface.created.payload_ref` → persisted tool-result binding. A surface with
// no resolved reference carries `state: null`; `stateFor` returns `undefined`,
// so the renderer shows its honest skeleton / tier-3 floor rather than a
// fabricated body.

import { useEffect, useRef, useState } from "react";

import type {
  RunSurfacesResponse,
  SurfaceId,
  SurfaceSnapshot,
} from "@0x-copilot/api-types";
import { isSurfaceId } from "@0x-copilot/api-types";
import type { Transport } from "@0x-copilot/chat-transport";

import type { SurfacePayload } from "../../thread-canvas/eventProjector";

export interface UseSurfacesV2Result {
  /** Keyed by `surfaceId`; `undefined` = not yet hydrated (mount shows its
   *  existing skeleton / tier-3 state).
   *
   *  Takes a plain tab URI because the strip is heterogeneous — it also carries
   *  artifact / effect-stage / receipt URIs, which are simply misses here. The
   *  MAP, though, is keyed by the branded `SurfaceId`, so nothing can be stored
   *  under an identity the producer did not mint. */
  readonly stateFor: (uri: string) => SurfacePayload | undefined;
  readonly status: "idle" | "loading" | "ready" | "error";
}

const EMPTY = new Map<SurfaceId, SurfacePayload>();

/**
 * Adapt one A3 `SurfaceSnapshot` into the `SurfacePayload` envelope shape the
 * renderers read (`{spec?, data}`). The endpoint's only hydration field is
 * declared `state`; other values are metadata and MUST NOT become synthetic
 * renderer payloads.
 */
function snapshotToPayload(
  snapshot: SurfaceSnapshot,
): SurfacePayload | undefined {
  const raw = snapshot as unknown as Record<string, unknown>;
  const state = raw.state;
  if (state !== null && typeof state === "object") {
    return state as SurfacePayload;
  }
  return undefined;
}

export function useSurfacesV2(
  transport: Transport,
  runId: string | null,
  lastLedgerSeq: number,
  enabled: boolean,
): UseSurfacesV2Result {
  const [byId, setById] =
    useState<ReadonlyMap<SurfaceId, SurfacePayload>>(EMPTY);
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
          const next = new Map<SurfaceId, SurfacePayload>();
          for (const snapshot of res.surfaces ?? []) {
            const payload = snapshotToPayload(snapshot);
            // The wire boundary: the server's id becomes THE identity, or the
            // snapshot is dropped. Storing it under anything else is what
            // guaranteed the lookup below could never find it.
            if (payload !== undefined && isSurfaceId(snapshot.surface_id))
              next.set(snapshot.surface_id, payload);
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

  const stateFor = (uri: string): SurfacePayload | undefined =>
    isSurfaceId(uri) ? byId.get(uri) : undefined;

  return { stateFor, status };
}
