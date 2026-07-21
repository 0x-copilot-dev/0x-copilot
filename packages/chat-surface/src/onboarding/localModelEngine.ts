// FTUE local-model engine helpers (P2) — pure functions the P2 hook + card and
// the P3 composer/ack share. These operate ON P1's `FirstRunEngine` (owned by
// `firstRun.ts`); P2 does NOT redefine that type — it only adds the download
// progress feed (`localModelPct`) and the pure label/percent/tag helpers here.
//
// Substrate-agnostic: no I/O, no globals — just math + string shaping.

import type { LocalModelSummary } from "@0x-copilot/api-types";

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
