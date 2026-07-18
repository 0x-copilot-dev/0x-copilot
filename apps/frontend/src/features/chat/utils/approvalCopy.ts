// PR 4.4.6.2 — server-supplied reason codes for the consent card.
//
// Each reason_code maps to one sentence the FE renders verbatim. New
// variants land server-side first; clients on this bundle ignore them
// and fall through to the synthesised fallback.

import type { McpApprovalReasonCode } from "@0x-copilot/api-types";

const REASON_COPY: Record<McpApprovalReasonCode, string> = {
  read_only_first_use:
    "Copilot is asking before reading from this connector for the first time this turn.",
  writes_out_of_workspace:
    "Copilot is asking because this writes outside your workspace.",
  risk_high:
    "Copilot is asking because this writes to a high-risk connector — review the scope below.",
  irreversible: "Copilot is asking because this action can't be undone.",
  default: "Copilot is asking before running this connector.",
};

/** Look up the reason sentence for a server-supplied reason_code.
 * Returns ``null`` when the code is missing or unrecognised; callers
 * fall back to the Phase-1 synthesiser. */
export function approvalReasonForCode(
  code: McpApprovalReasonCode | null | undefined,
): string | null {
  if (!code) {
    return null;
  }
  return REASON_COPY[code] ?? null;
}
