import {
  WebTransport,
  type Transport,
} from "@enterprise-search/chat-transport";

// The frontend keeps a single WebTransport for the lifetime of the page.
// Every HTTP call — through the http.ts helpers, through api modules that
// do raw fetch with correlationHeaders, or through OTel's fetch
// instrumentation — reads bearer and 401-handling from this module.
//
// Why a module singleton and not a React context: HTTP plumbing has to be
// callable from non-component code (api modules, OTel hooks, error
// boundaries) where useContext is not available. AuthContext configures the
// singleton on mount via setAuthBearerProvider / setUnauthorizedHandler.

let _bearerProvider: () => string | null = () => null;
let _unauthorizedHandler: (response: Response) => void = () => {};

const _appTransport: Transport = new WebTransport({
  bearerProvider: () => _bearerProvider(),
  onUnauthorized: (response) => _unauthorizedHandler(response),
});

export function getAppTransport(): Transport {
  return _appTransport;
}

export function getAuthBearer(): string | null {
  return _bearerProvider();
}

export function notifyUnauthorized(response: Response): void {
  // Handler errors must not mask the original 401 — swallowed at the
  // single notification site so every 401 path (transport.request and
  // raw-fetch assertOk) shares the same safety contract.
  try {
    _unauthorizedHandler(response);
  } catch {
    /* intentional */
  }
}

export function setAuthBearerProvider(
  provider: (() => string | null) | null,
): void {
  _bearerProvider = provider ?? (() => null);
}

export function setUnauthorizedHandler(
  handler: ((response: Response) => void) | null,
): void {
  _unauthorizedHandler = handler ?? (() => {});
}
