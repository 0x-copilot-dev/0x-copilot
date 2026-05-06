import { stringValue } from "./jsonUtils";

export function safeVisibleText(value: string): string {
  return value
    .replaceAll("/large_tool_results/", "saved result ")
    .replace(/\bmcp[_-]/gi, "")
    .replace(/_com\b/gi, "")
    .replaceAll("_", " ")
    .replace(/\s+/g, " ")
    .trim();
}

function formatBrandWord(value: string): string {
  const brands: Record<string, string> = {
    clickup: "ClickUp",
    github: "GitHub",
    gitlab: "GitLab",
    slack: "Slack",
    google: "Google",
  };
  const normalized = value.toLowerCase();
  return (
    brands[normalized] ?? value.replace(/^\w/, (letter) => letter.toUpperCase())
  );
}

export function humanizeIdentifier(value: string): string {
  const trimmed = value.trim();
  if (!trimmed) {
    return "Tool";
  }
  const normalized = trimmed
    .replace(/^mcp[_-]/i, "")
    .replace(/[_-]mcp$/i, "")
    .replace(/\bcom$/i, "")
    .replace(/[_-]+/g, " ")
    .trim();
  return normalized
    .split(/\s+/)
    .map(formatBrandWord)
    .join(" ")
    .replace(/\bMcp\b/g, "MCP")
    .replace(/\bApi\b/g, "API")
    .replace(/\bUrl\b/g, "URL");
}

export function formatAgentName(value: string): string {
  return humanizeIdentifier(value);
}

export function isWebSearchTool(toolName: string | undefined): boolean {
  const normalized = toolName?.trim().toLowerCase() ?? "";
  return (
    normalized === "web_search" ||
    normalized === "duckduckgo_search" ||
    normalized === "duckduckgo_search_results" ||
    normalized === "search_web"
  );
}

export function isProjectSearchTool(toolName: string): boolean {
  const normalized = toolName.trim().toLowerCase();
  return (
    normalized === "grep" ||
    normalized === "rg" ||
    normalized === "search_files" ||
    normalized === "file_search"
  );
}

export function toolDisplayName(toolName: string): string {
  const normalized = toolName.trim().toLowerCase();
  if (normalized === "ls" || normalized === "list_files") {
    return "List files";
  }
  if (isWebSearchTool(normalized)) {
    return "Search web";
  }
  if (
    normalized === "grep" ||
    normalized === "rg" ||
    normalized === "search_files" ||
    normalized === "file_search" ||
    normalized === "list_files"
  ) {
    return "Search project files";
  }
  if (normalized === "read_file") {
    return "Read file";
  }
  if (normalized === "shell") {
    return "Run command";
  }
  return humanizeIdentifier(toolName || "tool");
}

function toolRunningTitle(toolName: string, displayName: string): string {
  const normalized = toolName.trim().toLowerCase();
  if (isWebSearchTool(normalized)) {
    return "Searching the web";
  }
  if (isProjectSearchTool(normalized)) {
    return "Searching project files";
  }
  if (normalized === "ls" || normalized === "list_files") {
    return "Listing files";
  }
  return `Running ${displayName}`;
}

function toolCompletedTitle(toolName: string, displayName: string): string {
  const normalized = toolName.trim().toLowerCase();
  if (isWebSearchTool(normalized)) {
    return "Searched the web";
  }
  if (isProjectSearchTool(normalized)) {
    return "Searched project files";
  }
  if (normalized === "ls" || normalized === "list_files") {
    return "Listed files";
  }
  return displayName;
}

export function inlineToolTitle(
  toolName: string,
  status: string,
  isError: boolean | undefined,
  result: unknown,
): string {
  const displayName = toolDisplayName(toolName);
  if (isError || status === "incomplete") {
    return `${displayName} failed`;
  }
  if (status === "running") {
    return toolRunningTitle(toolName, displayName);
  }
  if (result !== undefined) {
    return toolCompletedTitle(toolName, displayName);
  }
  return displayName;
}

export function inlineMcpToolTitle(
  toolName: string,
  requestedTool: string | null,
  displayName: string | null,
  status: string,
): string {
  if (toolName === "load_mcp_server") {
    return displayName ? `Load ${displayName} tools` : "Load connector tools";
  }
  if (toolName === "auth_mcp") {
    return displayName ? `Connect ${displayName}` : "Connect connector";
  }
  const action = toolActionName(requestedTool ?? toolName);
  const connector = displayName ?? "connector";
  if (status === "running") {
    return `${capitalize(action)} ${connector}`;
  }
  return `${capitalizePastTense(action)} ${connector}`;
}

