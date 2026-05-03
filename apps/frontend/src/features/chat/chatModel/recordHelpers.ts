import { stringValue } from "../utils/jsonUtils";
import type {
  ThreadMessageContentPart,
  ThreadReasoningPart,
  ThreadTextPart,
  ThreadToolCallArgs,
  ThreadToolCallPart,
} from "./types";

export function assistantMessageId(runId: string): string {
  return `assistant-${runId}`;
}

export function isTextPart(
  part: ThreadMessageContentPart,
): part is ThreadTextPart {
  return part.type === "text";
}

export function isReasoningPart(
  part: ThreadMessageContentPart,
): part is ThreadReasoningPart {
  return part.type === "reasoning";
}

export function isToolCallPart(
  part: ThreadMessageContentPart,
): part is ThreadToolCallPart {
  return part.type === "tool-call";
}

export function jsonArgs(value: Record<string, unknown>): ThreadToolCallArgs {
  return value as ThreadToolCallArgs;
}

export function recordArray(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value) ? value.filter(isPlainRecord) : [];
}

export function withoutNullishValues(
  value: Record<string, unknown>,
): Record<string, unknown> {
  return Object.fromEntries(
    Object.entries(value).filter(
      ([, entry]) => entry !== null && entry !== undefined,
    ),
  );
}

export function summarizeRecord(value: Record<string, unknown>): string | null {
  const entries = Object.entries(value).filter(
    ([, entry]) => entry !== null && entry !== undefined,
  );
  if (entries.length === 0) {
    return null;
  }
  return entries
    .slice(0, 3)
    .map(
      ([key, entry]) => `${key.replaceAll("_", " ")}: ${inlineSummary(entry)}`,
    )
    .join(" · ");
}

export function inlineSummary(value: unknown): string {
  if (Array.isArray(value)) {
    return value.length === 0 ? "[]" : `${value.length} items`;
  }
  if (typeof value === "string") {
    const trimmed = value.trim();
    if (!trimmed) {
      return "empty";
    }
    return trimmed.length > 80 ? `${trimmed.slice(0, 77)}...` : trimmed;
  }
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  if (value && typeof value === "object") {
    return "{...}";
  }
  return String(value);
}

export function isPlainRecord(
  value: unknown,
): value is Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return false;
  }
  const prototype = Object.getPrototypeOf(value);
  return prototype === Object.prototype || prototype === null;
}

export function sameText(left: unknown, right: unknown): boolean {
  const leftText = stringValue(left)?.toLowerCase();
  const rightText = stringValue(right)?.toLowerCase();
  return leftText !== undefined && leftText !== null && leftText === rightText;
}

export function titleForEvent(eventType: string): string {
  return eventType
    .split("_")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

export function payloadString(
  payload: Record<string, unknown>,
  key: string,
): string | null {
  const value = payload[key];
  return typeof value === "string" && value.trim() ? value : null;
}

export function textFromPayload(
  payload: Record<string, unknown>,
  key: "message" | "delta" | "summary",
): string | null {
  return payloadString(payload, key);
}

export function objectSummary(
  value: Record<string, unknown> | undefined,
): string | undefined {
  if (value === undefined) {
    return undefined;
  }
  const message =
    payloadString(value, "message") ??
    payloadString(value, "content") ??
    payloadString(value, "summary");
  if (message) {
    return message;
  }
  const keys = Object.keys(value);
  return keys.length > 0 ? `${keys.length} fields returned` : undefined;
}
