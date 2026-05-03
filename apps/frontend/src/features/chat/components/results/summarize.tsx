import type { ReactNode } from "react";
import {
  asRecord,
  largeToolResultFromValue,
  mcpContentText,
  parseJsonObject,
  parseJsonValue,
  resultRowsFromValue,
  searchSourcesFromValue,
  stringValue,
  summarizeInlineString,
} from "../../utils/jsonUtils";
import {
  emptyResultLabel,
  isWebSearchTool,
  safeVisibleText,
} from "../../utils/toolLabels";
import { summarizeParsedMainResult } from "../../utils/toolResultAnalysis";
import { McpResultList } from "./McpResultList";
import { SearchSourceList } from "./SearchSourceList";
import { loadedMcpServerSummary } from "./loadedMcpServerSummary";

export function safeMainResultSummary(value: ReactNode): ReactNode {
  if (typeof value !== "string") {
    return value;
  }
  if (value.includes("/large_tool_results/")) {
    return "Large result saved for internal inspection";
  }
  const parsed = parseJsonValue(value);
  if (parsed !== null) {
    return summarizeParsedMainResult(parsed);
  }
  if (value.length > 220 || value.split(/\r\n|\r|\n/).length > 3) {
    return summarizeInlineString(value);
  }
  return safeVisibleText(value);
}

export function summarizeToolValue(
  value: unknown,
  toolName?: string,
): ReactNode {
  const largeResult = largeToolResultFromValue(value);
  if (largeResult) {
    return "Large result saved for the agent to inspect";
  }
  const sources = searchSourcesFromValue(value);
  if (isWebSearchTool(toolName) && sources) {
    return <SearchSourceList sources={sources} />;
  }
  const normalizedValue = parseJsonValue(value) ?? value;
  if (Array.isArray(normalizedValue)) {
    return normalizedValue.length === 0
      ? emptyResultLabel(toolName)
      : `${normalizedValue.length} results`;
  }
  if (typeof normalizedValue === "string") {
    const trimmed = normalizedValue.trim();
    if (trimmed === "[]") {
      return emptyResultLabel(toolName);
    }
    return trimmed || "Completed";
  }
  const record = asRecord(normalizedValue);
  const message =
    stringValue(record.message) ??
    stringValue(record.content) ??
    stringValue(record.summary);
  if (message) {
    return message;
  }
  const keys = Object.keys(record);
  return keys.length > 0 ? `${keys.length} fields returned` : "Completed";
}

export function summarizeMcpResult(value: unknown): ReactNode {
  const loadedServer = loadedMcpServerSummary(value);
  if (loadedServer) {
    return loadedServer;
  }
  const parsed = parseJsonObject(value);
  const output = asRecord(parsed?.output ?? parsed ?? value);
  const content = output.content;
  const text = mcpContentText(content) ?? stringValue(output.text);
  if (text) {
    const parsedText = parseJsonObject(text);
    const overview = stringValue(parsedText?.overview);
    const results = Array.isArray(parsedText?.results)
      ? parsedText.results
      : null;
    if (overview || results) {
      return (
        <div className="aui-mcp-result-preview">
          {overview ? <p>{overview}</p> : null}
          {results ? <McpResultList results={results} /> : null}
        </div>
      );
    }
    return summarizeInlineString(text);
  }
  const genericResults = resultRowsFromValue(value);
  if (genericResults) {
    return <McpResultList results={genericResults} />;
  }
  return summarizeToolValue(value);
}
