// PR-1.5 — substrate-portable copies of the pure helpers the hoisted
// subagent cards depend on.
//
// The subagent presentation family was hoisted from apps/frontend into
// chat-surface so web and desktop render multi-agent runs identically.
// chat-surface MUST NOT import apps/frontend (eslint no-restricted-imports),
// and FR-1.17 keeps the host as the owner of `chatModel/subagentStatus`,
// `utils/activityDataBuilders`, and the shared `utils/{jsonUtils,toolLabels}`
// modules (those files serve the whole web app, not just subagents, so they
// are out of scope for this move). The small pure helpers the moved cards
// need are therefore reproduced here byte-for-byte. They are pure functions
// of their inputs (no DOM, no globals), so the two copies render identically;
// unifying them onto a single `@0x-copilot/api-types` home is a later
// reconciliation (the same deferral the PRD applies to `depth.ts`).
//
// Provenance of each helper is noted inline.

import type { SubagentLifecycleStatus } from "@0x-copilot/api-types";

// ── from apps/frontend/.../utils/jsonUtils.ts ────────────────────────────

export function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

export function stringValue(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null;
}

export function truncateText(value: string, maxLength: number): string {
  if (value.length <= maxLength) {
    return value;
  }
  const truncated = value.slice(0, maxLength - 3).replace(/\s+\S*$/, "");
  return `${truncated || value.slice(0, maxLength - 3)}...`;
}

// ── from apps/frontend/.../utils/toolLabels.ts ───────────────────────────

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
 * visually". Returns both the badge tone (used in chrome) and the icon
 * kind (used by ActivityStatusIcon).
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

// ── from apps/frontend/.../utils/activityDataBuilders.ts ─────────────────

export type SubagentActivityRecord = {
  id: string;
  kind: string;
  title: string;
  status: string;
  summary: string | null;
  inputSummary: string | null;
  result: string | null;
  isError: boolean;
};

export function activityTitle(activity: SubagentActivityRecord): string {
  if (activity.kind === "tool") {
    return activity.isError
      ? `Could not run ${toolDisplayName(activity.title)}`
      : toolDisplayName(activity.title);
  }
  return activity.title;
}

// ── from apps/frontend/.../chatModel/subagentStatus.ts ───────────────────
//
// Only the two functions the view model needs are reproduced (raw→canonical
// normalisation + terminal predicate). The reducer-only predicates
// (isRunningStatus / isResumableStatus / normaliseTerminalStatus) stay
// host-side per FR-1.17.

const STATUS_ALIAS: Record<string, SubagentLifecycleStatus> = {
  queued: "queued",
  running: "running",
  started: "running",
  progress: "running",
  paused: "paused",
  completed: "completed",
  succeeded: "completed",
  success: "completed",
  complete: "completed",
  cancelled: "cancelled",
  canceled: "cancelled",
  failed: "failed",
  error: "failed",
  timed_out: "timed_out",
  timeout: "timed_out",
};

const TERMINAL_STATES: ReadonlySet<SubagentLifecycleStatus> = new Set([
  "completed",
  "cancelled",
  "failed",
  "timed_out",
]);

/**
 * Map a raw wire-format status string to a canonical
 * SubagentLifecycleStatus. `isError` forces "failed"; unknown/empty falls
 * back to `fallback` (default "running", the optimistic in-flight state).
 */
export function normaliseLifecycleStatus(
  raw: string | null | undefined,
  isError: boolean = false,
  fallback: SubagentLifecycleStatus = "running",
): SubagentLifecycleStatus {
  if (isError) return "failed";
  const lc = raw?.trim().toLowerCase() ?? "";
  if (lc.length === 0) return fallback;
  return STATUS_ALIAS[lc] ?? fallback;
}

export function isTerminalStatus(status: SubagentLifecycleStatus): boolean {
  return TERMINAL_STATES.has(status);
}
