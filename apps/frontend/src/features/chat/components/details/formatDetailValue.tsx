import type { ReactNode } from "react";
import {
  formatToolValue,
  parseJsonObject,
  shouldRenderBlockValue,
} from "../../utils/jsonUtils";

export function formatDetailValue(value: unknown): ReactNode {
  if (typeof value === "string") {
    const parsed = parseJsonObject(value);
    if (parsed) {
      return <pre>{formatToolValue(parsed)}</pre>;
    }
    return shouldRenderBlockValue(value) ? (
      <pre>{value}</pre>
    ) : (
      <span>{value}</span>
    );
  }
  if (
    typeof value === "number" ||
    typeof value === "boolean" ||
    value === null
  ) {
    return <span>{String(value)}</span>;
  }
  return <pre>{formatToolValue(value)}</pre>;
}
