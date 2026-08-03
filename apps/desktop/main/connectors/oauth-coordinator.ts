// AC9 — desktop connector OAuth coordinator (Electron main).
//
// The missing piece between the system browser / OS callback delivery and the
// backend OAuth authority. It generalizes the login precedent
// (`auth/google-login.ts`) WITHOUT generalizing `openExternal` to the renderer:
// main owns the loopback binding and the browser open; the renderer only asks
// "connect this slug".
//
// Flow (per connect):
//   1. bind an ephemeral loopback (random port, fixed connector path)
//   2. POST {facade}/v1/connectors/{slug}/desktop/start-oauth with the
//      loopback callback  →  { oauth_session_id, authorization_url, state, … }
//   3. register `state` in the pending map + arm the loopback on it
//   4. open `authorization_url` in the SYSTEM browser
//   5. race loopback-delivered ?code&state against a deep-link delivery of the
//      SAME state (`enterprise://oauth/callback`); first valid state wins, the
//      other listener closes
//   6. POST {facade}/v1/connectors/desktop/oauth-callback with ONLY
//      { oauth_session_id, state, code } (never a token)  →  safe metadata
//
// Demultiplexing: many flows can be in-flight (a second connector, or app
// login on its own loopback). A deep-link callback is routed by its unique
// 256-bit `state`: `handleDeepLinkCallback` resolves ONLY the pending session
// whose state matches and returns whether it owned it — so an app-login state
// (or an unknown one) falls through untouched. Provider tokens never cross into
// main: the callback response carries only safe connection metadata.

import type {
  DesktopConnectorConnectionResult,
  DesktopConnectorOAuthCallbackRequest,
  DesktopRequestedProductScope,
  DesktopStartConnectorOAuthRequest,
  DesktopStartConnectorOAuthResponse,
  McpOAuthClientConfigRequest,
} from "@0x-copilot/api-types";
import {
  DESKTOP_CONNECTOR_DEEP_LINK_URI,
  DESKTOP_CONNECTOR_LOOPBACK_PATH,
} from "@0x-copilot/api-types";

import {
  awaitLoopbackCode,
  type LoopbackHandle,
} from "../auth/loopback-server";

const DEFAULT_TIMEOUT_MS = 5 * 60 * 1000;

/** Per-attempt inputs to a connect. Both optional — the common case sends
 *  neither, and a connector whose OAuth discovery works never needs a client. */
export interface ConnectorConnectOptions {
  readonly productScope?: DesktopRequestedProductScope;
  /**
   * Which redirect the provider was registered against. `"loopback"` (default)
   * suits providers that accept an arbitrary loopback port; `"deep_link"` is
   * required by providers that demand one exact, pre-registered callback URL.
   * The profile's `callback_modes` declares what the backend will accept.
   */
  readonly callbackMode?: "loopback" | "deep_link";
  /**
   * Pre-registered OAuth client, supplied by the user after a connect failed
   * with `connector_oauth_client_required`. Held only for the duration of the
   * request; the backend encrypts the secret into the TokenVault, so a later
   * connect for the same server can omit it.
   */
  readonly oauthClient?: McpOAuthClientConfigRequest;
  /**
   * Invoked with a cancel function as soon as the loopback is listening — the
   * same shape the login flows use (`auth/google-login.ts`). Calling it aborts
   * the pending connect: it rejects the delivery promise and frees the port.
   *
   * Handed out BEFORE `start-oauth` returns on purpose. That POST took over a
   * second against a real provider, and a Cancel button that is inert for the
   * first second of a spinner is the kind of "nothing happens" this whole
   * change is about.
   */
  readonly onCancelAvailable?: (cancel: () => void) => void;
}

export class ConnectorOAuthError extends Error {
  readonly stage: "start" | "redirect" | "callback";

  constructor(stage: ConnectorOAuthError["stage"], message: string) {
    super(message);
    this.name = "ConnectorOAuthError";
    this.stage = stage;
  }
}

/**
 * Why a cancelled connect rejects. Only an Error MESSAGE survives the IPC hop,
 * so the string is the contract — but the renderer does not parse it: the side
 * that pressed Cancel already knows, and treats the rejection quietly (the same
 * shape `SignInGate` uses). This exists so a cancel reads as a cancel in logs
 * rather than as a mysterious redirect failure.
 */
export const CONNECT_CANCELLED = "connect cancelled";

