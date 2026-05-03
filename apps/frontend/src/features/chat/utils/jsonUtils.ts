export type SearchSource = {
  title: string;
  link: string | null;
  snippet: string | null;
  trust: string | null;
};

export type LargeToolResult = {
  path: string;
  callId: string | null;
};

export function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

export function stringValue(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null;
}

export function parseJsonValue(value: unknown): unknown | null {
  if (typeof value !== "string") {
    return null;
  }
  try {
    return JSON.parse(value) as unknown;
  } catch {
    return null;
  }
}

export function parseJsonObject(
  value: unknown,
): Record<string, unknown> | null {
  if (value && typeof value === "object" && !Array.isArray(value)) {
    return value as Record<string, unknown>;
  }
  if (typeof value !== "string") {
    return null;
  }
  try {
    return asRecord(JSON.parse(value) as unknown);
  } catch {
    return null;
  }
}

export function compactRecord(
  record: Record<string, unknown>,
): Record<string, unknown> | null {
  const entries = Object.entries(record).filter(([, value]) =>
    hasVisibleValue(value),
  );
  return entries.length > 0 ? Object.fromEntries(entries) : null;
}

export function hasVisibleValue(value: unknown): boolean {
  if (value === undefined || value === null) {
    return false;
  }
  if (typeof value === "string") {
    return value.trim().length > 0;
  }
  if (Array.isArray(value)) {
    return value.length > 0;
  }
  if (typeof value === "object") {
    return Object.keys(value).length > 0;
  }
  return true;
}

