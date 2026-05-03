import type {
  RuntimeEventEnvelope,
  RuntimeEventPresentation,
} from "@enterprise-search/api-types";
import { asRecord, stringValue } from "../utils/jsonUtils";
import { argsTextFromRecord, hiddenToolArgKeys } from "./payloadHelpers";
import { isToolCallPart, jsonArgs, payloadString } from "./recordHelpers";
import type { ChatItem } from "./types";

const TERMINAL_KINDS = new Set<RuntimeEventPresentation["kind"]>([
  "result",
  "error",
  "approval",
  "auth",
]);

function isTerminalKind(kind: RuntimeEventPresentation["kind"]): boolean {
  return TERMINAL_KINDS.has(kind);
}

// Newer event for the same call_id wins, with one invariant: never regress
// a terminal card back to progress. The backend now resolves a single
// coherent presentation per event (deterministic templates → tool template
// → payload projector → minimal envelope) and emits LLM polish as a
// body-only patch. That makes the merge rule trivial — there is no longer
// any "preliminary vs. enriched" race the client has to reconcile.
export function preferredPresentation(
  current: RuntimeEventPresentation | null,
  next: RuntimeEventPresentation | null,
): RuntimeEventPresentation | null {
  if (!current) return next;
  if (!next) return current;
  if (isTerminalKind(current.kind) && !isTerminalKind(next.kind)) {
    return current;
  }
  return next;
}

export function patchToolPartPresentation(
  items: ChatItem[],
  event: RuntimeEventEnvelope,
): ChatItem[] {
  if (!event.presentation) {
    return items;
  }
  const targetId =
    payloadString(event.payload, "call_id") ??
    payloadString(event.payload, "approval_id") ??
    payloadString(event.payload, "source_tool_call_id");
  if (!targetId) {
    return items;
  }
  return items.map((item) => {
    if (item.kind !== "message" || item.role !== "assistant") {
      return item;
    }
    const updated = item.content.map((part) => {
      if (!isToolCallPart(part) || part.toolCallId !== targetId) {
        return part;
      }
      const args = asRecord(part.args);
      const merged = preferredPresentation(
        presentationFromValue(args.presentation),
        event.presentation ?? null,
      );
      if (!merged) {
        return part;
      }
      const nextArgs = { ...args, presentation: merged };
      return {
        ...part,
        args: jsonArgs(nextArgs),
        argsText: argsTextFromRecord(nextArgs, hiddenToolArgKeys),
      };
    });
    return { ...item, content: updated };
  });
}

export function presentationFromValue(
  value: unknown,
): RuntimeEventPresentation | null {
  const record = asRecord(value);
  const title = stringValue(record.title);
  const statusLabel = stringValue(record.status_label);
  const kind = stringValue(record.kind);
  if (!title || !statusLabel || !kind) {
    return null;
  }
  return {
    title,
    summary: stringValue(record.summary),
    status_label: statusLabel as RuntimeEventPresentation["status_label"],
    kind: kind as RuntimeEventPresentation["kind"],
    group_key: stringValue(record.group_key),
    primary_entity: stringValue(record.primary_entity),
    action_label: stringValue(record.action_label),
    result_preview: Array.isArray(record.result_preview)
      ? record.result_preview.flatMap((item) => {
          const row = asRecord(item);
          const rowTitle = stringValue(row.title);
          return rowTitle
            ? [
                {
                  title: rowTitle,
                  subtitle: stringValue(row.subtitle),
                  url: stringValue(row.url),
                  badge: stringValue(row.badge),
                },
              ]
            : [];
        })
      : [],
    debug_label: stringValue(record.debug_label),
  };
}

export function isInternalCheckpointDelta(delta: string): boolean {
  return /^checkpoint\s*:/i.test(delta.trimStart());
}