/**
 * Why an unreachable facade must fail rather than spin.
 *
 * `timeoutMs` used to bound only the loopback — the step that CANNOT hang,
 * because it is a local listener we own. The facade calls before and after it
 * had no deadline at all, so a facade that accepted the connection and never
 * answered left `authorize` awaiting forever: no browser opened, no error
 * surfaced, and the row sat on "Connecting…" until the user pressed Cancel.
 * That is why this shipped looking like a cancellation — the only escape was
 * the cancel button, so `connect cancelled` is what the logs recorded.
 */
export const FACADE_UNREACHABLE =
  "the app could not reach its backend to start the connection";

/** Bounds every facade call in this file. Generous: a slow start is not a hang. */
export const FACADE_TIMEOUT_MS = 20_000;

export interface ConnectorOAuthDeps {
  readonly facadeBaseUrl: string;
  /** Opens a URL in the OS default browser (never exposed to the renderer). */
  readonly openExternal: (url: string) => Promise<void>;
  /** Resolves the current bearer for the active session, or null if signed out. */
  readonly getBearer: () => Promise<string | null>;
  readonly fetch?: typeof fetch;
  readonly loopback?: typeof awaitLoopbackCode;
  readonly timeoutMs?: number;
  /** Deadline for facade calls. Injectable for the same reason `timeoutMs` is:
   *  a test must be able to drive the expiry without a real wall-clock wait,
   *  and `AbortSignal.timeout` is not intercepted by fake timers. */
  readonly facadeTimeoutMs?: number;
  readonly logger?: {
    info: (msg: string, ctx?: Record<string, unknown>) => void;
    warn: (msg: string, ctx?: Record<string, unknown>) => void;
  };
}

interface PendingSession {
  readonly slug: string;
  readonly resolve: (received: { code: string; state: string }) => void;
}

const defaultLogger = {
  info: (msg: string, ctx?: Record<string, unknown>) => {
    console.log(`[connector-oauth] ${msg}`, ctx ?? "");
  },
  warn: (msg: string, ctx?: Record<string, unknown>) => {
    console.warn(`[connector-oauth] ${msg}`, ctx ?? "");
  },
};

export class ConnectorOAuthCoordinator {
  private readonly facadeBaseUrl: string;
  private readonly openExternal: (url: string) => Promise<void>;
  private readonly getBearer: () => Promise<string | null>;
  private readonly doFetch: typeof fetch;
  private readonly loopback: typeof awaitLoopbackCode;
  private readonly timeoutMs: number;
  private readonly facadeTimeoutMs: number;
  private readonly logger: NonNullable<ConnectorOAuthDeps["logger"]>;

  // state (256-bit, minted server-side) → pending session. The single source
  // of truth for which in-flight flow owns a given OAuth callback.
  private readonly pending = new Map<string, PendingSession>();

  /**
   * Fetch with a deadline, translating an expiry into a typed `start` failure.
   *
   * `AbortSignal.timeout` rejects with an `AbortError`; surfaced raw it reads as
   * a mystery. Naming it here is what makes an unhealthy backend diagnosable
   * from the toast alone rather than from a spinner that never resolves.
   */
  private async fetchWithDeadline(
    url: string,
    init: RequestInit,
    stage: ConnectorOAuthError["stage"],
  ): Promise<Response> {
    try {
      return await this.doFetch(url, {
        ...init,
        signal: AbortSignal.timeout(this.facadeTimeoutMs),
      });
    } catch (error) {
      const aborted =
        error instanceof Error &&
        (error.name === "AbortError" || error.name === "TimeoutError");
      if (aborted) {
        throw new ConnectorOAuthError(stage, FACADE_UNREACHABLE);
      }
      throw error;
    }
  }

  constructor(deps: ConnectorOAuthDeps) {
    this.facadeBaseUrl = trimTrailingSlash(deps.facadeBaseUrl);
    this.openExternal = deps.openExternal;
    this.getBearer = deps.getBearer;
    this.doFetch = deps.fetch ?? globalThis.fetch.bind(globalThis);
    this.loopback = deps.loopback ?? awaitLoopbackCode;
    this.timeoutMs = deps.timeoutMs ?? DEFAULT_TIMEOUT_MS;
    this.facadeTimeoutMs = deps.facadeTimeoutMs ?? FACADE_TIMEOUT_MS;
    this.logger = deps.logger ?? defaultLogger;
  }

