import type {
  McpApprovalCategory,
  McpApprovalReasonCode,
} from "@enterprise-search/api-types";
import { stringValue } from "./jsonUtils";
import { approvalReasonForCode } from "./approvalCopy";

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

export type ToolFamily =
  | "web_search"
  | "project_search"
  | "list_files"
  | "read_file"
  | "shell"
  | "other";

// Single source of truth for "what kind of tool is this". Every
// title-formatter (toolDisplayName, toolRunningTitle, toolCompletedTitle,
// inlineMcpToolTitle) branches on the classification rather than
// re-implementing the same alternation. New tool families are added in
// exactly one place.
export function classifyTool(toolName: string | null | undefined): ToolFamily {
  const normalized = toolName?.trim().toLowerCase() ?? "";
  if (
    normalized === "web_search" ||
    normalized === "duckduckgo_search" ||
    normalized === "duckduckgo_search_results" ||
    normalized === "search_web"
  ) {
    return "web_search";
  }
  if (
    normalized === "grep" ||
    normalized === "rg" ||
    normalized === "search_files" ||
    normalized === "file_search"
  ) {
    return "project_search";
  }
  if (normalized === "ls" || normalized === "list_files") {
    return "list_files";
  }
  if (normalized === "read_file") {
    return "read_file";
  }
  if (normalized === "shell") {
    return "shell";
  }
  return "other";
}

export function isWebSearchTool(toolName: string | undefined): boolean {
  return classifyTool(toolName) === "web_search";
}

export function isProjectSearchTool(toolName: string): boolean {
  return classifyTool(toolName) === "project_search";
}

export function toolDisplayName(toolName: string): string {
  switch (classifyTool(toolName)) {
    case "list_files":
      return "List files";
    case "web_search":
      return "Search web";
    case "project_search":
      return "Search project files";
    case "read_file":
      return "Read file";
    case "shell":
      return "Run command";
    case "other":
      return humanizeIdentifier(toolName || "tool");
  }
}

function toolRunningTitle(toolName: string, displayName: string): string {
  switch (classifyTool(toolName)) {
    case "web_search":
      return "Searching the web";
    case "project_search":
      return "Searching project files";
    case "list_files":
      return "Listing files";
    default:
      return `Running ${displayName}`;
  }
}

