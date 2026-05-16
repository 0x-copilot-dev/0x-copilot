// Cross-substrate artifact routes — the desktop's URI scheme model
// (architecture spec §4). Web routes (settings, share) are deliberately
// NOT in this union; they're a web-only extension carried inside the
// host app's wider route type. Components living in chat-surface that
// need to "open an artifact" navigate via `ArtifactRoute`.
export type ArtifactRoute =
  | { readonly kind: "chat"; readonly conversationId: string }
  | { readonly kind: "conversation"; readonly conversationId: string }
  | { readonly kind: "run"; readonly runId: string }
  | {
      readonly kind: "subagent";
      readonly runId: string;
      readonly subagentId: string;
    }
  | {
      readonly kind: "tool-result";
      readonly runId: string;
      readonly stepId: string;
    }
  | { readonly kind: "mcp"; readonly serverId: string }
  | {
      readonly kind: "mcp-tool";
      readonly serverId: string;
      readonly toolName: string;
    }
  | { readonly kind: "skill"; readonly skillId: string }
  | { readonly kind: "workspace"; readonly workspaceId: string };

export interface NavigateOptions {
  /** Replace the current history entry instead of pushing a new one. */
  readonly replace?: boolean;
}

// Generic router port. The substrate (browser history, VS Code commands,
// OS deep-link handlers) sits behind this interface; consumers — both the
// host app and chat-surface components — see a uniform contract.
//
// Implementations MUST notify subscribers for every change, *including*
// changes triggered by their own `navigate` call. That keeps callers from
// needing a separate "after navigate, update my state" step and removes
// a class of desync bugs.
export interface Router<TRoute> {
  /** Current route. Always defined — the implementation picks a default
   *  when the substrate is in an indeterminate state. */
  current(): TRoute;

  navigate(route: TRoute, opts?: NavigateOptions): void;

  /**
   * Subscribe to route changes. Returns an unsubscribe function. Fires
   * for every change source: `navigate()` calls, browser back/forward,
   * URL paste, deep-link, etc. Implementations should deliver synchronously
   * after the substrate-level commit so subscribers can rely on
   * `current()` reflecting the same value inside the handler.
   */
  subscribe(handler: (route: TRoute) => void): () => void;
}
