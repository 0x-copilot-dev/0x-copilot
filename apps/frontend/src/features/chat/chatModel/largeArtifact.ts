import type { RuntimeEventEnvelope } from "@enterprise-search/api-types";
import { asRecord } from "../utils/jsonUtils";
import { toolArgs, toolArgsDelta, toolName } from "./payloadHelpers";
import { objectSummary } from "./recordHelpers";

export function isLargeResultArtifactToolEvent(
  event: RuntimeEventEnvelope,
): boolean {
  const name = toolName(event.payload);
  if (!isLargeResultArtifactToolName(name)) {
    return false;
  }
  return hasLargeResultPath({
    ...toolArgs(event.payload),
    ...toolArgsDelta(event.payload),
  });
}

export function isLargeResultArtifactToolName(
  toolName: string | null,
): boolean {
  if (!toolName) {
    return false;
  }
  const normalized = toolName.trim().toLowerCase();
  return (
    normalized === "read_file" ||
    normalized === "rg" ||
    normalized === "grep" ||
    normalized === "search_files" ||
    normalized.includes("search")
  );
}

export function hasLargeResultPath(args: Record<string, unknown>): boolean {
  return Object.values(args).some(hasLargeResultReference);
}

export function activityResultText(
  result: unknown,
  toolName: string,
): string | null {
  if (hasLargeResultReference(result)) {
    return "Large result saved for internal inspection.";
  }
  if (typeof result === "string" && result.trim()) {
    return result;
  }
  const summary = objectSummary(asRecord(result));
  return (
    summary ?? (result === undefined ? null : `${toolName} returned data.`)
  );
}

export function hasLargeResultReference(value: unknown): boolean {
  if (typeof value === "string") {
    return value.includes("/large_tool_results/");
  }
  if (Array.isArray(value)) {
    return value.some(hasLargeResultReference);
  }
  if (value && typeof value === "object") {
    return Object.values(value as Record<string, unknown>).some(
      hasLargeResultReference,
    );
  }
  return false;
}
