import type { ReactNode } from "react";
import {
  displayToolResult,
  formatToolValue,
  hasVisibleValue,
  parseJsonValue,
  stringValue,
} from "../../utils/jsonUtils";
import { formatDetailValue } from "./formatDetailValue";

// Recursively un-stringify nested JSON (MCP tool results often wrap the
// real payload as a JSON-stringified ``text`` field). Bounded depth so
// pathological inputs don't loop.
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

export function approvalDetailsContent(
  args: Record<string, unknown>,
  result: unknown,
): ReactNode | null {
  const reason = stringValue(args.reason);
  const toolArgs = args.arguments;
  const decision = result !== undefined ? deepUnescapeJson(result) : undefined;
  if (!reason && !hasVisibleValue(toolArgs) && decision === undefined) {
    return null;
  }
  return (
    <>
      {reason ? (
        <>
          <small>Reason</small>
          <p>{reason}</p>
        </>
      ) : null}
      {hasVisibleValue(toolArgs) ? (
        <>
          <small>Arguments</small>
          {formatDetailValue(deepUnescapeJson(toolArgs))}
        </>
      ) : null}
      {decision !== undefined ? (
        <>
          <small>Decision</small>
          <pre>
            {formatToolValue(deepUnescapeJson(displayToolResult(decision)))}
          </pre>
        </>
      ) : null}
    </>
  );
}
