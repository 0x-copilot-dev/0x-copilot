import type { RequestIdentity } from "./config";
import { identityParams } from "./config";

export async function assertOk(response: Response): Promise<void> {
  if (response.ok) {
    return;
  }
  const detail = await response.text();
  throw new Error(detail || `Request failed with ${response.status}`);
}

export function jsonHeaders(): HeadersInit {
  return { "content-type": "application/json" };
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
  const response = await fetch(`${path}${buildQuery(identity, extra)}`);
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

export async function httpDelete(
  path: string,
  identity: RequestIdentity,
  extra?: Record<string, string | undefined>,
): Promise<void> {
  const response = await fetch(`${path}${buildQuery(identity, extra)}`, {
    method: "DELETE",
  });
  await assertOk(response);
}