export function formatToolValue(value: unknown): string {
  if (typeof value === "string") {
    return value;
  }
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

export function formatInlineValue(value: unknown): string {
  if (Array.isArray(value)) {
    return value.length === 0 ? "[]" : `${value.length} items`;
  }
  if (typeof value === "string") {
    return summarizeInlineString(value);
  }
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  if (value && typeof value === "object") {
    return "{...}";
  }
  return String(value);
}

export function summarizeInlineString(value: string): string {
  const trimmed = value.trim();
  if (!trimmed) {
    return "empty";
  }
  const lineCount = trimmed.split(/\r\n|\r|\n/).length;
  if (lineCount > 1) {
    return `${lineCount} lines`;
  }
  return trimmed.length > 90 ? `${trimmed.slice(0, 87)}...` : trimmed;
}

export function truncateText(value: string, maxLength: number): string {
  if (value.length <= maxLength) {
    return value;
  }
  const truncated = value.slice(0, maxLength - 3).replace(/\s+\S*$/, "");
  return `${truncated || value.slice(0, maxLength - 3)}...`;
}

export function formatMilliseconds(value: number): string {
  if (value < 1000) {
    return `${Math.max(0, Math.round(value))}ms`;
  }
  return `${formatNumber(value / 1000)}s`;
}

export function formatNumber(value: number): string {
  return Number.isInteger(value)
    ? String(value)
    : value.toFixed(2).replace(/\.?0+$/, "");
}

export function formatDateTime(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

export function formatArgLabel(key: string): string {
  return key.replaceAll("_", " ");
}

export function shouldRenderBlockValue(value: string): boolean {
  return value.includes("\n") || value.length > 120;
}

const hiddenToolArgKeys = new Set([
  "status",
  "summary",
  "delta",
  "deltas",
  "event_type",
  "action_id",
  "approval_id",
  "approval_kind",
  "auth_url",
  "display_name",
  "native_interrupt_id",
  "presentation",
  "server_id",
  "server_name",
  "source_tool_call_id",
  "tool_name",
]);

export function parseToolArgs(
  argsText: string,
): Record<string, unknown> | null {
  try {
    return asRecord(JSON.parse(argsText) as unknown);
  } catch {
    return null;
  }
}

export function visibleToolArgEntries(
  args: Record<string, unknown>,
): Array<[string, unknown]> {
  return Object.entries(args).filter(([key, entry]) => {
    return !hiddenToolArgKeys.has(key) && entry !== null && entry !== undefined;
  });
}

export function mcpContentText(content: unknown): string | null {
  if (typeof content === "string") {
    return content;
  }
  if (!Array.isArray(content)) {
    return null;
  }
  for (const item of content) {
    const record = asRecord(item);
    const text = stringValue(record.text);
    if (text) {
      return text;
    }
  }
  return null;
}

export function displayToolResult(value: unknown): unknown {
  const parsed = parseJsonValue(value) ?? value;
  const parsedRecord = asRecord(parsed);
  const output = asRecord(parsedRecord.output ?? parsed);
  const content = output.content;
  const text = mcpContentText(content) ?? stringValue(output.text);
  if (text) {
    return parseJsonValue(text) ?? text;
  }
  return parsed ?? value;
}

export function largeToolResultText(value: unknown): string | null {
  if (typeof value === "string") {
    return value;
  }
  const record = asRecord(value);
  const output = asRecord(record.output);
  return (
    mcpContentText(record.content) ??
    mcpContentText(output.content) ??
    stringValue(record.content) ??
    stringValue(output.content) ??
    stringValue(output.text)
  );
}

export function largeToolResultPath(value: unknown): string | null {
  if (typeof value === "string") {
    const match = value.match(/(\/large_tool_results\/[A-Za-z0-9_-]+)/);
    return match?.[1] ?? null;
  }
  if (Array.isArray(value)) {
    for (const item of value) {
      const path = largeToolResultPath(item);
      if (path) {
        return path;
      }
    }
    return null;
  }
  if (value && typeof value === "object") {
    for (const entry of Object.values(value as Record<string, unknown>)) {
      const path = largeToolResultPath(entry);
      if (path) {
        return path;
      }
    }
  }
  return null;
}

export function largeToolResultFromValue(
  value: unknown,
): LargeToolResult | null {
  const text = largeToolResultText(value);
  if (text === null) {
    const path = largeToolResultPath(value);
    return path ? { path, callId: null } : null;
  }
  const pathMatch = text.match(/(\/large_tool_results\/[A-Za-z0-9_-]+)/);
  if (!pathMatch) {
    return null;
  }
  const callMatch = text.match(/tool call\s+([A-Za-z0-9_-]+)/);
  return {
    path: pathMatch[1],
    callId: callMatch?.[1] ?? null,
  };
}

function sourceTrustLabel(title: string, link: string | null): string | null {
  const combined = `${title} ${link ?? ""}`.toLowerCase();
  if (
    combined.includes("docs.slack.dev") ||
    combined.includes("slack.com/help") ||
    combined.includes("modelcontextprotocol.io")
  ) {
    return "Official";
  }
  if (combined.includes("github.com")) {
    return "Community";
  }
  return null;
}

export function resultRowsFromValue(
  value: unknown,
): Record<string, unknown>[] | null {
  const parsed = parseJsonValue(value) ?? value;
  if (Array.isArray(parsed)) {
    const rows = parsed
      .map(asRecord)
      .filter((row) => Object.keys(row).length > 0);
    return rows.length > 0 ? rows : null;
  }
  const record = asRecord(parsed);
  for (const candidate of [
    record.results,
    record.items,
    record.sources,
    asRecord(record.output).results,
    asRecord(record.output).items,
  ]) {
    if (Array.isArray(candidate)) {
      const rows = candidate
        .map(asRecord)
        .filter((row) => Object.keys(row).length > 0);
      if (rows.length > 0) {
        return rows;
      }
    }
  }
  const text =
    mcpContentText(record.content) ??
    stringValue(record.text) ??
    stringValue(asRecord(record.output).text);
  if (text) {
    const parsedText = parseJsonValue(text);
    if (Array.isArray(parsedText)) {
      const rows = parsedText
        .map(asRecord)
        .filter((row) => Object.keys(row).length > 0);
      return rows.length > 0 ? rows : null;
    }
    const parsedRecord = asRecord(parsedText);
    if (Array.isArray(parsedRecord.results)) {
      const rows = parsedRecord.results
        .map(asRecord)
        .filter((row) => Object.keys(row).length > 0);
      return rows.length > 0 ? rows : null;
    }
  }
  return null;
}

export function searchSourcesFromValue(value: unknown): SearchSource[] | null {
  const rows = resultRowsFromValue(value);
  if (!rows) {
    return null;
  }
  const sources = rows
    .map((row) => {
      const title =
        stringValue(row.title) ??
        stringValue(row.name) ??
        stringValue(row.url) ??
        stringValue(row.link);
      if (!title) {
        return null;
      }
      const link = stringValue(row.link) ?? stringValue(row.url);
      return {
        title,
        link,
        snippet:
          stringValue(row.snippet) ??
          stringValue(row.description) ??
          stringValue(row.content),
        trust: sourceTrustLabel(title, link),
      };
    })
    .filter((source): source is SearchSource => source !== null);
  return sources.length > 0 ? sources : null;
}