  /**
   * Route a deep-link OAuth callback to the connector flow that owns `state`.
   * Returns true iff a connector session matched (and was resolved) — the
   * caller (deep-link dispatcher) treats a false as "not a connector callback"
   * and lets app-login handle it. Never throws; an unknown state is a no-op.
   */
  handleDeepLinkCallback(code: string, state: string): boolean {
    const session = this.pending.get(state);
    if (session === undefined) return false;
    this.logger.info("deep-link callback matched connector session", {
      slug: session.slug,
    });
    session.resolve({ code, state });
    return true;
  }

  /** True iff a connector flow is currently awaiting the given state. */
  ownsState(state: string): boolean {
    return this.pending.has(state);
  }

  /**
   * Begin (and complete) the connect flow for a stable slug. Resolves with
   * SAFE connection metadata; the provider token stays server-side in the
   * backend TokenVault and never enters this process.
   */
  async connect(
    slug: string,
    options: ConnectorConnectOptions = {},
  ): Promise<DesktopConnectorConnectionResult> {
    const bearer = await this.getBearer();
    if (bearer === null) {
      throw new ConnectorOAuthError("start", "not signed in");
    }

    // Deep-link mode registers the FIXED `enterprise://oauth/callback` as the
    // redirect; loopback registers `http://127.0.0.1:<random>/…`.
    //
    // The random port is the reason this choice has to exist. RFC 8252 lets a
    // native app vary the loopback port, and Google honours that — but a
    // provider that demands an exact, pre-registered callback URL (Atlassian
    // 3LO among them) rejects a port it has never seen, and no amount of
    // supplying a client_id fixes it. The deep-link URI was already accepted by
    // the backend and already demuxed here; it was simply never REQUESTED, so
    // that whole class of provider was unreachable.
    const useDeepLink = options.callbackMode === "deep_link";
    const handle: LoopbackHandle | null = useDeepLink
      ? null
      : await this.loopback({
          callbackPath: DESKTOP_CONNECTOR_LOOPBACK_PATH,
          timeoutMs: this.timeoutMs,
          randomPorts: {},
        });

    // Same cancel contract as `connectMcpServer`. Deep-link mode has no
    // loopback to close, so rejecting the delivery promise is the ONLY way to
    // abort it — which is why cancel rejects rather than just closing a port.
    let cancelled = false;
    let rejectDelivery: (error: Error) => void = () => {};
    options.onCancelAvailable?.(() => {
      cancelled = true;
      rejectDelivery(new ConnectorOAuthError("redirect", CONNECT_CANCELLED));
      handle?.close();
    });

    let registeredState: string | null = null;
    try {
      // -- 1. ask the facade to start the flow (backend mints state+PKCE) ----
      const start = await this.startOAuth(
        slug,
        handle?.port ?? null,
        bearer,
        options,
      );

      // -- 2. arm loopback + register the state for deep-link demux -----------
      const received = new Promise<{ code: string; state: string }>(
        (resolve, reject) => {
          rejectDelivery = reject;
          this.pending.set(start.state, { slug, resolve });
        },
      );
      registeredState = start.state;
      handle?.armState(start.state);

      // -- 3. system browser round-trip --------------------------------------
      // Never open a consent screen for a flow the user already cancelled —
      // approving in that tab would complete an authorization they stopped.
      if (cancelled) {
        throw new ConnectorOAuthError("redirect", CONNECT_CANCELLED);
      }
      await this.openExternal(start.authorization_url);

      // -- 4. first valid state wins: loopback OR deep-link delivery ----------
      let delivered: { code: string; state: string };
      try {
        // In deep-link mode there is no loopback to race — the app's own
        // protocol handler is the only delivery path.
        delivered =
          handle === null
            ? await received
            : await Promise.race([received, handle.codePromise]);
      } catch (err) {
        throw new ConnectorOAuthError(
          "redirect",
          err instanceof Error ? err.message : String(err),
        );
      }
      if (delivered.state !== start.state) {
        throw new ConnectorOAuthError("redirect", "oauth state mismatch");
      }

      // -- 5. complete: post ONLY code+state, receive safe metadata ----------
      return await this.completeOAuth(start, delivered.code, bearer);
    } finally {
      if (registeredState !== null) this.pending.delete(registeredState);
      handle?.close();
    }
  }

