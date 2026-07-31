// useConnectFlow — host-neutral orchestration for the "Connect a tool" flow.
//
// Source: PRD-11 D4. The <ConnectModal> owns its own phase machine (catalog →
// OAuth spinner → permission) purely through `open` / `pending` / `error` +
// callbacks. What USED to live inline in the web route (SSE completion
// tracking, window.open) and was ABSENT on desktop is lifted here so BOTH hosts
// drive one state machine. Copying it into each host binder would be the
// bandaid the standing constraint forbids.
//
// Substrate-clean (chat-surface boundary): NO bare window / fetch / EventSource.
// The two genuinely host-specific capabilities arrive as injected functions:
//
//   • authorize({ slug })   — open the authorization surface. Web opens a popup
//     (window.open) / starts connector OAuth; desktop invokes the main-brokered,
//     slug-scoped connect IPC (the desktop renderer is denied window.open).
//   • onConnect(slug, mode) — persist the chosen access mode on the connected
//     connector (the same PATCH the AccessModeSegment uses).
//
// Completion is host-driven: the host calls `markConnected()` from its own
// signal (web: an SSE `connector.created` envelope; desktop: the connect IPC
// resolving), which clears `pending` so the modal auto-advances.

import { useCallback, useRef, useState } from "react";

import type { ConnectorAccessMode, ConnectorSlug } from "@0x-copilot/api-types";

/** Where to open the authorization surface for a connect step. */
export interface ConnectAuthorizeRequest {
  /** A catalog pick — the slug the host authorizes (desktop: IPC connect). */
  readonly slug?: ConnectorSlug;
}

export interface UseConnectFlowOptions {
  /**
   * Open the authorization surface. Resolves once the host has handed control
   * to its auth path; rejects (with a message) when it cannot. For a catalog
   * pick the host drives completion via `markConnected`; a rejection surfaces
   * inline in the modal.
   */
  readonly authorize: (request: ConnectAuthorizeRequest) => Promise<void>;
  /** Persist the picked access mode on the connected connector, then close. */
  readonly onConnect: (
    slug: ConnectorSlug,
    permission: ConnectorAccessMode,
  ) => Promise<void>;
}

export interface ConnectFlow {
  readonly open: boolean;
  readonly pending: boolean;
  readonly error: string | null;
  /** Open the modal (the "Connect a tool" CTA). */
  readonly openConnect: () => void;
  /** Close + fully reset the flow. */
  readonly closeConnect: () => void;
  /** A catalog entry was picked — start the OAuth round-trip. */
  readonly onSelectEntry: (slug: ConnectorSlug) => void;
  /** Terminal Connect — persist the chosen permission. */
  readonly onConnect: (
    slug: ConnectorSlug,
    permission: ConnectorAccessMode,
  ) => void;
  /**
   * Host completion signal. A catalog pick (`slug` matches the one being
   * authorized, or omitted) resolves the OAuth spinner so the modal advances
   * from catalog to permission.
   */
  readonly markConnected: (slug?: ConnectorSlug) => void;
}

function toMessage(error: unknown, fallback: string): string {
  if (error instanceof Error && error.message.length > 0) return error.message;
  if (typeof error === "string" && error.length > 0) return error;
  return fallback;
}

export function useConnectFlow(options: UseConnectFlowOptions): ConnectFlow {
  const { authorize, onConnect } = options;

  const [open, setOpen] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Which slug the OAuth round-trip is authorizing — a ref so a host completion
  // signal always sees the latest value without re-rendering the caller.
  const connectingSlugRef = useRef<ConnectorSlug | null>(null);

  const reset = useCallback((): void => {
    connectingSlugRef.current = null;
    setPending(false);
    setError(null);
  }, []);

  const openConnect = useCallback((): void => {
    reset();
    setOpen(true);
  }, [reset]);

  const closeConnect = useCallback((): void => {
    reset();
    setOpen(false);
  }, [reset]);

  const onSelectEntry = useCallback(
    (slug: ConnectorSlug): void => {
      connectingSlugRef.current = slug;
      setError(null);
      setPending(true);
      authorize({ slug }).catch((err: unknown) => {
        connectingSlugRef.current = null;
        setPending(false);
        setError(toMessage(err, "Could not start the OAuth flow."));
      });
    },
    [authorize],
  );

  const handleConnect = useCallback(
    (slug: ConnectorSlug, permission: ConnectorAccessMode): void => {
      setPending(true);
      setError(null);
      onConnect(slug, permission).then(
        () => {
          closeConnect();
        },
        (err: unknown) => {
          setPending(false);
          setError(toMessage(err, "Could not connect the tool."));
        },
      );
    },
    [onConnect, closeConnect],
  );

  const markConnected = useCallback((slug?: ConnectorSlug): void => {
    const connecting = connectingSlugRef.current;
    if (connecting === null) return;
    if (slug !== undefined && slug !== connecting) return;
    connectingSlugRef.current = null;
    setPending(false);
    setError(null);
  }, []);

  return {
    open,
    pending,
    error,
    openConnect,
    closeConnect,
    onSelectEntry,
    onConnect: handleConnect,
    markConnected,
  };
}
