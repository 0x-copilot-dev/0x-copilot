// Who owns the folder-grant card's five states.
//
// The sibling of `useConnectorConsentStates`, and it exists for the same reason:
// the run stream can report that a folder was asked for, but everything after
// that happens in an OS dialog the stream cannot see. A card left at `pending`
// through the whole round-trip is a card that looks broken while the user is
// being asked something.
//
// ONE HONEST ASYMMETRY WITH OAUTH. `McpAuthPort.beginAuth` hands the user to a
// vendor and never comes back, which is why that hook needs the host to report
// success through `markConnected`. `WorkspaceGrantPort.requestGrant` RESOLVES —
// with `granted`, `cancelled`, or `failed` — so this hook observes the outcome
// itself and there is no `markGranted` twin to get out of sync. What it still
// cannot do is resume the run: that is a decision POST, host-owned exactly like
// `/decision`, so a granted folder calls back out through `onGranted`.
//
// A CANCEL IS NOT A DENIAL and a FAILURE IS NEITHER. Dismissing the OS dialog
// returns the card to `pending` (the user decided nothing, so offer the choice
// again); a broker/OS failure lands on `failed` WITH its message and leaves the
// run paused. Neither resumes the run — resuming without a grant is how an
// ungranted read became an empty listing with a green tick.

import { useCallback, useEffect, useRef, useState } from "react";

import type { WorkspaceGrantCardState } from "../../approvals/WorkspaceGrantCard";
import type { WorkspaceGrantRequest } from "../../approvals/presentation";
import type {
  WorkspaceGrant,
  WorkspaceGrantPort,
} from "../../ports/WorkspaceGrantPort";

export type WorkspaceGrantCardStates = Readonly<
  Record<string, WorkspaceGrantCardState>
>;

export interface WorkspaceGrantCardHandlers {
  /**
   * The folder is now granted — resume the run. Host-owned because it is a
   * decision POST, and deliberately fired only on a real `granted` outcome: a
   * cancel or a failure must leave the run paused.
   */
  readonly onGranted?: (approvalId: string, grant: WorkspaceGrant) => void;
  /** The user declined — resolve the interrupt without the folder. */
  readonly onDenied?: (approvalId: string) => void;
}

export interface WorkspaceGrantCardController {
  /** Per-`approval_id` card state; absent means `pending`. */
  readonly states: WorkspaceGrantCardStates;
  /** Per-`approval_id` failure text, shown verbatim on the `failed` card. */
  readonly failures: Readonly<Record<string, string>>;
  /**
   * Ask the host for the folder this card names. Also the retry and the
   * reverse-a-decline — all three are "ask again", so they are one verb.
   */
  readonly grant: (approvalId: string, request: WorkspaceGrantRequest) => void;
  readonly deny: (approvalId: string) => void;
  /**
   * Back to `pending` — the card's Cancel while the OS dialog is up.
   * Deliberately NOT a port verb: the dialog belongs to the operating system and
   * the port has no abort, so the honest effect is local — stop claiming a
   * dialog is in flight and offer the choice again. If the user answers the
   * dialog anyway, the outcome still arrives and still settles the card.
   */
  readonly cancel: (approvalId: string) => void;
}

const NO_STATES: WorkspaceGrantCardStates = {};
const NO_FAILURES: Readonly<Record<string, string>> = {};

export function useWorkspaceGrantCardStates(
  port: WorkspaceGrantPort | null | undefined,
  handlers?: WorkspaceGrantCardHandlers,
): WorkspaceGrantCardController {
  const [states, setStates] = useState<WorkspaceGrantCardStates>(NO_STATES);
  const [failures, setFailures] =
    useState<Readonly<Record<string, string>>>(NO_FAILURES);

  // Refs so a host passing fresh objects each render does not churn the
  // callbacks below (the connector hook keeps its port the same way).
  const portRef = useRef(port);
  portRef.current = port;
  const handlersRef = useRef(handlers);
  handlersRef.current = handlers;

  // The OS dialog outlives the surface that opened it more often than not — the
  // user switches destination, the run ends, the cockpit rebinds.
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  const set = useCallback(
    (approvalId: string, next: WorkspaceGrantCardState): void => {
      setStates((prev) =>
        prev[approvalId] === next ? prev : { ...prev, [approvalId]: next },
      );
    },
    [],
  );

  const grant = useCallback(
    (approvalId: string, request: WorkspaceGrantRequest): void => {
      const active = portRef.current;
      if (!active) {
        // Nothing asked, nothing claimed. The card's `actionable` flag already
        // keeps this unreachable from the UI; guarding here means a host that
        // wires the callback but not the port cannot strand a card on
        // `granting`, waiting for a dialog nobody opened.
        return;
      }
      // A retry starts clean: the previous failure is no longer what is true.
      setFailures((prev) =>
        prev[approvalId] === undefined
          ? prev
          : Object.fromEntries(
              Object.entries(prev).filter(([id]) => id !== approvalId),
            ),
      );
      // Optimistic, like `beginAuth`: the dialog is about to take the screen,
      // and a card that only reacted on return would read as a dead button.
      set(approvalId, "granting");
      void (async () => {
        try {
          const outcome = await active.requestGrant({
            // The ONE direction a host-absolute path may travel — into consent.
            // Everything downstream of the grant is mount + relative.
            path: request.path,
            ...(request.mode !== null ? { mode: request.mode } : {}),
            reason: request.reason,
          });
          if (!alive.current) {
            return;
          }
          if (outcome.status === "granted") {
            set(approvalId, "granted");
            handlersRef.current?.onGranted?.(approvalId, outcome.grant);
            return;
          }
          if (outcome.status === "cancelled") {
            set(approvalId, "pending");
            return;
          }
          set(approvalId, "failed");
          setFailures((prev) => ({ ...prev, [approvalId]: outcome.message }));
        } catch (cause) {
          if (!alive.current) {
            return;
          }
          set(approvalId, "failed");
          setFailures((prev) => ({ ...prev, [approvalId]: messageOf(cause) }));
        }
      })();
    },
    [set],
  );

  const deny = useCallback(
    (approvalId: string): void => {
      // Terminal but reversible on the card — the whole point of `denied` is
      // that the user can change their mind (same rule as `skipAuth`).
      set(approvalId, "denied");
      handlersRef.current?.onDenied?.(approvalId);
    },
    [set],
  );

  const cancel = useCallback(
    (approvalId: string): void => set(approvalId, "pending"),
    [set],
  );

  return { states, failures, grant, deny, cancel };
}

/** A thrown port becomes a sentence — never a card that just stops moving. */
function messageOf(cause: unknown): string {
  if (cause instanceof Error && cause.message.trim().length > 0) {
    return cause.message;
  }
  return "The folder could not be shared. Nothing was granted.";
}
