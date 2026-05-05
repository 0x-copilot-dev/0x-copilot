import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useRef,
  type ReactElement,
  type ReactNode,
} from "react";

/**
 * Approval-focus registry (PR 2.2).
 *
 * Lets `⌘↩` approve "the visible approval card" without coupling the
 * keymap to ApprovalTool internals. Each currently-rendered, unresolved
 * approval card registers itself on mount and unregisters on unmount via
 * a tiny `useEffect`. The keymap then calls `approveTopmost()` which
 * invokes the bottom-most registered handler — which corresponds to the
 * approval closest to the composer (the one the user just scrolled to).
 *
 * Registrations are stored in a `Map` whose insertion order is the
 * canonical "latest first" order JS guarantees.
 */

export interface RegisteredApproval {
  approvalId: string;
  approve: () => void;
}

interface ApprovalFocusApi {
  register: (approval: RegisteredApproval) => void;
  unregister: (approvalId: string) => void;
  /** Approves the bottom-most (last-registered) approval; returns true if one fired. */
  approveTopmost: () => boolean;
  /** Test helper — exposed so unit tests can assert registration order. */
  size: () => number;
}

const ApprovalFocusContext = createContext<ApprovalFocusApi | null>(null);

export function ApprovalFocusProvider({
  children,
}: {
  children: ReactNode;
}): ReactElement {
  // The map is held in a ref so the provider doesn't re-render on each
  // register/unregister. The keymap reads on demand, so render is unnecessary.
  const registry = useRef(new Map<string, RegisteredApproval>());

  const register = useCallback((approval: RegisteredApproval) => {
    // Re-inserting under the same id moves it to the bottom of the
    // insertion order — that's the desired "most recently rendered wins"
    // semantics when an approval tool re-mounts.
    registry.current.delete(approval.approvalId);
    registry.current.set(approval.approvalId, approval);
  }, []);

  const unregister = useCallback((approvalId: string) => {
    registry.current.delete(approvalId);
  }, []);

  const approveTopmost = useCallback((): boolean => {
    const entries = [...registry.current.values()];
    const last = entries.at(-1);
    if (!last) {
      return false;
    }
    last.approve();
    return true;
  }, []);

  const size = useCallback(() => registry.current.size, []);

  const api = useMemo<ApprovalFocusApi>(
    () => ({ register, unregister, approveTopmost, size }),
    [approveTopmost, register, size, unregister],
  );
  return (
    <ApprovalFocusContext.Provider value={api}>
      {children}
    </ApprovalFocusContext.Provider>
  );
}

/**
 * Reads the registry. Returns a no-op shape when the provider isn't
 * mounted — which keeps the ApprovalTool usable in storybook / standalone
 * test renders without forcing every consumer to wrap.
 */
export function useApprovalFocus(): ApprovalFocusApi {
  const ctx = useContext(ApprovalFocusContext);
  if (ctx) {
    return ctx;
  }
  return NO_OP;
}

const NO_OP: ApprovalFocusApi = {
  register: () => {},
  unregister: () => {},
  approveTopmost: () => false,
  size: () => 0,
};
