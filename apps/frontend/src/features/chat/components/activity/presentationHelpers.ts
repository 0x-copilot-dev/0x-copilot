import type { RuntimeEventPresentation } from "@0x-copilot/api-types";
import {
  parsePresentationRecord,
  parsePresentationRows,
} from "../../chatModel/presentation";
import { asRecord } from "../../utils/jsonUtils";
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
  return parsePresentationRecord(asRecord(args.presentation));
}

// Re-export to preserve the import path call sites use today.
export const presentationRows = parsePresentationRows;
