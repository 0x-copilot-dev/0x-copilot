// Run-activity bus (PRD-12 D1).
//
// A tiny publish/subscribe context so the Run cockpit (a PUBLISHER, living in
// `ChatShell`'s `children`) and the rail's active-run-count hook (a SUBSCRIBER,
// living in `ShellGrid`) can share ONE revalidation signal without either side
// knowing about the other. `ChatShell` mounts the provider OUTSIDE `ShellGrid`
// so both the rail subtree and the cockpit subtree read the same instance.
//
// Why a bus and not a poll: the badge should refresh the instant the user's own
// run changes state (start / finish / cancel), which is the common case. A 30s
// poll is only the safety net for OTHER devices / tabs. `useRunSession`
// publishes on every run-id / run-status transition; `useActiveRunCount`
// revalidates on a debounced publish.
//
// Substrate-agnostic: no `window`/`fetch`/`document`. Pure React context.

import {
  createContext,
  useContext,
  useMemo,
  useRef,
  type ReactElement,
  type ReactNode,
} from "react";

export interface RunActivityBus {
  /** Signal that some run's identity or status just changed. */
  readonly publish: () => void;
  /**
   * Subscribe to publishes. Returns an unsubscribe function. Handlers are
   * called synchronously on each `publish()`; a throwing handler never blocks
   * the others.
   */
  readonly subscribe: (handler: () => void) => () => void;
}

// The inert bus a consumer gets when no provider is mounted. `publish` is a
// no-op and `subscribe` never fires — so a `useRunSession` unit test (no shell
// wrapper) neither throws nor leaks a subscription. This is why the context
// default is a real object, not `null`: consumers never need a null-check.
const INERT_BUS: RunActivityBus = {
  publish: () => {
    /* no-op — no provider mounted */
  },
  subscribe: () => () => {
    /* no-op unsubscribe */
  },
};

const RunActivityBusContext = createContext<RunActivityBus>(INERT_BUS);

/**
 * Mount ONE bus for a shell subtree. `ChatShell` renders this outside
 * `ShellGrid` so the rail (subscriber) and the cockpit in `children`
 * (publisher) share the same fan-out set.
 */
export function RunActivityBusProvider({
  children,
}: {
  readonly children: ReactNode;
}): ReactElement {
  // A stable Set of handlers held in a ref: the bus identity never changes
  // across renders, so subscribers don't churn their effects.
  const handlersRef = useRef<Set<() => void>>(new Set());
  const bus = useMemo<RunActivityBus>(
    () => ({
      publish: () => {
        for (const handler of handlersRef.current) {
          handler();
        }
      },
      subscribe: (handler: () => void) => {
        handlersRef.current.add(handler);
        return () => {
          handlersRef.current.delete(handler);
        };
      },
    }),
    [],
  );
  return (
    <RunActivityBusContext.Provider value={bus}>
      {children}
    </RunActivityBusContext.Provider>
  );
}

/**
 * Read the run-activity bus. Falls back to an inert no-op bus when no provider
 * is mounted, so a component (e.g. `useRunSession`) can publish/subscribe
 * unconditionally without a shell wrapper in tests.
 */
export function useRunActivityBus(): RunActivityBus {
  return useContext(RunActivityBusContext);
}
