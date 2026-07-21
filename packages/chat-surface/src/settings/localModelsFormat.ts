// Formatting helpers shared by <LocalModelsPage> and <DownloadLocalModelModal>
// (DESIGN-SPEC §4/§5). Pure functions — no substrate touchpoints, no tokens.
//
// Kept in one module so the installed-list row and the download-flow progress
// line format bytes / ETA / Ollama status strings identically (DRY: the web
// section duplicated these across PullProgress + InstalledRow).

import type { LocalModelRunPlacement } from "@0x-copilot/api-types";

/** Human-readable byte size, e.g. 808_000_000 → "808 MB". */
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
