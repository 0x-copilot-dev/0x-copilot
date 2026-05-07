import type { ReactNode } from "react";
import {
  displayToolResult,
  formatToolValue,
  parseJsonValue,
} from "../../utils/jsonUtils";

// Recursively un-stringify nested JSON. Mirrors the helper in
// ``toolDetailsContent`` — kept local to avoid a cross-module utility
// just for this. Bounded depth so pathological inputs don't loop.
function deepUnescapeJson(value: unknown, depth = 0): unknown {
  if (depth > 6) {
    return value;
  }
  if (typeof value === "string") {
    const trimmed = value.trim();
    if (trimmed.startsWith("{") || trimmed.startsWith("[")) {
      const parsed = parseJsonValue(trimmed);
      if (parsed !== null) {
        return deepUnescapeJson(parsed, depth + 1);
      }
    }
    return value;
  }
  if (Array.isArray(value)) {
    return value.map((item) => deepUnescapeJson(item, depth + 1));
  }
  if (value && typeof value === "object") {
    const out: Record<string, unknown> = {};
    for (const [key, item] of Object.entries(
      value as Record<string, unknown>,
    )) {
      out[key] = deepUnescapeJson(item, depth + 1);
    }
    return out;
  }
  return value;
}

export function mcpAuthDetails(result: unknown): ReactNode | null {
  if (result === undefined) {
    return null;
  }
  return (
    <>
      <small>Result</small>
      <pre>{formatToolValue(deepUnescapeJson(displayToolResult(result)))}</pre>
    </>
  );
}
