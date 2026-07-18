import { app } from "electron";

export const DEEP_LINK_SCHEME = "enterprise";
export const OAUTH_CALLBACK_PATH = "oauth/callback";

export interface ParsedDeepLink {
  readonly url: string;
  readonly pathname: string;
  readonly searchParams: Readonly<Record<string, string>>;
}

export function parseDeepLink(rawUrl: string): ParsedDeepLink | null {
  let parsed: URL;
  try {
    parsed = new URL(rawUrl);
  } catch {
    return null;
  }
  if (parsed.protocol !== `${DEEP_LINK_SCHEME}:`) {
    return null;
  }
  const search: Record<string, string> = {};
  parsed.searchParams.forEach((value, key) => {
    search[key] = value;
  });
  return {
    url: rawUrl,
    pathname: pickPathname(parsed),
    searchParams: search,
  };
}

function pickPathname(parsed: URL): string {
  const raw = parsed.pathname || parsed.host || "";
  if (raw.startsWith("//")) return raw.slice(2);
  if (raw.startsWith("/")) {
    return parsed.host ? `${parsed.host}${raw}` : raw.slice(1);
  }
  return parsed.host && raw ? `${parsed.host}/${raw}` : raw;
}

export type OAuthCallbackHandler = (code: string, state: string) => void;

/**
 * Connector (AC9) demux hook. The single `enterprise://oauth/callback` scheme
 * is shared by app-login and every desktop connector flow, so a callback must
 * be routed by its unique 256-bit `state`. This handler is consulted FIRST for
 * every OAuth-callback deep link: it returns true iff a pending connector flow
 * owns the state (and consumes it); a false lets the callback fall through to
 * app-login (`onOAuthCallback`). Never routes tokens — only code + state.
 */
export type ConnectorCallbackRouter = (code: string, state: string) => boolean;

export interface DeepLinkRegistration {
  readonly unsubscribe: () => void;
}

export interface RegisterDeepLinksOptions {
  // OAuth deep-link path is the primary delivery channel for the OIDC
  // authorization code + state. The loopback HTTP server (Phase 5A) is the
  // fallback for environments that can't reliably register custom URL
  // schemes; whichever resolves first wins the race in oidc-client.
  readonly onOAuthCallback?: OAuthCallbackHandler;
  /**
   * AC9 — routed BEFORE `onOAuthCallback`. When it returns true the callback
   * belonged to a connector flow and app-login is NOT invoked; false means the
   * state is not a connector's, so the app-login handler runs.
   */
  readonly connectorCallbackRouter?: ConnectorCallbackRouter;
  readonly logger?: {
    info: (msg: string, ctx?: Record<string, unknown>) => void;
    warn: (msg: string, ctx?: Record<string, unknown>) => void;
  };
}

const defaultLogger = {
  info: (msg: string, ctx?: Record<string, unknown>) => {
    console.log(`[deep-links] ${msg}`, ctx ?? "");
  },
  warn: (msg: string, ctx?: Record<string, unknown>) => {
    console.warn(`[deep-links] ${msg}`, ctx ?? "");
  },
};

export function registerDeepLinks(
  options: RegisterDeepLinksOptions = {},
): DeepLinkRegistration {
  const logger = options.logger ?? defaultLogger;
  const onOAuthCallback = options.onOAuthCallback;
  const connectorCallbackRouter = options.connectorCallbackRouter;

  const protocolRegistered = app.setAsDefaultProtocolClient(DEEP_LINK_SCHEME);
  if (!protocolRegistered) {
    logger.warn(`failed to register ${DEEP_LINK_SCHEME}:// protocol`);
  }

  const dispatch = (
    source: "open-url" | "second-instance",
    rawUrl: string,
  ): void => {
    const parsed = parseDeepLink(rawUrl);
    if (parsed === null) {
      logger.warn(`ignored non-${DEEP_LINK_SCHEME} url`, { source, rawUrl });
      return;
    }
    if (isOAuthCallbackPath(parsed.pathname)) {
      const { code, state } = parsed.searchParams;
      const hasCode = typeof code === "string" && code.length > 0;
      const hasState = typeof state === "string" && state.length > 0;
      if (hasCode && hasState) {
        // AC9 demux: a connector flow that owns this state consumes the
        // callback first; only a non-connector state reaches app-login. The
        // 256-bit state is the sole discriminator — the query never carries a
        // flow tag we could spoof against.
        if (connectorCallbackRouter && connectorCallbackRouter(code, state)) {
          return;
        }
        if (onOAuthCallback) {
          onOAuthCallback(code, state);
          return;
        }
      }
      logger.warn("oauth callback missing code/state or no handler", {
        source,
        hasCode,
        hasState,
        hasConnectorRouter: Boolean(connectorCallbackRouter),
        hasLoginHandler: Boolean(onOAuthCallback),
      });
      return;
    }
    logger.info("deep link", {
      source,
      path: parsed.pathname,
      query: parsed.searchParams,
    });
  };

  const onOpenUrl = (event: Electron.Event, url: string): void => {
    event.preventDefault();
    dispatch("open-url", url);
  };

  const onSecondInstance = (
    _event: Electron.Event,
    argv: readonly string[],
  ): void => {
    const candidate = argv.find((arg) =>
      arg.startsWith(`${DEEP_LINK_SCHEME}://`),
    );
    if (candidate === undefined) return;
    dispatch("second-instance", candidate);
  };

  app.on("open-url", onOpenUrl);
  app.on("second-instance", onSecondInstance);

  return {
    unsubscribe: () => {
      app.off("open-url", onOpenUrl);
      app.off("second-instance", onSecondInstance);
    },
  };
}

function isOAuthCallbackPath(pathname: string): boolean {
  const normalized = pathname.replace(/^\/+/, "");
  return (
    normalized === OAUTH_CALLBACK_PATH ||
    normalized === `${OAUTH_CALLBACK_PATH}/`
  );
}
