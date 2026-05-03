import type { RuntimeEventPresentation } from "@enterprise-search/api-types";
import { asRecord, stringValue } from "../../utils/jsonUtils";
import type { ActivityVariant } from "./types";

export function activityVariantForPresentation(
  presentation: RuntimeEventPresentation,
): ActivityVariant {
  if (presentation.kind === "approval") {
    return "approval";
  }
  if (presentation.kind === "auth") {
    return "connector";
  }
  if (presentation.kind === "progress") {
    return "progress";
  }
  return "tool";
}

export function presentationFromArgs(
  args: Record<string, unknown>,
): RuntimeEventPresentation | null {
  const raw = asRecord(args.presentation);
  const title = stringValue(raw.title);
  const status = stringValue(raw.status_label);
  const kind = stringValue(raw.kind);
  if (!title || !status || !kind) {
    return null;
  }
  return {
    title,
    summary: stringValue(raw.summary),
    status_label: status as RuntimeEventPresentation["status_label"],
    kind: kind as RuntimeEventPresentation["kind"],
    group_key: stringValue(raw.group_key),
    primary_entity: stringValue(raw.primary_entity),
    action_label: stringValue(raw.action_label),
    result_preview: presentationRows(raw.result_preview),
    debug_label: stringValue(raw.debug_label),
    confidence: stringValue(
      raw.confidence,
    ) as RuntimeEventPresentation["confidence"],
  };
}

export function presentationRows(
  value: unknown,
): RuntimeEventPresentation["result_preview"] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.flatMap((item) => {
    const row = asRecord(item);
    const title = stringValue(row.title);
    if (!title) {
      return [];
    }
    return [
      {
        title,
        subtitle: stringValue(row.subtitle),
        url: stringValue(row.url),
        badge: stringValue(row.badge),
      },
    ];
  });
}
