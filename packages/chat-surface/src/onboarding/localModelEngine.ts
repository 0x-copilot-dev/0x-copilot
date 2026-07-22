// FTUE local-model engine helpers (P2) — pure functions the P2 hook + card and
// the P3 composer/ack share. These operate ON P1's `FirstRunEngine` (owned by
// `firstRun.ts`); P2 does NOT redefine that type — it only adds the download
// progress feed (`localModelPct`) and the pure label/percent/tag helpers here.
//
// Substrate-agnostic: no I/O, no globals — just math + string shaping.

import type {
  LocalModelErrorKind,
  LocalModelPullEvent,
  LocalModelSummary,
} from "@0x-copilot/api-types";

import type { FirstRunEngine } from "./firstRun";

/**
 * Composer/ack model-pill text.
 *
 *  - local, downloading (`localModelPct` present and < 100) → `"Qwen 3 4B · 41%"`
 *  - local, ready (`localModelPct === 100` or `null`)       → `"Qwen 3 4B"`
 *  - key                                                     → the provider label
 *  - none (`null`)                                           → `""`
 *
 * `localModelPct` is the P1 surface's download feed (owned by `FirstRunSurface`,
 * fed by `useFirstRunLocalModel`). Keeping the label pure means the P3 composer
 * pill and the P2 card render the identical string with zero duplicated logic.
 */
export function firstRunModelPillLabel(
  engine: FirstRunEngine,
  presetName: string,
  localModelPct: number | null,
): string {
  if (engine === null) return "";
  if (engine.kind === "local") {
    if (localModelPct !== null && localModelPct < 100) {
      return `${presetName} · ${Math.round(localModelPct)}%`;
    }
    return presetName;
  }
  // key
  return engine.label;
}

/**
 * Percent [0..100] from a live pull frame, falling back to the preset's
 * `sizeBytes` hint as the denominator until the first `bytes_total` frame
 * arrives; a terminal `done` frame with no byte totals still lands at 100.
 * Same reducer as `DownloadLocalModelModal`, kept in one place so the gate card
 * and the settings modal compute progress identically.
 */
export function pullPercent(
  bytesCompleted: number | null | undefined,
  bytesTotal: number | null | undefined,
  sizeHint: number | null | undefined,
  done: boolean,
): number {
  const total = bytesTotal ?? sizeHint ?? 0;
  const got = bytesCompleted ?? 0;
  if (total > 0) return Math.min(100, (got / total) * 100);
  return done ? 100 : 0;
}

/**
 * Resolve the installed Ollama tag to send as `model_name` for the first run.
 *
 * An HF GGUF pull lands as `hf.co/{repo}:{quant}`, but Ollama's stored casing
 * on that tag is unreliable — so match the freshly-listed installed tags by
 * case-insensitive substring on the repo, and fall back to the literal
 * `hf.co/{repo}:{quant}` if nothing matches (never a hardcoded guess).
 */
export function resolveInstalledTag(
  models: readonly LocalModelSummary[],
  repo: string,
  quant: string,
): string {
  const needle = repo.toLowerCase();
  const hit = models.find((m) => m.name.toLowerCase().includes(needle));
  return hit?.name ?? `hf.co/${repo}:${quant}`;
}

/**
 * Presence check behind the already-installed short-circuit (PRD-P8 §6).
 *
 * `resolveInstalledTag` always answers with a tag — it falls back to the
 * literal `hf.co/{repo}:{quant}` — so it cannot be used to decide whether the
 * preset is actually installed. This is the same match, but honest about a
 * miss: `null` means "not installed, a pull is required".
 */
export function findInstalledTag(
  models: readonly LocalModelSummary[],
  repo: string,
): string | null {
  const needle = repo.toLowerCase();
  return (
    models.find((m) => m.name.toLowerCase().includes(needle))?.name ?? null
  );
}

/**
 * Reduced download progress: the percent the card's bar renders plus the raw
 * byte counts the byte line renders. Byte FORMATTING is not this module's job
 * (`settings/localModelsFormat.ts` owns it) — this is the arithmetic only.
 */
export interface LocalPullProgress {
  /** 0–100, `pullPercent` semantics. */
  readonly pct: number;
  readonly bytesCompleted: number | null;
  readonly bytesTotal: number | null;
}

/** Progress seeded the instant a pull begins (SPEC: the pill starts at 2%). */
export const INITIAL_PULL_PROGRESS: LocalPullProgress = {
  pct: 2,
  bytesCompleted: null,
  bytesTotal: null,
};

/**
 * Fold one live pull frame into the running progress.
 *
 * Ollama interleaves byte-carrying download lines with status-only lines
 * ("verifying sha256", "writing manifest") whose `bytes_*` are null. Reducing
 * each frame in isolation makes the bar snap back to 0% on every one of those;
 * carrying the last known byte counts forward keeps the bar monotonic and lets
 * a `runtime_unreachable` / `transient` break KEEP its progress (PRD-P8 §6 —
 * progress is never silently thrown away).
 */
export function reducePullProgress(
  frame: Pick<LocalModelPullEvent, "bytes_completed" | "bytes_total" | "done">,
  sizeHint: number | null | undefined,
  previous: LocalPullProgress | null,
): LocalPullProgress {
  const bytesTotal = frame.bytes_total ?? previous?.bytesTotal ?? null;
  const bytesCompleted =
    frame.bytes_completed ?? previous?.bytesCompleted ?? null;

  if (frame.done) {
    return {
      pct: 100,
      bytesCompleted: bytesTotal ?? bytesCompleted,
      bytesTotal,
    };
  }
  if (bytesCompleted === null) {
    // Status-only line: keep whatever the last byte-carrying frame proved.
    return { pct: previous?.pct ?? 0, bytesCompleted: null, bytesTotal };
  }
  return {
    pct: pullPercent(bytesCompleted, bytesTotal, sizeHint, false),
    bytesCompleted,
    bytesTotal,
  };
}

/**
 * Classify a failed pull frame (PRD-P8 D1).
 *
 * `error_kind` is optional and nullable: older servers omit it entirely. An
 * unclassified failure degrades to `"terminal"` — stop and surface it — rather
 * than guessing that an automatic retry is safe.
 */
export function classifyPullError(
  kind: LocalModelErrorKind | null | undefined,
): LocalModelErrorKind {
  return kind === "runtime_unreachable" ||
    kind === "transient" ||
    kind === "terminal"
    ? kind
    : "terminal";
}

/** First retry delay, and the ceiling the backoff saturates at (PRD-P8 §6). */
const RETRY_BASE_MS = 1_000;
const RETRY_CEILING_MS = 30_000;

/**
 * Capped exponential backoff for a `transient` failure: 1s, 2s, 4s … 30s.
 * `attempt` is 0-based; anything below 0 is treated as the first attempt.
 */
export function backoffDelayMs(attempt: number): number {
  const n = Number.isFinite(attempt) ? Math.max(0, Math.floor(attempt)) : 0;
  if (n >= 32) return RETRY_CEILING_MS; // avoid 2 ** huge → Infinity
  return Math.min(RETRY_CEILING_MS, RETRY_BASE_MS * 2 ** n);
}
