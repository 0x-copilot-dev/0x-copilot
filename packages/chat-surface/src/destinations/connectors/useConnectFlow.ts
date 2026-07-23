// useConnectFlow — host-neutral orchestration for the "Connect a tool" flow.
//
// Source: PRD-11 D4. The <ConnectModal> owns its own phase machine (catalog →
// OAuth spinner → permission) purely through `open` / `pending` / `error` +
// callbacks. What USED to live inline in the web route (SSE completion
// tracking, window.open, custom-server create → OAuth) and was ABSENT on
// desktop is lifted here so BOTH hosts drive one state machine. Copying it into
// each host binder would be the bandaid the standing constraint forbids.
//
// Substrate-clean (chat-surface boundary): NO bare window / fetch / EventSource.
// The two genuinely host-specific capabilities arrive as injected functions:
//
//   • authorize({ slug | url }) — open the authorization surface. Web opens a
//     popup (window.open) / starts connector OAuth; desktop invokes the
//     main-brokered, slug-scoped connect IPC and REJECTS url-only requests it
//     cannot open (the desktop renderer is denied window.open).
//   • addCustomServer(input)   — create a custom MCP server (the injected
//     FirstRunConnectorsPort's addCustomServer is the SSOT); returns an
//     `authorizeUrl` when the freshly-created server still needs OAuth.
//   • onConnect(slug, mode)    — persist the chosen access mode on the
//     connected connector (the same PATCH the AccessModeSegment uses).
//
// Completion is host-driven: the host calls `markConnected()` from its own
// signal (web: an SSE `connector.created` envelope; desktop: the connect IPC
// resolving), which clears `pending` so the modal auto-advances / closes.

import { useCallback, useRef, useState } from "react";

import type { ConnectorAccessMode, ConnectorSlug } from "@0x-copilot/api-types";

import type { CustomServerInput } from "./ConnectModal";

/** Where to open the authorization surface for a connect step. */
export interface ConnectAuthorizeRequest {
  /** A catalog pick — the slug the host authorizes (desktop: IPC connect). */
  readonly slug?: ConnectorSlug;
  /** A custom server's OAuth URL — web opens it; desktop rejects. */
  readonly url?: string;
}

/** Result of creating a custom MCP server. */
export interface CustomServerResult {
  /** Present when the server still needs OAuth after creation. */
  readonly authorizeUrl?: string;
}

export interface UseConnectFlowOptions {
  /**
   * Open the authorization surface. Resolves once the host has handed control
   * to its auth path; rejects (with a message) when it cannot. For a catalog
   * pick the host drives completion via `markConnected`; a rejection surfaces
   * inline in the modal.
   */
  readonly authorize: (request: ConnectAuthorizeRequest) => Promise<void>;
  /**
   * Create a custom MCP server (the injected port's `addCustomServer`). Return
   * `{ authorizeUrl }` when the created server needs OAuth. Omit the option
   * entirely to hide the modal's "Add a custom server" affordance.
   */
  readonly addCustomServer?: (
    input: CustomServerInput,
  ) => Promise<CustomServerResult>;
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
  /** Submit the custom-server form. `undefined` when no `addCustomServer`. */
  readonly onAddCustomServer: ((input: CustomServerInput) => void) | undefined;
  /** Terminal Connect — persist the chosen permission. */
  readonly onConnect: (
    slug: ConnectorSlug,
    permission: ConnectorAccessMode,
  ) => void;
  /**
   * Host completion signal. A catalog pick (`slug` matches the one being
   * authorized, or omitted) or a pending custom add resolves the OAuth spinner
   * so the modal advances (catalog → permission) or closes (custom).
   */
  readonly markConnected: (slug?: ConnectorSlug) => void;
}

function toMessage(error: unknown, fallback: string): string {
  if (error instanceof Error && error.message.length > 0) return error.message;
  if (typeof error === "string" && error.length > 0) return error;
  return fallback;
}

export function useConnectFlow(options: UseConnectFlowOptions): ConnectFlow {
  const { authorize, addCustomServer, onConnect } = options;

  const [open, setOpen] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Which slug the OAuth round-trip is authorizing, and whether a custom add is
  // in flight — refs so a host completion signal always sees the latest value
  // without re-rendering the caller.
  const connectingSlugRef = useRef<ConnectorSlug | null>(null);
  const customPendingRef = useRef(false);

  const reset = useCallback((): void => {
    connectingSlugRef.current = null;
    customPendingRef.current = false;
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
      customPendingRef.current = false;
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

  const onAddCustomServer = useCallback(
    (input: CustomServerInput): void => {
      if (addCustomServer === undefined) return;
      connectingSlugRef.current = null;
      customPendingRef.current = true;
      setError(null);
      setPending(true);
      addCustomServer(input)
        .then(async (result) => {
          if (result.authorizeUrl === undefined) {
            // Create alone completes the add — clear pending so the modal
            // closes (the custom flow has no permission step).
            customPendingRef.current = false;
            setPending(false);
            return;
          }
          // The server needs OAuth: hand the URL to the host. Completion still
          // lands via `markConnected`.
          await authorize({ url: result.authorizeUrl });
        })
        .catch((err: unknown) => {
          customPendingRef.current = false;
          setPending(false);
          setError(toMessage(err, "Could not add the custom server."));
        });
    },
    [addCustomServer, authorize],
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
    if (customPendingRef.current) {
      customPendingRef.current = false;
      setPending(false);
      setError(null);
      return;
    }
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
    onAddCustomServer:
      addCustomServer !== undefined ? onAddCustomServer : undefined,
    onConnect: handleConnect,
    markConnected,
  };
}
