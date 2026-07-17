export class HealthCheckTimeout extends Error {
  readonly service: string;
  readonly url: string;

  constructor(service: string, url: string, timeoutMs: number) {
    super(`${service} did not become healthy at ${url} within ${timeoutMs}ms`);
    this.name = "HealthCheckTimeout";
    this.service = service;
    this.url = url;
  }
}

export type HealthFetch = (url: string) => Promise<{ ok: boolean }>;

export interface WaitForHealthyOptions {
  readonly service: string;
  /** e.g. http://127.0.0.1:8200 — /v1/health is appended here. */
  readonly baseUrl: string;
  readonly fetchFn?: HealthFetch;
  /** Total budget. Default 90s (cold python + first imports are slow). */
  readonly timeoutMs?: number;
  readonly intervalMs?: number;
  readonly sleep?: (ms: number) => Promise<void>;
  readonly now?: () => number;
}

// Polls GET <baseUrl>/v1/health until 2xx or the budget is exhausted.
export async function waitForHealthy(
  options: WaitForHealthyOptions,
): Promise<void> {
  const timeoutMs = options.timeoutMs ?? 90_000;
  const intervalMs = options.intervalMs ?? 250;
  const now = options.now ?? Date.now;
  const sleep = options.sleep ?? defaultSleep;
  const fetchFn: HealthFetch =
    options.fetchFn ?? ((url) => fetch(url, { method: "GET" }));
  const url = `${options.baseUrl}/v1/health`;
  const deadline = now() + timeoutMs;
  for (;;) {
    try {
      const response = await fetchFn(url);
      if (response.ok) return;
    } catch {
      // Connection refused while the service boots — keep polling.
    }
    if (now() >= deadline) {
      throw new HealthCheckTimeout(options.service, url, timeoutMs);
    }
    await sleep(intervalMs);
  }
}

function defaultSleep(ms: number): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, ms);
  });
}
