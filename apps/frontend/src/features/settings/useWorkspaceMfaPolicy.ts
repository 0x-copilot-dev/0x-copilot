// PRD: docs/architecture/prds/05-workspace-mfa-hook.md
//
// Workspace MFA policy hook — same hook shape as `useWorkspace` and
// `useWorkspaceDefaults` (PR6 / `useMutableRecord`). Replaces the
// inline `useState` + `useEffect` + cancellation ref that
// `WorkspaceMfaSettings.tsx` used to hand-roll.

import type {
  UpdateWorkspaceMfaPolicyRequest,
  WorkspaceMfaPolicy,
} from "@0x-copilot/api-types";

import {
  getWorkspaceMfaPolicy,
  updateWorkspaceMfaPolicy,
} from "../../api/workspaceMfaApi";
import {
  useMutableRecord,
  type MutableRecordState,
} from "../../api/useResource";

export type UseWorkspaceMfaPolicyResult = MutableRecordState<
  WorkspaceMfaPolicy,
  UpdateWorkspaceMfaPolicyRequest
>;

export function useWorkspaceMfaPolicy(): UseWorkspaceMfaPolicyResult {
  return useMutableRecord(getWorkspaceMfaPolicy, updateWorkspaceMfaPolicy, {
    load: "Could not load MFA policy.",
    save: "Could not save MFA policy.",
  });
}
