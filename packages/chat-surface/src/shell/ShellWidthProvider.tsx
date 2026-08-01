// ShellWidthProvider — the shell's observed width class, published to the tree.
//
// Source: docs/plan/windowed-mode/PRD-00-overview.md §4 (D-0.2, FR-0.3).
//
// `ChatShell` installs ONE `ResizeObserver` on its root and provides the derived
// class here; any descendant reads it with `useShellWidthClass()`. Threading a
// prop through ten components instead would guarantee that the tenth one is
// forgotten — which is the shape of the bug this whole program is about.
//
// A missing provider resolves to `wide` rather than throwing, matching
// `useOptionalDeploymentProfile`'s fail-safe stance: a component rendered in a
// bare test harness must not explode, and `wide` is the historical layout.

import {
  createContext,
  useContext,
  type ReactElement,
  type ReactNode,
} from "react";

import { DEFAULT_SHELL_WIDTH_CLASS, type ShellWidthClass } from "./layout";

const ShellWidthContext = createContext<ShellWidthClass>(
  DEFAULT_SHELL_WIDTH_CLASS,
);

export interface ShellWidthProviderProps {
  readonly value: ShellWidthClass;
  readonly children?: ReactNode;
}

export function ShellWidthProvider({
  value,
  children,
}: ShellWidthProviderProps): ReactElement {
  return (
    <ShellWidthContext.Provider value={value}>
      {children}
    </ShellWidthContext.Provider>
  );
}

/**
 * The surface's current width class. `wide` when no provider is mounted, so a
 * component under test renders the historical layout by default.
 */
export function useShellWidthClass(): ShellWidthClass {
  return useContext(ShellWidthContext);
}
