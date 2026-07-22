// Formatting helpers shared by <LocalModelsPage> and <DownloadLocalModelModal>
// (DESIGN-SPEC §4/§5). Pure functions — no substrate touchpoints, no tokens.
//
// Kept in one module so the installed-list row and the download-flow progress
// line format bytes / ETA / Ollama status strings identically (DRY: the web
// section duplicated these across PullProgress + InstalledRow).

import type { LocalModelRunPlacement } from "@0x-copilot/api-types";

/** Human-readable byte size in BINARY units, e.g. 808_000_000 → "771 MB". */
export function formatBytes(bytes: number): string {
  if (bytes <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const exponent = Math.min(
    units.length - 1,
    Math.floor(Math.log(bytes) / Math.log(1024)),
  );
  const value = bytes / 1024 ** exponent;
  return `${value.toFixed(value >= 10 || exponent === 0 ? 0 : 1)} ${units[exponent]}`;
}

// ---------------------------------------------------------------------------
// formatBytesPair — the FTUE card's "2.4 / 4.3 GB" line (PRD-P8 §5).
// ---------------------------------------------------------------------------
//
// Two deliberate differences from `formatBytes` above, both load-bearing:
//
//  1. ONE unit, chosen from the TOTAL, for both halves. Formatting each value
//     independently gives "2400 MB / 4.3 GB" — two scales in one line, which
//     reads as a bug. The total is the stable half, so it picks the unit.
//  2. SI (1000) rather than binary (1024). The pair line renders directly under
//     the card's frozen header meta "Qwen 3 4B · 4.3 GB · free forever"
//     (PRD-P8 D5 — 4,280,404,704 B, the decimal-GB figure Ollama and HF both
//     report). A 1024-based foot would read "… / 4.0 GB" under a "4.3 GB"
//     header on the SAME card. `formatBytes` stays binary — Settings' installed
//     list and download modal are pinned to it and are not P8's to flip — so
//     this module deliberately carries both bases. Unifying them is a tracked
//     follow-up, not a silent change.
//
// Returns `null` when there is nothing honest to say (no total and no bytes);
// the caller omits the segment rather than printing a fake "0 B".

const PAIR_UNITS = ["B", "KB", "MB", "GB", "TB"] as const;
const PAIR_BASE = 1000;

/** Largest unit index whose scale `bytes` reaches (clamped to the table). */
function pairExponent(bytes: number): number {
  if (bytes < PAIR_BASE) return 0;
  return Math.min(
    PAIR_UNITS.length - 1,
    Math.floor(Math.log(bytes) / Math.log(PAIR_BASE)),
  );
}

/**
 * One half, rendered in an externally chosen unit. Precision follows
 * `formatBytes`' rule (whole numbers at >= 10 or in raw bytes, else one
 * decimal) so a pair and a lone size never disagree about digits; an exact
 * zero is "0", never "0.0".
 */
function inPairUnit(bytes: number, exponent: number): string {
  const value = bytes / PAIR_BASE ** exponent;
  if (value === 0) return "0";
  return value.toFixed(value >= 10 || exponent === 0 ? 0 : 1);
}

function finiteOrNull(value: number | null | undefined): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/**
 * Downloaded-of-total in one shared unit, e.g. `"2.4 / 4.3 GB"`.
 *
 *  - total known (> 0)  → both halves in the total's unit; `completed` is
 *    clamped into `[0, total]` (Ollama can briefly report more completed than
 *    total across layers, and "4.5 / 4.3 GB" reads as broken).
 *  - total unknown, some bytes downloaded → the lone completed size, so a pull
 *    whose `bytes_total` never arrived still shows honest progress.
 *  - nothing known → `null`.
 */
export function formatBytesPair(
  completed: number | null | undefined,
  total: number | null | undefined,
): string | null {
  const totalBytes = finiteOrNull(total);
  const completedBytes = Math.max(0, finiteOrNull(completed) ?? 0);

  if (totalBytes !== null && totalBytes > 0) {
    const exponent = pairExponent(totalBytes);
    const got = Math.min(completedBytes, totalBytes);
    return `${inPairUnit(got, exponent)} / ${inPairUnit(totalBytes, exponent)} ${PAIR_UNITS[exponent]}`;
  }
  if (completedBytes > 0) {
    const exponent = pairExponent(completedBytes);
    return `${inPairUnit(completedBytes, exponent)} ${PAIR_UNITS[exponent]}`;
  }
  return null;
}

/** Compact remaining-time label, e.g. 40 → "40s", 125 → "2m 5s". */
export function formatEta(seconds: number): string {
  if (seconds < 60) return `${Math.ceil(seconds)}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ${Math.round(seconds % 60)}s`;
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
}

/** Friendly label for a raw Ollama pull status line. */
export function humanStatus(status: string): string {
  if (status === "starting") return "Starting…";
  if (status === "resolving") return "Checking size…";
  if (status.startsWith("pulling") || status === "downloading") {
    return "Downloading…";
  }
  if (status.includes("verifying")) return "Verifying…";
  if (status.includes("writing")) return "Finishing…";
  return status;
}

/** Where a loaded model runs — GPU is silent-good, CPU/partial warn "slower". */
export function placementLabel(placement: LocalModelRunPlacement): string {
  if (placement === "gpu") return "GPU";
  if (placement === "cpu") return "CPU — slower";
  return "GPU + CPU — slower";
}