function toolCompletedTitle(toolName: string, displayName: string): string {
  switch (classifyTool(toolName)) {
    case "web_search":
      return "Searched the web";
    case "project_search":
      return "Searched project files";
    case "list_files":
      return "Listed files";
    default:
      return displayName;
  }
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

// ``inlineMcpToolTitle`` and ``mcpToolSummary`` used to live here. They
// recomputed the MCP tool title / summary on the client from raw args,
// duplicating logic the backend already owns in
// ``RuntimeEventPresentationProjector._display_title_for`` (which now
// unwraps the dispatcher via ``McpDispatcherUnwrap``). The client-side
// derivation produced ``"Action connector"`` at ``tool_call_started``
// time because the inner args hadn't streamed yet. ``McpTool.tsx`` now
// consumes the projected ``display_title`` / ``summary`` directly — see
// the comment block at the top of that file for the project invariant.

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

export type StatusKind = "running" | "error" | "done" | "neutral";
export type StatusTone =
  | "neutral"
  | "success"
  | "warning"
  | "danger"
  | "accent";

const RUNNING_STATUS_WORDS = new Set([
  "starting",
  "working",
  "still working",
  "waiting",
  "running",
  "action required",
  "waiting for permission",
]);

const ERROR_STATUS_WORDS = new Set(["could not complete", "error", "failed"]);

const DONE_STATUS_WORDS = new Set([
  "complete",
  "completed",
  "done",
  "resolved",
]);

/**
 * Single source of truth for "what does this status string mean
 * visually". Returns both the badge tone (used in chrome) and the
 * icon kind (used by ActivityStatusIcon) so the two surfaces can never
 * disagree on whether a status is running, errored, or done.
 */
export function statusClassification(status: string): {
  kind: StatusKind;
  tone: StatusTone;
} {
  const normalized = status.toLowerCase();
  if (DONE_STATUS_WORDS.has(normalized)) {
    return { kind: "done", tone: "success" };
  }
  if (RUNNING_STATUS_WORDS.has(normalized)) {
    return { kind: "running", tone: "warning" };
  }
  if (ERROR_STATUS_WORDS.has(normalized)) {
    return { kind: "error", tone: "danger" };
  }
  return { kind: "neutral", tone: "neutral" };
}

export function badgeToneForStatus(status: string): StatusTone {
  return statusClassification(status).tone;
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

// PR 4.4.6.1 — approval card copy helpers. The bundle replaces the
// generic "Allow {tool} on {connector}? Approve or deny." with copy
// that answers *what / who / why* in three slots. Tested in
// ApprovalTool.test.tsx; consumers should not synthesize approval
// strings inline.

/** Verb-first action title for an MCP tool. Used as the card heading.
 * "List your Linear issues" reads as a request the user can act on,
 * unlike "Allow List Issues?" which buries the verb. */
export function mcpApprovalActionTitle(
  toolName: string | null,
  displayName: string | null,
  readOnly: boolean | null,
): string {
  const connector = displayName ?? "this connector";
  const verb = mcpApprovalVerb(toolName, readOnly);
  const subject = mcpApprovalSubject(toolName);
  if (subject) {
    return `${verb} your ${connector} ${subject}?`;
  }
  return `${verb} ${connector}?`;
}

function mcpApprovalVerb(
  toolName: string | null,
  readOnly: boolean | null,
): string {
  const action = toolActionName(toolName);
  if (action === "search") {
    return "Search";
  }
  if (action === "read") {
    return "Read";
  }
  if (action === "modify") {
    return readOnly === true ? "Read from" : "Update";
  }
  return readOnly === true ? "Read from" : "Run an action on";
}

function mcpApprovalSubject(toolName: string | null): string | null {
  const normalized = toolName?.trim().toLowerCase() ?? "";
  if (!normalized) {
    return null;
  }
  if (normalized.includes("issue")) return "issues";
  if (normalized.includes("ticket")) return "tickets";
  if (normalized.includes("page")) return "pages";
  if (normalized.includes("doc")) return "docs";
  if (normalized.includes("message")) return "messages";
  if (normalized.includes("channel")) return "channels";
  if (normalized.includes("repo")) return "repos";
  if (normalized.includes("pull")) return "pull requests";
  if (normalized.includes("file")) return "files";
  return null;
}

/** Vendor + access category for the right-hand pill. The pill anchors
 * the card to the MCP server contract — the user knows at a glance
 * which connector and how invasive the call is.
 *
 * PR 4.4.6.2 — server-supplied ``vendor`` / ``category`` win when
 * present; otherwise we fall back to inferring from ``displayName`` +
 * ``readOnly`` so old events (no structured payload) still render. */
export function mcpApprovalCategory(
  displayName: string | null,
  readOnly: boolean | null,
  serverSupplied?: {
    vendor?: string | null;
    category?: McpApprovalCategory | null;
  },
): { vendor: string; access: "READ" | "WRITE" | "ACTION" } {
  const serverVendor = serverSupplied?.vendor?.trim();
  const serverCategory = serverSupplied?.category;
  if (serverVendor && serverCategory) {
    return {
      vendor: serverVendor,
      access: serverCategory.toUpperCase() as "READ" | "WRITE" | "ACTION",
    };
  }
  const vendor = (displayName ?? "Connector").toUpperCase();
  if (readOnly === true) {
    return { vendor, access: "READ" };
  }
  if (readOnly === false) {
    return { vendor, access: "WRITE" };
  }
  return { vendor, access: "ACTION" };
}

/** One-sentence explanation of *why* the user is being asked. Read-only
 * vs. write changes the framing — read calls are routine, writes carry
 * the consent weight.
 *
 * PR 4.4.6.2 — when the server tags the approval with a ``reason_code``
 * we render the matching sentence verbatim. Otherwise we synthesise as
 * before from ``readOnly`` + ``riskLevel``. ``approvalReasonForCode``
 * returns ``null`` for unknown codes, which also falls through. */
export function mcpApprovalReason(
  readOnly: boolean | null,
  riskLevel: string | null,
  fallbackMessage: unknown,
  reasonCode?: McpApprovalReasonCode | null,
): string {
  const supplied = approvalReasonForCode(reasonCode);
  if (supplied) {
    return supplied;
  }
  if (readOnly === true) {
    return "Atlas is asking before reading from this connector for the first time this turn.";
  }
  if (readOnly === false) {
    if (riskLevel === "high") {
      return "Atlas is asking because this writes to a high-risk connector — review the scope below.";
    }
    return "Atlas is asking because this writes outside your workspace.";
  }
  return (
    stringValue(fallbackMessage) ??
    "Atlas is asking before running this connector."
  );
}

/** Persistent rule footer — teaches the user the policy so by the
 * third approval they predict it. */
export function mcpApprovalReassurance(readOnly: boolean | null): string {
  if (readOnly === true) {
    return "Atlas only reads here — no changes will be made.";
  }
  if (readOnly === false) {
    return "You're always asked before Atlas writes outside this chat.";
  }
  return "You're always asked before Atlas runs an unrecognised connector action.";
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
