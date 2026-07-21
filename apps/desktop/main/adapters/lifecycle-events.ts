import { dirname } from "node:path";

import type {
  AdapterGeneratedPayload,
  AdapterLayoutTemplate,
} from "@0x-copilot/api-types";

// Type-only import (erased at runtime) — avoids a require cycle with
// `lifecycle.ts`, which imports `appendLifecycleEvent` from this module.
import type { LifecycleBoundaryEvent, LifecycleEventSource } from "./lifecycle";

// PRD §9.5.2 / §9.5.4. Phase 6C audit log: append-only JSON Lines at
// {userData}/audit/adapter-lifecycle.log. Shares the file with 6D's
// broken-mark primitive so SIEM exports see one timeline per adapter.
// Implementation uses fs.appendFile exclusively — never writeFile,
// truncate, or unlink — so historical records cannot be rewritten.

export type LifecycleEventKind =
  | "requested"
  | "generated"
  | "validated"
  | "installed"
  | "render-error"
  | "marked-broken"
  | "regen-queued"
  | "lifecycle-exhausted";

export interface LifecycleAuditEntry {
  readonly ts: number;
  readonly kind: LifecycleEventKind;
  readonly scheme: string;
  readonly version: number;
  readonly detail?: string;
}

export interface LifecycleEventsFs {
  appendFile(path: string, data: string): Promise<void>;
  mkdir(path: string, opts: { recursive: true }): Promise<string | undefined>;
  readFile(path: string, encoding: "utf8"): Promise<string>;
}

export interface LifecycleEventsDeps {
  readonly logPath: string;
  readonly fs: LifecycleEventsFs;
}

export interface ReadLifecycleEventsOpts {
  readonly scheme?: string;
  readonly limit?: number;
  readonly kind?: LifecycleEventKind;
}

function isMissingFileError(err: unknown): boolean {
  if (err === null || typeof err !== "object") return false;
  const code = (err as { code?: unknown }).code;
  return code === "ENOENT";
}

function isLifecycleAuditEntry(value: unknown): value is LifecycleAuditEntry {
  if (value === null || typeof value !== "object") return false;
  const o = value as Record<string, unknown>;
  if (typeof o.ts !== "number") return false;
  if (typeof o.kind !== "string") return false;
  if (typeof o.scheme !== "string") return false;
  if (typeof o.version !== "number") return false;
  if (o.detail !== undefined && typeof o.detail !== "string") return false;
  return true;
}

export async function appendLifecycleEvent(
  entry: LifecycleAuditEntry,
  deps: LifecycleEventsDeps,
): Promise<void> {
  const line = `${JSON.stringify(entry)}\n`;
  await deps.fs.mkdir(dirname(deps.logPath), { recursive: true });
  await deps.fs.appendFile(deps.logPath, line);
}

export async function readLifecycleEvents(
  opts: ReadLifecycleEventsOpts,
  deps: LifecycleEventsDeps,
): Promise<readonly LifecycleAuditEntry[]> {
  let raw: string;
  try {
    raw = await deps.fs.readFile(deps.logPath, "utf8");
  } catch (err) {
    if (isMissingFileError(err)) return [];
    throw err;
  }

  const out: LifecycleAuditEntry[] = [];
  for (const lineRaw of raw.split("\n")) {
    if (lineRaw.length === 0) continue;
    let parsed: unknown;
    try {
      parsed = JSON.parse(lineRaw);
    } catch {
      continue;
    }
    if (!isLifecycleAuditEntry(parsed)) continue;
    if (opts.scheme !== undefined && parsed.scheme !== opts.scheme) continue;
    if (opts.kind !== undefined && parsed.kind !== opts.kind) continue;
    out.push(parsed);
  }

  if (opts.limit !== undefined && out.length > opts.limit) {
    return out.slice(out.length - opts.limit);
  }
  return out;
}

// ===========================================================================
// PRD-10 (Wave 4) — the real tier-2 lifecycle event source.
//
// Phase 6C shipped only a no-op `StubLifecycleEventSource`. This is its
// replacement: a source that the desktop main process feeds from two places it
// already owns — the run-feed SSE tap (via `TransportBridge`'s
// `onRunFeedMessage`) for `adapter_generated` events, and the renderer's
// `tier2.boundary-error` IPC for live render failures. `startTier2Lifecycle`
// subscribes to it exactly as before; the source is pure fan-out with no I/O of
// its own, so it stays trivially testable.
// ===========================================================================

const _ADAPTER_LAYOUTS: ReadonlySet<AdapterLayoutTemplate> =
  new Set<AdapterLayoutTemplate>([
    "form",
    "table",
    "kanban",
    "definition-list",
  ]);

/** Defensive shape guard for an untrusted `adapter_generated` payload. */
export function coerceAdapterGeneratedPayload(
  value: unknown,
): AdapterGeneratedPayload | null {
  if (value === null || typeof value !== "object") return null;
  const o = value as Record<string, unknown>;
  if (typeof o.scheme !== "string" || o.scheme.length === 0) return null;
  if (
    typeof o.layout !== "string" ||
    !_ADAPTER_LAYOUTS.has(o.layout as AdapterLayoutTemplate)
  ) {
    return null;
  }
  if (
    typeof o.schema_version !== "number" ||
    !Number.isFinite(o.schema_version)
  )
    return null;
  if (typeof o.adapter_source !== "string" || o.adapter_source.length === 0)
    return null;
  if (typeof o.generated_at !== "string") return null;
  if (typeof o.generator_model !== "string") return null;
  return {
    scheme: o.scheme,
    layout: o.layout as AdapterLayoutTemplate,
    schema_version: o.schema_version,
    adapter_source: o.adapter_source,
    generated_at: o.generated_at,
    generator_model: o.generator_model,
  };
}

interface RunFeedEnvelope {
  readonly event_type?: unknown;
  readonly payload?: unknown;
}

export class RunFeedLifecycleEventSource implements LifecycleEventSource {
  readonly #generatedHandlers = new Set<
    (payload: AdapterGeneratedPayload) => void
  >();
  readonly #boundaryHandlers = new Set<
    (info: LifecycleBoundaryEvent) => void
  >();

  onAdapterGenerated(
    handler: (payload: AdapterGeneratedPayload) => void,
  ): () => void {
    this.#generatedHandlers.add(handler);
    return () => {
      this.#generatedHandlers.delete(handler);
    };
  }

  onBoundaryError(handler: (info: LifecycleBoundaryEvent) => void): () => void {
    this.#boundaryHandlers.add(handler);
    return () => {
      this.#boundaryHandlers.delete(handler);
    };
  }

  /**
   * Fed by the run-feed tap for every SSE message. Parses the envelope and, on
   * an `adapter_generated` event with a well-formed payload, fans it out. All
   * other events (and malformed input) are ignored — the tap sees the whole run
   * feed, and only tier-2 generation events matter here.
   */
  feedStreamMessage(raw: string): void {
    let env: RunFeedEnvelope;
    try {
      env = JSON.parse(raw) as RunFeedEnvelope;
    } catch {
      return;
    }
    if (env === null || typeof env !== "object") return;
    if (env.event_type !== "adapter_generated") return;
    const payload = coerceAdapterGeneratedPayload(env.payload);
    if (payload === null) return;
    for (const handler of this.#generatedHandlers) handler(payload);
  }

  /** Fed by the renderer's `tier2.boundary-error` IPC (a live render failed). */
  feedBoundaryError(info: LifecycleBoundaryEvent): void {
    for (const handler of this.#boundaryHandlers) handler(info);
  }
}
