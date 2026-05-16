import { UnauthorizedError } from "@enterprise-search/chat-transport";

import type { RequestIdentity } from "./config";
import { identityParams } from "./config";
import {
  getAppTransport,
  getAuthBearer,
  notifyUnauthorized,
  setAuthBearerProvider,
  setUnauthorizedHandler,
} from "./transport";

// Re-exported for callers that catch typed auth errors. Source of truth is
// @enterprise-search/chat-transport — keep the symbol here only as a
// backward-compatible re-export so AuthContext + tests don't need to chase
// the package boundary in every catch block.
export { UnauthorizedError };

const REQUEST_ID_HEADER = "x-request-id";
const AUTHORIZATION_HEADER = "authorization";

type UnauthorizedHandler = (response: Response) => void;
type BearerProvider = () => string | null;

// Bearer plumbing and 401 notification used to live as module-private
// closures in this file. They moved into ./transport.ts so a single
// WebTransport instance owns the substrate boundary (see
// docs/architecture/desktop-app-rollout.md §3.1). The two `configure*`
// functions below are deprecation shims kept so AuthContext, the api
// modules, and existing tests can migrate one PR at a time instead of in
// a flag-day rewrite. Slated for deletion in the rollout plan's PR #5.

export function configureUnauthorizedHandler(
  handler: UnauthorizedHandler | null,
): void {
  setUnauthorizedHandler(handler);
}

export function configureAuthBearerProvider(provider: BearerProvider): void {
  setAuthBearerProvider(provider);
}

export async function assertOk(response: Response): Promise<void> {
  if (response.ok) {
    return;
  }
  const body = await response.text();
  // FastAPI / Starlette serialise errors as `{"detail": "..."}`. Pull the
  // message out so consumers don't render raw JSON. Non-JSON bodies fall
  // through verbatim (proxy timeouts, HTML error pages).
  const message = parseErrorMessage(body) ?? body;
  if (response.status === 401) {
    notifyUnauthorized(response);
    throw new UnauthorizedError(message);
  }
  throw new Error(message || `Request failed with ${response.status}`);
}

function parseErrorMessage(body: string): string | null {
  if (!body || body[0] !== "{") {
    return null;
  }
  try {
    const parsed = JSON.parse(body) as { detail?: unknown };
    if (typeof parsed.detail === "string" && parsed.detail.trim() !== "") {
      return parsed.detail;
    }
  } catch {
    /* not JSON; fall through */
  }
  return null;
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
  const bearer = getAuthBearer();
  if (bearer) {
    headers[AUTHORIZATION_HEADER] = `Bearer ${bearer}`;
  }
  return headers;
}

export function dynamicCorrelationHeaders(): Record<string, string> {
  return new Proxy({} as Record<string, string>, {
    get(_target, property) {
      return typeof property === "string"
        ? correlationHeaders()[property]
        : undefined;
    },
    getOwnPropertyDescriptor(_target, property) {
      if (typeof property !== "string") {
        return undefined;
      }
      return property in correlationHeaders()
        ? { configurable: true, enumerable: true }
        : undefined;
    },
    ownKeys() {
      return Object.keys(correlationHeaders());
    },
  });
}

export function jsonHeaders(): HeadersInit {
  return { "content-type": "application/json", ...correlationHeaders() };
}

async function assertOkJson<T>(response: Response): Promise<T> {
  await assertOk(response);
  return (await response.json()) as T;
}

// kept exported because some legacy api modules wrap `assertOkJson` for
// the JSON happy path; everything else routes through getAppTransport().
export { assertOkJson };

function buildQuery(
  identity: RequestIdentity | null,
  extra: Record<string, string | undefined> | undefined,
): Record<string, string | undefined> {
  const out: Record<string, string | undefined> = {};
  if (identity) {
    for (const [k, v] of identityParams(identity)) {
      out[k] = v;
    }
  }
  if (extra) {
    for (const [k, v] of Object.entries(extra)) {
      if (v !== undefined) {
        out[k] = v;
      }
    }
  }
  return out;
}

export async function httpGet<T>(
  path: string,
  identity: RequestIdentity,
  extra?: Record<string, string | undefined>,
): Promise<T> {
  return getAppTransport().request<T>({
    method: "GET",
    path,
    query: buildQuery(identity, extra),
  });
}

export async function httpPostQuery<T>(
  path: string,
  body: unknown,
  identity: RequestIdentity,
  extra?: Record<string, string | undefined>,
): Promise<T> {
  return getAppTransport().request<T>({
    method: "POST",
    path,
    query: buildQuery(identity, extra),
    body,
  });
}

export async function httpPost<T>(path: string, body: unknown): Promise<T> {
  return getAppTransport().request<T>({
    method: "POST",
    path,
    body,
  });
}

export async function httpPatchQuery<T>(
  path: string,
  body: unknown,
  identity: RequestIdentity,
  extra?: Record<string, string | undefined>,
): Promise<T> {
  return getAppTransport().request<T>({
    method: "PATCH",
    path,
    query: buildQuery(identity, extra),
    body,
  });
}

export async function httpPutQuery<T>(
  path: string,
  body: unknown,
  identity: RequestIdentity,
  extra?: Record<string, string | undefined>,
): Promise<T> {
  return getAppTransport().request<T>({
    method: "PUT",
    path,
    query: buildQuery(identity, extra),
    body,
  });
}

export async function httpDelete(
  path: string,
  identity: RequestIdentity,
  extra?: Record<string, string | undefined>,
): Promise<void> {
  await getAppTransport().request<void>({
    method: "DELETE",
    path,
    query: buildQuery(identity, extra),
  });
}
