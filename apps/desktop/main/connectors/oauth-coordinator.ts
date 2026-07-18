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
} from "@0x-copilot/api-types";
import { DESKTOP_CONNECTOR_LOOPBACK_PATH } from "@0x-copilot/api-types";

import {
  awaitLoopbackCode,
  type LoopbackHandle,
} from "../auth/loopback-server";

const DEFAULT_TIMEOUT_MS = 5 * 60 * 1000;

export class ConnectorOAuthError extends Error {
  readonly stage: "start" | "redirect" | "callback";

  constructor(stage: ConnectorOAuthError["stage"], message: string) {
    super(message);
    this.name = "ConnectorOAuthError";
    this.stage = stage;
  }
}

export interface ConnectorOAuthDeps {
  readonly facadeBaseUrl: string;
  /** Opens a URL in the OS default browser (never exposed to the renderer). */
  readonly openExternal: (url: string) => Promise<void>;
  /** Resolves the current bearer for the active session, or null if signed out. */
  readonly getBearer: () => Promise<string | null>;
  readonly fetch?: typeof fetch;
  readonly loopback?: typeof awaitLoopbackCode;
  readonly timeoutMs?: number;
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
  private readonly logger: NonNullable<ConnectorOAuthDeps["logger"]>;

  // state (256-bit, minted server-side) → pending session. The single source
  // of truth for which in-flight flow owns a given OAuth callback.
  private readonly pending = new Map<string, PendingSession>();

  constructor(deps: ConnectorOAuthDeps) {
    this.facadeBaseUrl = trimTrailingSlash(deps.facadeBaseUrl);
    this.openExternal = deps.openExternal;
    this.getBearer = deps.getBearer;
    this.doFetch = deps.fetch ?? globalThis.fetch.bind(globalThis);
    this.loopback = deps.loopback ?? awaitLoopbackCode;
    this.timeoutMs = deps.timeoutMs ?? DEFAULT_TIMEOUT_MS;
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
    options: { readonly productScope?: DesktopRequestedProductScope } = {},
  ): Promise<DesktopConnectorConnectionResult> {
    const bearer = await this.getBearer();
    if (bearer === null) {
      throw new ConnectorOAuthError("start", "not signed in");
    }

    const handle: LoopbackHandle = await this.loopback({
      callbackPath: DESKTOP_CONNECTOR_LOOPBACK_PATH,
      timeoutMs: this.timeoutMs,
      randomPorts: {},
    });

    let registeredState: string | null = null;
    try {
      // -- 1. ask the facade to start the flow (backend mints state+PKCE) ----
      const start = await this.startOAuth(slug, handle.port, bearer, options);

      // -- 2. arm loopback + register the state for deep-link demux -----------
      const received = new Promise<{ code: string; state: string }>(
        (resolve) => {
          this.pending.set(start.state, { slug, resolve });
        },
      );
      registeredState = start.state;
      handle.armState(start.state);

      // -- 3. system browser round-trip --------------------------------------
      await this.openExternal(start.authorization_url);

      // -- 4. first valid state wins: loopback OR deep-link delivery ----------
      let delivered: { code: string; state: string };
      try {
        delivered = await Promise.race([received, handle.codePromise]);
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
      handle.close();
    }
  }

  private async startOAuth(
    slug: string,
    port: number,
    bearer: string,
    options: { readonly productScope?: DesktopRequestedProductScope },
  ): Promise<DesktopStartConnectorOAuthResponse> {
    const body: DesktopStartConnectorOAuthRequest = {
      callback: {
        kind: "desktop_loopback",
        port,
        path: DESKTOP_CONNECTOR_LOOPBACK_PATH,
      },
      requested_product_scope: options.productScope ?? "read",
    };
    const response = await this.doFetch(
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
    const response = await this.doFetch(
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
    );
    if (!response.ok) {
      throw new ConnectorOAuthError(
        "callback",
        `oauth-callback failed: ${response.status} ${await safeText(response)}`,
      );
    }
    return (await response.json()) as DesktopConnectorConnectionResult;
  }
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
