import type { RequestIdentity } from "./config";
import { identityParams } from "./config";

const REQUEST_ID_HEADER = "x-request-id";
const AUTHORIZATION_HEADER = "authorization";

// AuthContext registers a callback so a 401 anywhere in the API surface
// flows back to "anonymous" + login redirect — without prop-threading
// the auth context through every API helper. Defaults to a no-op so
// tests and pre-AuthContext callers don't blow up.
type UnauthorizedHandler = (response: Response) => void;
let _onUnauthorized: UnauthorizedHandler = () => {};

export function configureUnauthorizedHandler(
  handler: UnauthorizedHandler | null,
): void {
  _onUnauthorized = handler ?? (() => {});
}

// Bearer plumbing lives at the HTTP layer (not in authApi) so every API
// helper attaches `Authorization: Bearer …` automatically when a session
// is active. Previously this was private to authApi.ts and most modules
// shipped requests with no bearer at all — the facade tolerated that
// while DEV_AUTH_BYPASS existed (W0.1 removed it). AuthProvider wires
// this up once on mount via configureAuthBearerProvider.
type BearerProvider = () => string | null;
let _bearerProvider: BearerProvider = () => null;

export function configureAuthBearerProvider(provider: BearerProvider): void {
  _bearerProvider = provider;
}

export class UnauthorizedError extends Error {
  readonly status = 401;

  constructor(detail?: string) {
    super(detail || "Request failed with 401");
    this.name = "UnauthorizedError";
  }
}

export async function assertOk(response: Response): Promise<void> {
  if (response.ok) {
    return;
  }
  const detail = await response.text();
  if (response.status === 401) {
    // Notify AuthContext (or any registered handler) before throwing so
    // the caller's catch can still surface a useful message — the
    // notification is fire-and-forget.
    try {
      _onUnauthorized(response);
    } catch {
      /* handler errors must not mask the original 401 */
    }
    throw new UnauthorizedError(detail);
  }
  throw new Error(detail || `Request failed with ${response.status}`);
}

export function newRequestId(): string {
  const random =
    typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
      ? crypto.randomUUID().replace(/-/g, "")
      : Math.random().toString(16).slice(2).padEnd(32, "0");
  return `req_${random}`;
}

// Default headers attached to every same-origin /v1/* request: a fresh
// request-id for tracing plus the bearer when a session is active.
// Public endpoints (e.g. /v1/auth/discover) ignore the bearer; protected
// endpoints reject the call without it. Consumers should not branch on
// auth state — just call this and let the bearer ride along when it
// exists.
export function correlationHeaders(): Record<string, string> {
  const headers: Record<string, string> = {
    [REQUEST_ID_HEADER]: newRequestId(),
  };
  const bearer = _bearerProvider();
  if (bearer) {
    headers[AUTHORIZATION_HEADER] = `Bearer ${bearer}`;
  }
  return headers;
}

export function jsonHeaders(): HeadersInit {
  return { "content-type": "application/json", ...correlationHeaders() };
}

async function assertOkJson<T>(response: Response): Promise<T> {
  await assertOk(response);
  return (await response.json()) as T;
}

function buildQuery(
  identity: RequestIdentity | null,
  extra: Record<string, string | undefined> | undefined,
): string {
  const params = identity ? identityParams(identity) : new URLSearchParams();
  if (extra) {
    for (const [key, value] of Object.entries(extra)) {
      if (value !== undefined) {
        params.set(key, value);
      }
    }
  }
  const query = params.toString();
  return query ? `?${query}` : "";
}

export async function httpGet<T>(
  path: string,
  identity: RequestIdentity,
  extra?: Record<string, string | undefined>,
): Promise<T> {
  const response = await fetch(`${path}${buildQuery(identity, extra)}`, {
    headers: correlationHeaders(),
  });
  return assertOkJson<T>(response);
}

export async function httpPostQuery<T>(
  path: string,
  body: unknown,
  identity: RequestIdentity,
  extra?: Record<string, string | undefined>,
): Promise<T> {
  const response = await fetch(`${path}${buildQuery(identity, extra)}`, {
    method: "POST",
    headers: jsonHeaders(),
    body: JSON.stringify(body),
  });
  return assertOkJson<T>(response);
}

export async function httpPost<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(path, {
    method: "POST",
    headers: jsonHeaders(),
    body: JSON.stringify(body),
  });
  return assertOkJson<T>(response);
}

export async function httpPatchQuery<T>(
  path: string,
  body: unknown,
  identity: RequestIdentity,
  extra?: Record<string, string | undefined>,
): Promise<T> {
  const response = await fetch(`${path}${buildQuery(identity, extra)}`, {
    method: "PATCH",
    headers: jsonHeaders(),
    body: JSON.stringify(body),
  });
  return assertOkJson<T>(response);
}

export async function httpPutQuery<T>(
  path: string,
  body: unknown,
  identity: RequestIdentity,
  extra?: Record<string, string | undefined>,
): Promise<T> {
  const response = await fetch(`${path}${buildQuery(identity, extra)}`, {
    method: "PUT",
    headers: jsonHeaders(),
    body: JSON.stringify(body),
  });
  return assertOkJson<T>(response);
}

export async function httpDelete(
  path: string,
  identity: RequestIdentity,
  extra?: Record<string, string | undefined>,
): Promise<void> {
  const response = await fetch(`${path}${buildQuery(identity, extra)}`, {
    method: "DELETE",
    headers: correlationHeaders(),
  });
  await assertOk(response);
}
