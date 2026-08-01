// useWorkspaceFolderGrants — the grant list a surface renders, in ONE place.
//
// Lives here rather than in each host because it is the pure half of the seam:
// call the port, hold the result, surface the failure. The impure half (IPC to
// the capability broker, the native folder dialog) is the port's problem. Two
// hosts each writing this reducer is how desktop and web drifted apart before —
// and this particular reducer has a rule that must not be re-derived per host:
//
//   THE BROKER IS THE SOURCE OF TRUTH. After any change we re-read `listGrants`
//   rather than trusting our own bookkeeping, because a grant can disappear
//   without us (revoked in Settings, expired at reboot, a disk unmounted). A
//   pill that outlives its grant is a claim of access the agent does not have.
//
//   A FAILURE IS NOT AN EMPTY LIST. Every failure path sets `error` to something
//   showable and leaves the previous list alone. Collapsing "the broker didn't
//   answer" into "you have no folders" is precisely the defect this subsystem
//   exists to fix, one layer up.
//
// No browser primitives — the package bans them, and there are none to want.

import { useCallback, useEffect, useRef, useState } from "react";

import type {
  WorkspaceGrant,
  WorkspaceGrantPort,
  WorkspaceGrantRequestInput,
} from "../ports/WorkspaceGrantPort";

export interface WorkspaceFolderGrantsState {
  /** Active grants, newest read of the broker's own list, IN THE BROKER'S ORDER. */
  readonly grants: readonly WorkspaceGrant[];
  /**
   * The grant this surface most recently watched the user create, or null.
   *
   * `WorkspaceGrant` carries no timestamp — the broker's renderer projection is
   * deliberately `grantId` / `mount` / `label` / `mode` and nothing else — so
   * "most recent" cannot be read off the list, and the list's ORDER is not a
   * promise anyone made (see `mostRecentFirst`). What we do know for certain is
   * which grant WE just watched appear, so that is what is recorded, and it is
   * cleared the moment the broker stops reporting it.
   */
  readonly lastGrantedId: string | null;
  /** A port call is in flight (the native dialog may be up). */
  readonly busy: boolean;
  /**
   * Last failure, in words a user can act on; null when nothing has failed
   * since the last success. Callers are expected to RENDER this.
   */
  readonly error: string | null;
  /**
   * Ask for a folder. Omit `input` to let the host open its picker; pass a
   * `path` to name the folder up front (the mid-run ask).
   */
  readonly requestGrant: (input?: WorkspaceGrantRequestInput) => Promise<void>;
  readonly revokeGrant: (grantId: string) => Promise<void>;
  /** Re-read the active set (mount, and after any change). */
  readonly refresh: () => Promise<void>;
  /** Dismiss the failure line without retrying. */
  readonly clearError: () => void;
}

const NO_GRANTS: readonly WorkspaceGrant[] = [];

/**
 * Hold the active folder grants for whichever surface is showing them.
 *
 * `port` is nullable on purpose: web has no grant capability, and a null port
 * yields a permanently empty state whose callbacks are no-ops, so a caller can
 * call this hook unconditionally (hook rules) and gate only its RENDER on the
 * port's presence.
 */
