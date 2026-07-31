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

import type {
  ConnectorAccessMode,
  ConnectorSlug,
  McpOAuthClientConfigRequest,
} from "@0x-copilot/api-types";

/** Where to open the authorization surface for a connect step. */
export interface ConnectAuthorizeRequest {
  /** A catalog pick — the slug the host authorizes (desktop: IPC connect). */
  readonly slug?: ConnectorSlug;
  /**
   * A pre-registered OAuth client the user supplied after this connector
   * reported `connector_oauth_client_required`. Present only on the retry.
   */
  readonly oauthClient?: McpOAuthClientConfigRequest;
  /**
   * Which redirect the user's OAuth app was registered against. Only meaningful
   * alongside `oauthClient` — it describes THAT registration, not a preference.
   */
  readonly callbackMode?: ConnectCallbackMode;
}

/**
 * Redirect styles a desktop connect can use. `loopback` varies its port per
 * attempt (RFC 8252); `deep_link` is one fixed URI. A provider that demands an
 * exact pre-registered callback only works with the latter.
 */
export type ConnectCallbackMode = "loopback" | "deep_link";

/**
 * Thrown by a host's `authorize` when the connector cannot proceed without a
 * pre-registered OAuth client. It is a distinct type rather than a magic
 * message string because the flow has to BRANCH on it — the difference between
 * "this failed" and "this failed and here is the one thing that fixes it" is
 * the difference between a dead end and a form.
 */
export class ConnectOAuthClientRequiredError extends Error {
  readonly slug: ConnectorSlug;

  constructor(slug: ConnectorSlug, message?: string) {
    super(message ?? "This connector needs a pre-registered OAuth client.");
    this.name = "ConnectOAuthClientRequiredError";
    this.slug = slug;
  }
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
  /**
   * Which slug is mid-authorization, or `null`. `pending` alone cannot answer
   * this, and the surface's catalog list needs it to put "Connecting…" on the
   * ONE row the user clicked rather than on all of them. Mirrors
   * `connectingSlugRef` as render state — the ref stays, because the host's
   * completion signal must read the latest value without waiting for a render.
   */
  readonly connectingSlug: ConnectorSlug | null;
  /**
   * The slug whose connect stopped because it needs a pre-registered OAuth
   * client, or `null`. The modal renders its client form off this.
   */
  readonly clientRequiredSlug: ConnectorSlug | null;
  /** Retry the blocked connect with the client the user just supplied. */
  readonly submitOAuthClient: (
    client: McpOAuthClientConfigRequest,
    callbackMode?: ConnectCallbackMode,
  ) => void;
  /** Open the modal (the "Connect a tool" CTA). */
  readonly openConnect: () => void;
  /**
   * Connect ONE catalog entry from the surface — opens the same modal already
   * picked on that entry, so the row and the CTA run one flow rather than two.
   *
   * The row used to call `onSelectEntry` directly, which started the OAuth
   * round-trip with the modal closed. That worked, but it silently skipped the
   * modal's access-mode step: a row connect landed read-only while a CTA
   * connect asked the user to choose. Same outcome, two ceremonies, and the
   * more discoverable button was the one that asked less.
   */
  readonly connectEntry: (slug: ConnectorSlug) => void;
  /**
   * Entry the modal should pick as soon as it opens (set by `connectEntry`).
   * `null` when the modal was opened by the CTA and should show the catalog.
   */
  readonly initialEntrySlug: ConnectorSlug | null;
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
  // Render mirror of `connectingSlugRef` (see ConnectFlow.connectingSlug).
  const [connectingSlug, setConnectingSlug] = useState<ConnectorSlug | null>(
    null,
  );
  const [clientRequiredSlug, setClientRequiredSlug] =
    useState<ConnectorSlug | null>(null);
  const [initialEntrySlug, setInitialEntrySlug] =
    useState<ConnectorSlug | null>(null);

  // Which slug the OAuth round-trip is authorizing — a ref so a host completion
  // signal always sees the latest value without re-rendering the caller.
  const connectingSlugRef = useRef<ConnectorSlug | null>(null);

  const reset = useCallback((): void => {
    connectingSlugRef.current = null;
    setPending(false);
    setError(null);
    setConnectingSlug(null);
    setClientRequiredSlug(null);
  }, []);

  const openConnect = useCallback((): void => {
    reset();
    setInitialEntrySlug(null);
    setOpen(true);
  }, [reset]);

  // Deliberately does NOT authorize here. It opens the modal pointed at the
  // entry, and the modal's own pick fires `onSelectEntry` — so there is exactly
  // one code path from "an entry was chosen" onwards, whichever control
  // started it.
  const connectEntry = useCallback(
    (slug: ConnectorSlug): void => {
      reset();
      setInitialEntrySlug(slug);
      setOpen(true);
    },
    [reset],
  );

  const closeConnect = useCallback((): void => {
    reset();
    setInitialEntrySlug(null);
    setOpen(false);
  }, [reset]);

  const attempt = useCallback(
    (
      slug: ConnectorSlug,
      oauthClient?: McpOAuthClientConfigRequest,
      callbackMode?: ConnectCallbackMode,
    ): void => {
      connectingSlugRef.current = slug;
      setError(null);
      setPending(true);
      setConnectingSlug(slug);
      setClientRequiredSlug(null);
      authorize({
        slug,
        ...(oauthClient !== undefined ? { oauthClient } : {}),
        ...(callbackMode !== undefined ? { callbackMode } : {}),
      }).catch((err: unknown) => {
        connectingSlugRef.current = null;
        setPending(false);
        setConnectingSlug(null);
        // A missing client is not a failure to report and forget — it names
        // the one input that unblocks this connector, so the flow holds the
        // slug and the modal asks for it instead of showing a dead error.
        if (err instanceof ConnectOAuthClientRequiredError) {
          setClientRequiredSlug(slug);
          setError(null);
          return;
        }
        setError(toMessage(err, "Could not start the OAuth flow."));
      });
    },
    [authorize],
  );

  const onSelectEntry = useCallback(
    (slug: ConnectorSlug): void => {
      attempt(slug);
    },
    [attempt],
  );

  const submitOAuthClient = useCallback(
    (
      client: McpOAuthClientConfigRequest,
      callbackMode?: ConnectCallbackMode,
    ): void => {
      const slug = clientRequiredSlug;
      if (slug === null) return;
      attempt(slug, client, callbackMode);
    },
    [attempt, clientRequiredSlug],
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
    setConnectingSlug(null);
    setError(null);
  }, []);

  return {
    open,
    pending,
    error,
    connectingSlug,
    clientRequiredSlug,
    submitOAuthClient,
    openConnect,
    connectEntry,
    initialEntrySlug,
    closeConnect,
    onSelectEntry,
    onConnect: handleConnect,
    markConnected,
  };
}
