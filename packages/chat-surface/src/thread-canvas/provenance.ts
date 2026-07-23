// Surface provenance selector (Generative Surfaces v2, PRD-B2 D1).
//
// `projectProvenance` is a PURE PEER of `projectLedger` / `projectSurfaceTabs`
// over the SAME `session.events` array (the one-projector invariant, FR-3.3 /
// SDR §2). It folds the ledger read-path — `surface.created`, `read.executed`,
// `action.classified`, `view.derived` — into per-surface accountability chrome:
// the producing op, latency, access class, stable ledger id, and view tier. It
// is module-state-free and total: run it twice on the same array and the results
// deep-equal; a malformed payload degrades that surface per-field, never throws.
//
// Footer fields are sourced from ledger events ONLY (DoD). The one value events
// cannot carry — the deep link (`openIn`), which needs the materialized payload's
// spec — is left `null` here and resolved by `resolveSurfaceOpenIn` where the
// hydrated content is in hand (the frame). `connector` stores the raw slug;
// humanizing happens in the component, so the selector stays pure/serializable.

import type { RuntimeEventEnvelope } from "@0x-copilot/api-types";
import { formatLedgerId } from "@0x-copilot/api-types";

import { isSafeHttpUrl, resolveDotPath } from "./dotPath";

// ---------------------------------------------------------------------------
// Value objects
// ---------------------------------------------------------------------------

export type SurfaceAccessClass = "read" | "write_held";
export type SurfaceViewTier = "pending" | "raw" | "generic" | "shaped";

/** A surface's deep link into its native app, or `null` when none is safe.
 *  `label === null` ⇒ the component builds `Open in <connector>` from the slug. */
export interface SurfaceOpenIn {
  readonly label: string | null;
  readonly url: string;
}

export interface SurfaceProvenance {
  readonly surfaceId: string;
  readonly ledgerId: string; // "r7f3·042" — A1 formatter(runId, surface.created seq)
  readonly connector: string; // surface.created.source.connector (raw slug)
  readonly op: string; // surface.created.source.op
  readonly kind: string; // surface.created.kind
  readonly latencyMs: number | null; // joined read.executed.latency_ms, else null
  readonly accessClass: SurfaceAccessClass;
  readonly tier: SurfaceViewTier; // latest view.derived.tier; none yet => "pending"
  readonly openIn: SurfaceOpenIn | null; // resolved later from hydrated content
}

// ---------------------------------------------------------------------------
// Display helpers (pure)
// ---------------------------------------------------------------------------