  private async startOAuth(
    slug: string,
    port: number | null,
    bearer: string,
    options: ConnectorConnectOptions,
  ): Promise<DesktopStartConnectorOAuthResponse> {
    const body: DesktopStartConnectorOAuthRequest = {
      callback:
        port === null
          ? {
              kind: "desktop_deep_link",
              uri: DESKTOP_CONNECTOR_DEEP_LINK_URI,
            }
          : {
              kind: "desktop_loopback",
              port,
              path: DESKTOP_CONNECTOR_LOOPBACK_PATH,
            },
      requested_product_scope: options.productScope ?? "read",
      // Only sent when the user has actually supplied one. The secret transits
      // this process in memory and is never written to disk here — the backend
      // encrypts it into the TokenVault on receipt.
      ...(options.oauthClient !== undefined
        ? { oauth_client: options.oauthClient }
        : {}),
    };
    const response = await this.fetchWithDeadline(
      `${this.facadeBaseUrl}/v1/connectors/${encodeURIComponent(slug)}/desktop/start-oauth`,
      {
        method: "POST",
        headers: {
          "content-type": "application/json",
          accept: "application/json",
          authorization: `Bearer ${bearer}`,
        },
        body: JSON.stringify(body),
      },
      "start",
    );
    if (!response.ok) {
      throw new ConnectorOAuthError(
        "start",
        `start-oauth failed: ${response.status} ${await safeText(response)}`,
      );
    }
    const start = (await response.json()) as DesktopStartConnectorOAuthResponse;
    if (!start.authorization_url || !start.state) {
      throw new ConnectorOAuthError(
        "start",
        "start-oauth returned no url/state",
      );
    }
    return start;
  }

  private async completeOAuth(
    start: DesktopStartConnectorOAuthResponse,
    code: string,
    bearer: string,
  ): Promise<DesktopConnectorConnectionResult> {
    const body: DesktopConnectorOAuthCallbackRequest = {
      oauth_session_id: start.oauth_session_id,
      state: start.state,
      code,
    };
    const response = await this.fetchWithDeadline(
      `${this.facadeBaseUrl}/v1/connectors/desktop/oauth-callback`,
      {
        method: "POST",
        headers: {
          "content-type": "application/json",
          accept: "application/json",
          authorization: `Bearer ${bearer}`,
        },
        body: JSON.stringify(body),
      },
      "callback",
    );
    if (!response.ok) {
      throw new ConnectorOAuthError(
        "callback",
        `oauth-callback failed: ${response.status} ${await safeText(response)}`,
      );
    }
    return (await response.json()) as DesktopConnectorConnectionResult;
  }

  // -- MCP-server OAuth (seeds + custom servers) ------------------------------
  //
  // `connect()` above resolves a slug against `desktop_profiles.yaml`, which
  // knows four connectors. Everything else the Tools popover lists comes from
  // `mcp_catalog.DEFAULT_CATALOG` and has no profile, so routing those through
  // `connect()` returns 404 connector_profile_unavailable — a dead button.
  //
  // Those servers authenticate through the MCP OAuth routes instead, which
  // support discovery + dynamic client registration and therefore need no
  // pre-registered client. The shape is the same as above (loopback → browser →
  // callback); only the endpoints differ:
  //
  //   POST {facade}/v1/mcp/servers/{id}/auth/start  { redirect_uri }  → auth_url
  //   GET  {facade}/v1/mcp/oauth/callback?state&code                  → server
  //
  // `McpAuthStartResponse` carries no `state` field — the state is minted into
  // the `auth_url` query, so it is read back out of the URL to arm the loopback
  // and the deep-link demux. `redirect_uri` is client-supplied and validated
  // server-side with `allow_localhost=True`, which is what makes the desktop
  // loopback a first-class callback rather than a workaround.
  async connectMcpServer(
    serverId: string,
    options: { readonly onCancelAvailable?: (cancel: () => void) => void } = {},
  ): Promise<void> {
    const bearer = await this.getBearer();
    if (bearer === null) {
      throw new ConnectorOAuthError("start", "not signed in");
    }

    const handle: LoopbackHandle = await this.loopback({
      callbackPath: DESKTOP_CONNECTOR_LOOPBACK_PATH,
      timeoutMs: this.timeoutMs,
      randomPorts: {},
    });

    // Cancellation is armed the moment the port exists — before `auth/start`,
    // which is a real network round-trip. `rejectDelivery` starts as a no-op
    // and is replaced once the delivery promise exists, so a cancel landing in
    // that window still closes the loopback and still sets `cancelled`, which
    // is what stops the browser from being opened at all.
    let cancelled = false;
    let rejectDelivery: (error: Error) => void = () => {};
    options.onCancelAvailable?.(() => {
      cancelled = true;
      rejectDelivery(new ConnectorOAuthError("redirect", CONNECT_CANCELLED));
      handle.close();
    });

    let registeredState: string | null = null;
    try {
      const start = await this.startMcpAuth(
        serverId,
        handle.redirectUri,
        bearer,
      );
      const state = extractOAuthState(start.auth_url);

      const received = new Promise<{ code: string; state: string }>(
        (resolve, reject) => {
          rejectDelivery = reject;
          this.pending.set(state, { slug: serverId, resolve });
        },
      );
      registeredState = state;
      handle.armState(state);

      // Opening a consent screen for a flow the user already cancelled is worse
      // than doing nothing: the tab is live, so approving in it would complete
      // an authorization they explicitly stopped.
      if (cancelled) {
        throw new ConnectorOAuthError("redirect", CONNECT_CANCELLED);
      }
      await this.openExternal(start.auth_url);

      let delivered: { code: string; state: string };
      try {
        delivered = await Promise.race([received, handle.codePromise]);
      } catch (err) {
        throw new ConnectorOAuthError(
          "redirect",
          err instanceof Error ? err.message : String(err),
        );
      }
      if (delivered.state !== state) {
        throw new ConnectorOAuthError("redirect", "oauth state mismatch");
      }

      await this.completeMcpAuth(delivered.state, delivered.code, bearer);
    } finally {
      if (registeredState !== null) this.pending.delete(registeredState);
      handle.close();
    }
  }

