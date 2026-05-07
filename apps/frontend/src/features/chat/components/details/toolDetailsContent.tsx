import type { ReactNode } from "react";
import {
  displayToolResult,
  formatToolValue,
  largeToolResultFromValue,
  parseJsonValue,
} from "../../utils/jsonUtils";
import { shouldShowToolDetails } from "../../utils/toolResultAnalysis";

// Recursively un-stringify nested JSON. MCP tool results commonly wrap
// the real payload as a JSON-stringified ``text`` field inside
// ``output.content[]``; the user sees ``{\"issues\":[{\"id\":...}]}`` if
// we don't dig in. Walks objects + arrays and parses any string value
// that begins with `{` or `[`. Bounded depth so we don't loop on weird
// inputs.
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

export function toolDetailsContent(
  argsText: string | undefined,
  result: unknown,
): ReactNode | null {
  if (!shouldShowToolDetails(argsText, result)) {
    return null;
  }
  const parsedArgs =
    argsText !== undefined
      ? deepUnescapeJson(parseJsonValue(argsText) ?? argsText)
      : undefined;
  const parsedResult =
    result !== undefined && !largeToolResultFromValue(result)
      ? deepUnescapeJson(displayToolResult(result))
      : undefined;
  return (
    <>
      {parsedArgs !== undefined ? (
        <>
          <small>Input</small>
          <pre>{formatToolValue(parsedArgs)}</pre>
        </>
      ) : null}
      {parsedResult !== undefined ? (
        <>
          <small>Result</small>
          <pre>{formatToolValue(parsedResult)}</pre>
        </>
      ) : null}
    </>
  );
}
