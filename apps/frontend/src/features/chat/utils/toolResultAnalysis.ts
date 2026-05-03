import {
  asRecord,
  formatArgLabel,
  formatInlineValue,
  largeToolResultFromValue,
  mcpContentText,
  parseJsonObject,
  parseToolArgs,
  resultRowsFromValue,
  stringValue,
  visibleToolArgEntries,
} from "./jsonUtils";
import { safeVisibleText } from "./toolLabels";

export function isComplexToolValue(value: unknown): boolean {
  if (Array.isArray(value)) {
    return value.length > 0;
  }
  if (value && typeof value === "object") {
    return true;
  }
  if (typeof value === "string") {
    return value.length > 120 || value.includes("\n");
  }
  return false;
}

export function hasComplexToolArgs(argsText: string): boolean {
  const args = parseToolArgs(argsText);
  if (args === null) {
    return true;
  }
  return visibleToolArgEntries(args).some(([, value]) =>
    isComplexToolValue(value),
  );
}

export function hasComplexToolResult(value: unknown): boolean {
  if (value === undefined || value === null) {
    return false;
  }
  if (Array.isArray(value)) {
    return value.length > 0;
  }
  if (typeof value === "string") {
    const trimmed = value.trim();
    return trimmed.length > 160 || trimmed.includes("\n");
  }
  if (typeof value === "number" || typeof value === "boolean") {
    return false;
  }
  const record = asRecord(value);
  const keys = Object.keys(record);
  if (keys.length === 0) {
    return false;
  }
  const messageOnly = keys.every((key) =>
    ["message", "content", "summary"].includes(key),
  );
  return !messageOnly || keys.some((key) => isComplexToolValue(record[key]));
}

export function hasRichToolResult(value: unknown): boolean {
  if (value === undefined || value === null) {
    return false;
  }
  if (largeToolResultFromValue(value)) {
    return false;
  }
  if (Array.isArray(value)) {
    return value.length > 3;
  }
  if (typeof value === "string") {
    const trimmed = value.trim();
    return trimmed.length > 360 || trimmed.split(/\r\n|\r|\n/).length > 4;
  }
  const record = asRecord(value);
  const keys = Object.keys(record);
  if (keys.length === 0) {
    return false;
  }
  const output = asRecord(record.output);
  const content = output.content ?? record.content;
  const text = mcpContentText(content) ?? stringValue(output.text);
  if (text) {
    const parsed = parseJsonObject(text);
    if (Array.isArray(parsed?.results) || stringValue(parsed?.overview)) {
      return true;
    }
    return text.length > 360 || text.split(/\r\n|\r|\n/).length > 4;
  }
  const informationalKeys = new Set([
    "message",
    "content",
    "summary",
    "status",
  ]);
  return keys.some((key) => !informationalKeys.has(key));
}

export function summarizeArgs(value: unknown): string | null {
  const entries = visibleToolArgEntries(asRecord(value));
  if (entries.length === 0) {
    return null;
  }
  return entries
    .slice(0, 3)
    .map(
      ([key, entry]) => `${formatArgLabel(key)}: ${formatInlineValue(entry)}`,
    )
    .join(" · ");
}

export function summarizeArgsText(argsText?: string): string | null {
  if (!argsText) {
    return null;
  }
  return summarizeArgs(parseToolArgs(argsText));
}

export function summarizeParsedMainResult(value: unknown): string {
  if (Array.isArray(value)) {
    return value.length === 0 ? "No results" : `${value.length} results`;
  }
  const record = asRecord(value);
  const rows = resultRowsFromValue(record);
  if (rows) {
    return `${rows.length} results`;
  }
  const message =
    stringValue(record.message) ??
    stringValue(record.summary) ??
    stringValue(record.overview);
  return message ? safeVisibleText(message) : "Result returned";
}

export function shouldShowToolDetails(
  argsText: string | undefined,
  result: unknown,
): boolean {
  if (!argsText && result === undefined) {
    return false;
  }
  if (largeToolResultFromValue(result)) {
    return Boolean(argsText && hasComplexToolArgs(argsText));
  }
  return Boolean(
    (argsText && hasComplexToolArgs(argsText)) || hasComplexToolResult(result),
  );
}

export function shouldRenderFullToolCard(
  status: string,
  isError: boolean | undefined,
  result: unknown,
): boolean {
  return (
    isError === true ||
    status === "requires-action" ||
    hasRichToolResult(result)
  );
}

export function shouldRenderFullMcpCard(
  toolName: string,
  status: string,
  isError: boolean | undefined,
  result: unknown,
): boolean {
  if (isError === true || status === "requires-action") {
    return true;
  }
  if (status === "running") {
    return false;
  }
  return toolName === "call_mcp_tool" && hasRichToolResult(result);
}