export function mcpToolSummary(
  toolName: string,
  status: string,
  serverName: string | null,
  requestedTool: string | null,
): string {
  const connector = serverName ? humanizeIdentifier(serverName) : "connector";
  const action = toolActionName(requestedTool ?? toolName);
  if (status === "running") {
    if (toolName === "load_mcp_server") {
      return `Loading available tools from ${connector}.`;
    }
    return `${capitalize(action)} ${connector}.`;
  }
  if (status === "requires-action") {
    return `Review ${connector} ${action} before it runs.`;
  }
  return `${connector} action completed.`;
}

export function toolStatusLabel(status: string, isError?: boolean): string {
  if (isError) {
    return "Failed";
  }
  if (status === "requires-action") {
    return "Waiting for permission";
  }
  if (status === "running") {
    return "Running";
  }
  return "Done";
}

export function badgeToneForStatus(
  status: string,
): "neutral" | "success" | "warning" | "danger" | "accent" {
  const normalized = status.toLowerCase();
  if (
    normalized === "complete" ||
    normalized === "completed" ||
    normalized === "done" ||
    normalized === "resolved"
  ) {
    return "success";
  }
  if (
    normalized === "starting" ||
    normalized === "working" ||
    normalized === "still working" ||
    normalized === "waiting" ||
    normalized === "running" ||
    normalized === "action required" ||
    normalized === "waiting for permission"
  ) {
    return "warning";
  }
  if (
    normalized === "could not complete" ||
    normalized === "error" ||
    normalized === "failed"
  ) {
    return "danger";
  }
  return "neutral";
}

export function toolActionName(toolName: string | null): string {
  const normalized = toolName?.trim().toLowerCase() ?? "";
  if (!normalized) {
    return "action";
  }
  if (
    normalized.includes("search") ||
    normalized.includes("filter") ||
    normalized.includes("find") ||
    normalized.includes("list")
  ) {
    return "search";
  }
  if (normalized.includes("read") || normalized.includes("get")) {
    return "read";
  }
  if (
    normalized.includes("create") ||
    normalized.includes("post") ||
    normalized.includes("send") ||
    normalized.includes("update") ||
    normalized.includes("delete")
  ) {
    return "modify";
  }
  return "action";
}

export function safeToolActionLabel(value: string): string {
  const normalized = value
    .replace(/^call_mcp_tool$/i, "action")
    .replace(/^auth_mcp$/i, "connect")
    .replace(/^mcp_/i, "")
    .replace(/_com$/i, "")
    .replace(/^[a-z0-9]+_/, "");
  return safeVisibleText(humanizeIdentifier(normalized));
}

export function safeConnectorDisplayName(value: string | null): string | null {
  if (!value) {
    return null;
  }
  return safeVisibleText(humanizeIdentifier(value));
}

export function mcpApprovalDescription(
  displayName: string | null,
  actionName: string,
  readOnly: boolean | null,
  fallback: unknown,
): string {
  const connector = displayName ?? "this connector";
  if (readOnly === true) {
    return `Enterprise Search wants to ${actionName} ${connector}. Read-only. No changes will be made.`;
  }
  if (readOnly === false) {
    return `Enterprise Search wants to ${actionName} ${connector}. This action may change data.`;
  }
  return (
    stringValue(fallback) ??
    `Enterprise Search wants to run a ${connector} action.`
  );
}

export function emptyResultLabel(toolName?: string): string {
  const normalized = toolName?.toLowerCase() ?? "";
  if (normalized.includes("grep") || normalized.includes("search")) {
    return "No matches found";
  }
  if (normalized.includes("ls") || normalized.includes("list")) {
    return "No files found";
  }
  return "No results";
}

function capitalize(value: string): string {
  return value.charAt(0).toUpperCase() + value.slice(1);
}

function capitalizePastTense(value: string): string {
  if (value === "search") {
    return "Searched";
  }
  if (value === "read") {
    return "Read";
  }
  if (value === "modify") {
    return "Updated";
  }
  return "Ran";
}