/** `420ms` under 1s, `1.2s` at/above (PRD-B2 D1). */
export function formatLatency(ms: number): string {
  if (!Number.isFinite(ms) || ms < 0) return "";
  if (ms < 1000) return `${Math.round(ms)}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

/** `read` → `read-only`; `write_held` → `write · held` (PRD-B2 D1). */
export function formatAccessClass(access: SurfaceAccessClass): string {
  return access === "read" ? "read-only" : "write · held";
}

// ---------------------------------------------------------------------------
// Fold
// ---------------------------------------------------------------------------

interface ReadRecord {
  readonly callId: string;
  readonly latencyMs: number | null;
}

interface SurfaceSeed {
  surfaceId: string;
  ledgerId: string;
  connector: string;
  op: string;
  kind: string;
  payloadRef: string;
  tier: SurfaceViewTier;
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object"
    ? (value as Record<string, unknown>)
    : null;
}

function strOr(value: unknown, fallback: string): string {
  return typeof value === "string" ? value : fallback;
}

function seqOf(event: RuntimeEventEnvelope): number {
  const raw = event.sequence_no;
  return typeof raw === "number" && Number.isFinite(raw) ? raw : 0;
}

/** The A1 formatter throws for a too-short run id / seq < 1; the fold must never
 *  throw, so fall back to a plainly composed id (mirrors `ledgerProjection`). */
function safeLedgerId(runId: string, seq: number): string {
  try {
    return formatLedgerId(runId, seq);
  } catch {
    return `r${runId}·${seq}`;
  }
}

/**
 * Fold a run's events into per-surface {@link SurfaceProvenance}, keyed by
 * `surface_id`. Deterministic + total; `openIn` is always `null` here (resolved
 * from hydrated content by {@link resolveSurfaceOpenIn}).
 */
export function projectProvenance(
  events: readonly RuntimeEventEnvelope[],
): ReadonlyMap<string, SurfaceProvenance> {
  const ordered = [...events].sort((a, b) => seqOf(a) - seqOf(b));

  let runId = "";
  const readsByPayloadRef = new Map<string, ReadRecord>();
  const classByCallId = new Map<string, string>();
  const seeds = new Map<string, SurfaceSeed>();

  for (const event of ordered) {
    if (runId === "" && typeof event.run_id === "string") runId = event.run_id;
    const payload = asRecord(event.payload);
    if (payload === null) continue;

    switch (event.event_type) {
      case "read.executed": {
        const payloadRef = strOr(payload.payload_ref, "");
        const callId = strOr(payload.call_id, "");
        const latencyRaw = payload.latency_ms;
        const latencyMs =
          typeof latencyRaw === "number" && Number.isFinite(latencyRaw)
            ? latencyRaw
            : null;
        if (payloadRef !== "") {
          readsByPayloadRef.set(payloadRef, { callId, latencyMs });
        }
        break;
      }
      case "action.classified": {
        const callId = strOr(payload.call_id, "");
        const cls = strOr(payload.class, "");
        if (callId !== "") classByCallId.set(callId, cls);
        break;
      }
      case "surface.created": {
        const surfaceId = strOr(payload.surface_id, "");
        if (surfaceId === "") break;
        if (seeds.has(surfaceId)) {
          // Upsert: refresh payload_ref, keep the first ledger anchor + kind.
          const existing = seeds.get(surfaceId);
          if (existing !== undefined) {
            existing.payloadRef = strOr(
              payload.payload_ref,
              existing.payloadRef,
            );
          }
          break;
        }
        const source = asRecord(payload.source);
        seeds.set(surfaceId, {
          surfaceId,
          ledgerId: safeLedgerId(event.run_id ?? runId, seqOf(event)),
          connector: source !== null ? strOr(source.connector, "") : "",
          op: source !== null ? strOr(source.op, "") : "",
          kind: strOr(payload.kind, ""),
          payloadRef: strOr(payload.payload_ref, ""),
          tier: "pending",
        });
        break;
      }
      case "view.derived": {
        const surfaceId = strOr(payload.surface_id, "");
        const seed = seeds.get(surfaceId);
        if (seed === undefined) break; // view for an unseen surface is ignored
        const tier = strOr(payload.tier, "");
        if (tier === "raw" || tier === "generic" || tier === "shaped") {
          seed.tier = tier;
        }
        break;
      }
      default:
        break; // tolerate + ignore every other event type
    }
  }

  const out = new Map<string, SurfaceProvenance>();
  for (const seed of seeds.values()) {
    const read = readsByPayloadRef.get(seed.payloadRef);
    const latencyMs = read?.latencyMs ?? null;
    // Access class fail-closed (FR-C0 / SDR §10.1): only an explicit `read`
    // classification, joined via read.executed.call_id, unlocks `read`;
    // write / unknown / a missing classification ⇒ `write_held`.
    const cls = read !== undefined ? classByCallId.get(read.callId) : undefined;
    const accessClass: SurfaceAccessClass =
      cls === "read" ? "read" : "write_held";
    out.set(seed.surfaceId, {
      surfaceId: seed.surfaceId,
      ledgerId: seed.ledgerId,
      connector: seed.connector,
      op: seed.op,
      kind: seed.kind,
      latencyMs,
      accessClass,
      tier: seed.tier,
      openIn: null,
    });
  }
  return out;
}

// ---------------------------------------------------------------------------
// Deep-link resolution (needs hydrated content, so kept out of the pure fold)
// ---------------------------------------------------------------------------

/**
 * Return a copy of `provenance` with `openIn` resolved from the surface's
 * hydrated payload, or unchanged when no safe link exists. The link comes from
 * the payload's `spec.link` (`SurfaceLink { label, url_path }` — `url_path` only,
 * no free-form URLs): resolve `url_path` against the payload with the local
 * resolver, accept ONLY `http(s)://` strings, and carry the spec `label` (the
 * component builds the `Open in <connector>` fallback when it is absent).
 * Anything else ⇒ `openIn` stays `null` (link omitted, never unsafe).
 */
export function resolveSurfaceOpenIn(
  provenance: SurfaceProvenance,
  payload: unknown,
): SurfaceProvenance {
  const root = asRecord(payload);
  if (root === null) return provenance;
  const spec = asRecord(root.spec);
  if (spec === null) return provenance;
  const link = asRecord(spec.link);
  if (link === null) return provenance;
  const urlPath = link.url_path;
  if (typeof urlPath !== "string" || urlPath === "") return provenance;
  // Resolve against the payload's `data` first (the connector response), then
  // the whole payload as a fallback.
  const resolved =
    resolveDotPath(root.data, urlPath) ?? resolveDotPath(root, urlPath);
  if (!isSafeHttpUrl(resolved)) return provenance;
  const label =
    typeof link.label === "string" && link.label !== "" ? link.label : null;
  return { ...provenance, openIn: { label, url: resolved } };
}