  private async startMcpAuth(
    serverId: string,
    redirectUri: string,
    bearer: string,
  ): Promise<{ readonly auth_url: string }> {
    const response = await this.fetchWithDeadline(
      `${this.facadeBaseUrl}/v1/mcp/servers/${encodeURIComponent(serverId)}/auth/start`,
      {
        method: "POST",
        headers: {
          "content-type": "application/json",
          accept: "application/json",
          authorization: `Bearer ${bearer}`,
        },
        // Identity is server-derived from the bearer; only the callback is ours.
        body: JSON.stringify({ redirect_uri: redirectUri }),
      },
      "start",
    );
    if (!response.ok) {
      throw new ConnectorOAuthError(
        "start",
        `mcp auth/start failed: ${response.status} ${await safeText(response)}`,
      );
    }
    const start = (await response.json()) as { auth_url?: string };
    if (!start.auth_url) {
      throw new ConnectorOAuthError("start", "mcp auth/start returned no url");
    }
    return { auth_url: start.auth_url };
  }

  /** Hand the provider's code back.
   *
   *  The BACKEND's callback is a public route — `state` is its trust anchor,
   *  because a provider can redirect a browser straight to it with no session.
   *  The FACADE's is not: it runs `FacadeAuthenticator.authenticate_request`
   *  and answers 401 "Missing bearer token" without one. Desktop always reaches
   *  the facade, and the redirect lands on our own loopback rather than in a
   *  browser tab, so this call is ours to make and is authenticated as the user
   *  — strictly better than relying on `state` alone. */
  private async completeMcpAuth(
    state: string,
    code: string,
    bearer: string,
  ): Promise<void> {
    const url = new URL(`${this.facadeBaseUrl}/v1/mcp/oauth/callback`);
    url.searchParams.set("state", state);
    url.searchParams.set("code", code);
    const response = await this.doFetch(url.toString(), {
      method: "GET",
      headers: {
        accept: "application/json",
        authorization: `Bearer ${bearer}`,
      },
    });
    if (!response.ok) {
      throw new ConnectorOAuthError(
        "callback",
        `mcp oauth/callback failed: ${response.status} ${await safeText(response)}`,
      );
    }
  }
}

/** Read the `state` the backend minted into the authorization URL. */
function extractOAuthState(authUrl: string): string {
  let parsed: URL;
  try {
    parsed = new URL(authUrl);
  } catch {
    throw new ConnectorOAuthError("start", "auth_url is not a valid URL");
  }
  const state = parsed.searchParams.get("state");
  if (state === null || state.length === 0) {
    throw new ConnectorOAuthError("start", "auth_url carries no state");
  }
  return state;
}

function trimTrailingSlash(url: string): string {
  return url.endsWith("/") ? url.slice(0, -1) : url;
}

async function safeText(response: Response): Promise<string> {
  try {
    return await response.text();
  } catch {
    return "";
  }
}
