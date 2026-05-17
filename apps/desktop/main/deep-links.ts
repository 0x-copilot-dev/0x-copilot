import { app } from "electron";

export const DEEP_LINK_SCHEME = "enterprise";

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
    pathname: parsed.pathname || parsed.host || "",
    searchParams: search,
  };
}

export interface DeepLinkRegistration {
  readonly unsubscribe: () => void;
}

// Phase 1 just registers the scheme and logs incoming URLs. Routing into
// the renderer happens at integration time with Agent 1-C (IPC bridge) +
// Agent 1-D (Router); on second-instance, Windows passes the URL as a
// process argv tail, while macOS dispatches 'open-url' on the app object.
export function registerDeepLinks(): DeepLinkRegistration {
  const protocolRegistered = app.setAsDefaultProtocolClient(DEEP_LINK_SCHEME);
  if (!protocolRegistered) {
    console.warn(
      `[deep-links] failed to register ${DEEP_LINK_SCHEME}:// protocol`,
    );
  }

  const onOpenUrl = (event: Electron.Event, url: string): void => {
    event.preventDefault();
    const parsed = parseDeepLink(url);
    if (parsed === null) {
      console.warn(`[deep-links] ignored non-${DEEP_LINK_SCHEME} url: ${url}`);
      return;
    }
    console.log(
      `[deep-links] (open-url) path=${parsed.pathname} query=`,
      parsed.searchParams,
    );
  };

  const onSecondInstance = (
    _event: Electron.Event,
    argv: readonly string[],
  ): void => {
    const candidate = argv.find((arg) =>
      arg.startsWith(`${DEEP_LINK_SCHEME}://`),
    );
    if (candidate === undefined) return;
    const parsed = parseDeepLink(candidate);
    if (parsed === null) {
      console.warn(
        `[deep-links] ignored non-${DEEP_LINK_SCHEME} second-instance arg: ${candidate}`,
      );
      return;
    }
    console.log(
      `[deep-links] (second-instance) path=${parsed.pathname} query=`,
      parsed.searchParams,
    );
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