export function useWorkspaceFolderGrants(
  port: WorkspaceGrantPort | null | undefined,
): WorkspaceFolderGrantsState {
  const [grants, setGrants] = useState<readonly WorkspaceGrant[]>(NO_GRANTS);
  const [lastGranted, setLastGranted] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // A grant request resolves only when the user answers the OS dialog, which
  // can easily outlive the surface that opened it (they switch destination, the
  // run ends, the pane unmounts). Writing state after that is a React warning
  // at best and a resurrection of a dead pill at worst.
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  const refresh = useCallback(async (): Promise<void> => {
    if (!port) {
      return;
    }
    try {
      const active = await port.listGrants();
      if (alive.current) {
        setGrants(active);
      }
    } catch (cause) {
      // Keep the last known list: a failed read tells us nothing about what the
      // user granted, so blanking the pills would invent a revocation.
      if (alive.current) {
        setError(messageOf(cause, "Couldn't read your shared folders."));
      }
    }
  }, [port]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const requestGrant = useCallback(
    async (input?: WorkspaceGrantRequestInput): Promise<void> => {
      if (!port) {
        return;
      }
      setBusy(true);
      setError(null);
      try {
        const outcome = await port.requestGrant(input);
        if (outcome.status === "failed") {
          if (alive.current) {
            setError(outcome.message);
          }
          return;
        }
        if (outcome.status === "cancelled") {
          // Nothing to say. The user closed a dialog they opened; an error line
          // here would turn their own decision into an app failure. Note what is
          // NOT touched: the list, the error line, and `lastGranted` all stay
          // exactly as they were, so a dismissed dialog leaves the surface
          // unchanged (PRD-FS-10 §4.1).
          return;
        }
        if (alive.current) {
          setLastGranted(outcome.grant.grantId);
        }
        try {
          const active = await port.listGrants();
          if (alive.current) {
            setGrants(active);
          }
        } catch {
          // The grant exists — we just failed to re-read the list. Show the
          // one we were handed rather than dropping a folder the user granted.
          if (alive.current) {
            setGrants((current) =>
              current.some((held) => held.grantId === outcome.grant.grantId)
                ? current
                : [...current, outcome.grant],
            );
          }
        }
      } catch (cause) {
        if (alive.current) {
          setError(messageOf(cause, "Couldn't ask for that folder."));
        }
      } finally {
        if (alive.current) {
          setBusy(false);
        }
      }
    },
    [port],
  );

  const revokeGrant = useCallback(
    async (grantId: string): Promise<void> => {
      if (!port) {
        return;
      }
      setBusy(true);
      setError(null);
      try {
        const outcome = await port.revokeGrant(grantId);
        if (outcome.status === "failed") {
          if (alive.current) {
            setError(outcome.message);
          }
          // Fall through to the re-read anyway: "failed" means we do not know
          // what the broker's set is now, and guessing is what we are avoiding.
        }
        await refresh();
      } catch (cause) {
        if (alive.current) {
          setError(messageOf(cause, "Couldn't stop sharing that folder."));
        }
      } finally {
        if (alive.current) {
          setBusy(false);
        }
      }
    },
    [port, refresh],
  );

  const clearError = useCallback((): void => setError(null), []);

  // A remembered id outlives its grant when the folder is revoked (here or in
  // Settings) — report it only while the broker still lists it, so nothing
  // downstream can feature a grant that no longer exists.
  const lastGrantedId =
    lastGranted !== null &&
    grants.some((grant) => grant.grantId === lastGranted)
      ? lastGranted
      : null;

  return {
    grants,
    lastGrantedId,
    busy,
    error,
    requestGrant,
    revokeGrant,
    refresh,
    clearError,
  };
}

/**
 * The grant to NAME first, then the rest.
 *
 * Callers that show one folder out of several must not take `grants[0]`: the
 * hook hands back whatever order the broker sent, so an unrelated change to how
 * the broker stores or sorts grants would silently rename the folder on screen.
 * The folder a user is thinking about is the one they just attached, so a known
 * `lastGrantedId` leads; everything else keeps its relative order.
 *
 * Returns the input array itself when there is nothing to move, so a caller can
 * keep it in a `useMemo` without churning identity on every render.
 */
export function mostRecentFirst(
  grants: readonly WorkspaceGrant[],
  lastGrantedId: string | null,
): readonly WorkspaceGrant[] {
  if (lastGrantedId === null || grants.length < 2) {
    return grants;
  }
  const index = grants.findIndex((grant) => grant.grantId === lastGrantedId);
  if (index <= 0) {
    return grants;
  }
  const head = grants[index];
  if (head === undefined) {
    return grants;
  }
  return [head, ...grants.filter((_, at) => at !== index)];
}

/** A thrown port becomes a sentence, never an empty state. */
function messageOf(cause: unknown, fallback: string): string {
  if (cause instanceof Error && cause.message.trim().length > 0) {
    return cause.message;
  }
  return fallback;
}
