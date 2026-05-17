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

export interface DeepLinkRegistration {
  readonly unsubscribe: () => void;
}

export interface RegisterDeepLinksOptions {
  // OAuth deep-link path is the primary delivery channel for the OIDC
  // authorization code + state. The loopback HTTP server (Phase 5A) is the
  // fallback for environments that can't reliably register custom URL
  // schemes; whichever resolves first wins the race in oidc-client.
  readonly onOAuthCallback?: OAuthCallbackHandler;
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
      if (
        onOAuthCallback &&
        typeof code === "string" &&
        code.length > 0 &&
        typeof state === "string" &&
        state.length > 0
      ) {
        onOAuthCallback(code, state);
        return;
      }
      logger.warn("oauth callback missing code/state", {
        source,
        hasCode: typeof code === "string" && code.length > 0,
        hasState: typeof state === "string" && state.length > 0,
        hasHandler: Boolean(onOAuthCallback),
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
