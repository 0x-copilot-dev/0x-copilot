// Tool-use approval-policy data seam (DESIGN-SPEC §4 Approval policy · PRD-03
// FR-17/18/19 · D5 web-convergence capstone). The Model & behavior "Approval
// policy" card (`ApprovalPolicy`) is bound to the per-user tool-use policy at
// `/v1/me/policies/tool-use` — the SAME store the runtime consults at run-start
// (D2 enforcement). No fake success: a failed save rejects.
//
// The card speaks three DESIGN-SPEC axes with per-axis mode subsets; the wire
// speaks three kinds × four modes (PRD-03 §6.4 D-7). This module owns the ONE
// bidirectional mapping so neither host duplicates it:
//
//   UI axis    Wire kind      UI modes                     Wire modes
//   ---------  -------------  ---------------------------  ----------------------
//   readOnly   read           auto · ask                   auto · ask
//   write      write          require · ask · auto · block require · ask · auto · block
//   danger     destructive    require · block              require · block
//
// Hydration is DEFENSIVE (PRD-03 NFR-9): the store can hold a mode the UI can't
// show (e.g. `read=block`), so a wire value outside an axis's UI subset degrades
// to the deployment default for that axis — fail-open for read/write, and the
// STRICTER `require` for danger (never an over-permissive `auto`/`ask` on a
// destructive axis). The deployment defaults mirror the backend
// `ToolUsePolicySnapshot._DEFAULT_MODES` (read=auto, write=ask, destructive=
// require), so a fresh user with no stored policy hydrates to the same posture
// the runtime would fail-open to.
//
// Substrate-agnostic: no bare `fetch` / `window` — the adapter only builds
// `TypedRequest`s and calls the injected `Transport.request()`. Both hosts wire
// `createToolUsePolicyPort(transport)` (each binds its own adapter — no
// apps/* → apps/* import).
//
// Facade routes (user bearer, RBAC scope RUNTIME_USE):
//
//   GET  /v1/me/policies/tool-use  → ToolUsePolicyResponse (caller-scoped)
//   PUT  /v1/me/policies/tool-use  → ToolUsePolicyResponse (atomic 3-axis replace)

import type {
  ToolPolicyKind,
  ToolPolicyMode,
  ToolUsePolicyResponse,
  UpdateToolUsePolicyRequest,
} from "@0x-copilot/api-types";

import type { Transport } from "../../ports/Transport";
import type {
  ApprovalPolicyValue,
  DangerApprovalMode,
  ReadOnlyApprovalMode,
  WriteApprovalMode,
} from "../ApprovalPolicy";

/**
 * Deployment defaults — the fail-open posture used before a snapshot loads and
 * whenever a stored value falls outside an axis's UI-legal subset. Mirrors the
 * backend `ToolUsePolicySnapshot._DEFAULT_MODES` (read=auto, write=ask,
 * destructive=require) so the UI never silently diverges from what the runtime
 * enforces when the policy lane is unconfigured (PRD-03 NFR-4/D-5).
 */
export const DEFAULT_APPROVAL_POLICY: ApprovalPolicyValue = {
  readOnly: "auto",
  write: "ask",
  danger: "require",
};

const READ_ONLY_MODES: ReadonlySet<ReadOnlyApprovalMode> = new Set([
  "auto",
  "ask",
]);
const WRITE_MODES: ReadonlySet<WriteApprovalMode> = new Set([
  "require",
  "ask",
  "auto",
  "block",
]);
const DANGER_MODES: ReadonlySet<DangerApprovalMode> = new Set([
  "require",
  "block",
]);

/** The wire kind backing each UI axis (readOnly↔read, danger↔destructive). */
const KIND_FOR_AXIS = {
  readOnly: "read",
  write: "write",
  danger: "destructive",
} as const satisfies Record<keyof ApprovalPolicyValue, ToolPolicyKind>;

function modeForKind(
  response: ToolUsePolicyResponse,
  kind: ToolPolicyKind,
): ToolPolicyMode | undefined {
  // Defensive (PRD-03 NFR-9): a malformed body (missing/non-array `policies`)
  // degrades to the deployment default per axis, never a crash.
  const policies = Array.isArray(response.policies) ? response.policies : [];
  return policies.find((entry) => entry.kind === kind)?.mode;
}

/**
 * Project a wire `ToolUsePolicyResponse` onto the UI `ApprovalPolicyValue`,
 * clamping any mode outside an axis's UI subset to that axis's deployment
 * default (fail-open for read/write; stricter `require` for danger).
 */
export function approvalPolicyFromResponse(
  response: ToolUsePolicyResponse,
): ApprovalPolicyValue {
  const read = modeForKind(response, "read");
  const write = modeForKind(response, "write");
  const destructive = modeForKind(response, "destructive");
  return {
    readOnly:
      read !== undefined && READ_ONLY_MODES.has(read as ReadOnlyApprovalMode)
        ? (read as ReadOnlyApprovalMode)
        : DEFAULT_APPROVAL_POLICY.readOnly,
    write:
      write !== undefined && WRITE_MODES.has(write as WriteApprovalMode)
        ? (write as WriteApprovalMode)
        : DEFAULT_APPROVAL_POLICY.write,
    danger:
      destructive !== undefined &&
      DANGER_MODES.has(destructive as DangerApprovalMode)
        ? (destructive as DangerApprovalMode)
        : DEFAULT_APPROVAL_POLICY.danger,
  };
}

/**
 * Build the atomic 3-axis PUT body from the UI value. UI modes are always a
 * legal subset of the wire modes, so no clamp is needed on the way out.
 */
export function toolUsePolicyRequestFromValue(
  value: ApprovalPolicyValue,
): UpdateToolUsePolicyRequest {
  return {
    policies: [
      { kind: KIND_FOR_AXIS.readOnly, mode: value.readOnly },
      { kind: KIND_FOR_AXIS.write, mode: value.write },
      { kind: KIND_FOR_AXIS.danger, mode: value.danger },
    ],
  };
}

/** The tool-use approval-policy port (both hosts bind the same adapter). */
export interface ApprovalPolicyPort {
  /** `GET /v1/me/policies/tool-use` → the caller's 3-axis approval policy. */
  read(signal?: AbortSignal): Promise<ApprovalPolicyValue>;
  /**
   * `PUT /v1/me/policies/tool-use` — atomic 3-axis replace. Rejects on failure
   * (the host surfaces an honest error — never a fabricated success).
   */
  save(next: ApprovalPolicyValue, signal?: AbortSignal): Promise<void>;
}

/**
 * Default `ApprovalPolicyPort` backed by the injected `Transport`. Encapsulates
 * the `/v1/me/policies/tool-use` GET/PUT + the UI↔wire axis/mode mapping so both
 * hosts bind the identical behaviour (web/desktop lockstep, PRD-03 NFR-2/NFR-6).
 */
export function createToolUsePolicyPort(
  transport: Transport,
): ApprovalPolicyPort {
  return {
    async read(signal) {
      const response = await transport.request<ToolUsePolicyResponse>({
        method: "GET",
        path: "/v1/me/policies/tool-use",
        signal,
      });
      return approvalPolicyFromResponse(response);
    },

    async save(next, signal) {
      const body = toolUsePolicyRequestFromValue(next);
      await transport.request<ToolUsePolicyResponse>({
        method: "PUT",
        path: "/v1/me/policies/tool-use",
        body,
        signal,
      });
    },
  };
}
