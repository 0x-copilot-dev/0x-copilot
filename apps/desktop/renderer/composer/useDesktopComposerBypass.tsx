// useDesktopComposerBypass — the composer half of filesystem bypass mode
// (PRD-FS-10 §4.3), for the desktop host.
//
// Three tiers, and this hook owns exactly one of them: it READS the master
// switch from the workspace defaults and holds the run/message selection. It
// decides nothing. The switch is re-read from the server rather than cached
// per-session because it is a workspace posture an admin can revoke, and a
// composer that kept offering Bypass after a revoke would be lying about what
// the next send will do.
//
// A read failure resolves to OFF. That is the honest direction: "we could not
// confirm you may skip approvals" must degrade to "you will be asked", never to
// "go ahead" — the same rule the runtime applies when it cannot resolve a
// grant.
//
// The `spend` callback is what makes message scope mean something. Call it
// after a SUCCESSFUL send: a message-scoped Bypass returns to Manual so a
// one-turn choice cannot quietly become a standing one, while a run-scoped one
// persists until the user changes it.

import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import {
  BypassPill,
  bypassSelectionForSend,
  bypassStateAfterSend,
  MANUAL_BYPASS_STATE,
  useTransport,
  type FilesystemBypassSelection,
  type FilesystemBypassState,
} from "@0x-copilot/chat-surface";
import type { WorkspaceDefaultsResponse } from "@0x-copilot/api-types";

export interface UseDesktopComposerBypassOptions {
  /** Disable the pill for reasons other than the master switch. */
  readonly disabled?: boolean;
}

export interface DesktopComposerBypass {
  /** The pill, for `AssistantComposer.bypassTrigger`. */
  readonly bypassTrigger: ReactNode;
  /** The run-create field, or `undefined` when there is nothing to send. */
  readonly filesystemBypass: FilesystemBypassSelection | undefined;
  /** Call after a successful send — spends a message-scoped selection. */
  readonly spend: () => void;
}

export function useDesktopComposerBypass(
  options: UseDesktopComposerBypassOptions = {},
): DesktopComposerBypass {
  const { disabled = false } = options;
  const transport = useTransport();
  const [masterEnabled, setMasterEnabled] = useState(false);
  const [state, setState] =
    useState<FilesystemBypassState>(MANUAL_BYPASS_STATE);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const defaults = await transport.request<WorkspaceDefaultsResponse>({
          method: "GET",
          path: "/v1/agent/workspace/defaults",
        });
        if (cancelled) return;
        setMasterEnabled(
          defaults.behavior_overrides?.filesystem_bypass_enabled === true,
        );
      } catch {
        // Fail closed — see the header. An unreachable switch is an off switch.
        if (!cancelled) setMasterEnabled(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [transport]);

  // A switch turned off underneath a live composer must also drop the standing
  // selection, not merely hide the control: otherwise the next send would carry
  // a bypass the deployment has withdrawn (the server refuses it, but the pill
  // would still be claiming something untrue to the user).
  useEffect(() => {
    if (!masterEnabled) setState(MANUAL_BYPASS_STATE);
  }, [masterEnabled]);

  const bypassTrigger = useMemo(
    () => (
      <BypassPill
        mode={state.mode}
        enabled={masterEnabled}
        disabled={disabled}
        onChange={(mode) => setState((prev) => ({ ...prev, mode }))}
        scope={state.scope}
        onScopeChange={(scope) => setState((prev) => ({ ...prev, scope }))}
      />
    ),
    [state.mode, state.scope, masterEnabled, disabled],
  );

  const spend = useCallback(() => {
    setState((prev) => bypassStateAfterSend(prev));
  }, []);

  return {
    bypassTrigger,
    filesystemBypass: bypassSelectionForSend(state, { masterEnabled }),
    spend,
  };
}
