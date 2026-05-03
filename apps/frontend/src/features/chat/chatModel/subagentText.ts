import {
  isSubagentActivityPayload,
  type RuntimeEventEnvelope,
} from "@enterprise-search/api-types";

export function subagentKeyForEvent(
  event: RuntimeEventEnvelope,
): string | null {
  if (isSubagentActivityPayload(event.payload)) {
    return event.payload.task_id;
  }
  return event.task_id ?? event.parent_task_id ?? event.subagent_id ?? null;
}

export function subagentNameForEvent(
  event: RuntimeEventEnvelope,
): string | null {
  const payloadName = isSubagentActivityPayload(event.payload)
    ? (event.payload.subagent_name ?? event.payload.subagent_id)
    : undefined;
  return (
    meaningfulSubagentName(payloadName) ??
    meaningfulSubagentName(event.subagent_id)
  );
}

export function meaningfulSubagentName(
  value: string | null | undefined,
): string | null {
  if (typeof value !== "string") {
    return null;
  }
  const trimmed = value.trim();
  if (!trimmed || trimmed.toLowerCase() === "subagent") {
    return null;
  }
  return trimmed;
}

export function meaningfulSubagentTitle(
  value: string | null | undefined,
): string | null {
  const trimmed = meaningfulDisplayText(value);
  if (trimmed === null) {
    return null;
  }
  const normalized = trimmed.toLowerCase();
  if (
    normalized === "subagent update" ||
    normalized === "subagent" ||
    normalized.endsWith(" subagent")
  ) {
    return null;
  }
  return trimmed;
}

export function meaningfulDisplayText(
  value: string | null | undefined,
): string | null {
  if (typeof value !== "string") {
    return null;
  }
  const trimmed = value.trim();
  return trimmed ? trimmed : null;
}

export function shortSubagentSummary(
  value: string | null | undefined,
): string | null {
  const text = meaningfulDisplayText(value);
  if (text === null) {
    return null;
  }
  const [beforeRequirements] = text.split(
    /\b(?:Provide|Include|For each claim)\b\s*[:,-]?/i,
    1,
  );
  const firstSentence = beforeRequirements
    .replace(/\s+/g, " ")
    .split(/(?<=[.!?])\s+/, 1)[0]
    .trim();
  return truncateText(firstSentence || text, 120);
}

export function truncateText(value: string, maxLength: number): string {
  if (value.length <= maxLength) {
    return value;
  }
  const truncated = value.slice(0, maxLength - 3).replace(/\s+\S*$/, "");
  return `${truncated || value.slice(0, maxLength - 3)}...`;
}
