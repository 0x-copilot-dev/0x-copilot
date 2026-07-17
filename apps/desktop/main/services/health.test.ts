// @vitest-environment node
import { describe, expect, it } from "vitest";

import { HealthCheckTimeout, waitForHealthy } from "./health";

describe("waitForHealthy", () => {
  it("resolves once /v1/health returns ok", async () => {
    const urls: string[] = [];
    let attempts = 0;
    await waitForHealthy({
      service: "backend",
      baseUrl: "http://127.0.0.1:8101",
      fetchFn: (url) => {
        urls.push(url);
        attempts += 1;
        if (attempts < 3) return Promise.reject(new Error("ECONNREFUSED"));
        return Promise.resolve({ ok: true });
      },
      timeoutMs: 10_000,
      intervalMs: 100,
      sleep: () => Promise.resolve(),
      now: () => 0,
    });
    expect(attempts).toBe(3);
    expect(urls[0]).toBe("http://127.0.0.1:8101/v1/health");
  });

  it("keeps polling through non-ok responses", async () => {
    let attempts = 0;
    await waitForHealthy({
      service: "backend-facade",
      baseUrl: "http://127.0.0.1:8201",
      fetchFn: () => {
        attempts += 1;
        return Promise.resolve({ ok: attempts >= 2 });
      },
      timeoutMs: 10_000,
      sleep: () => Promise.resolve(),
      now: () => 0,
    });
    expect(attempts).toBe(2);
  });

  it("throws HealthCheckTimeout when the budget is exhausted", async () => {
    let t = 0;
    let caught: unknown = null;
    try {
      await waitForHealthy({
        service: "ai-backend",
        baseUrl: "http://127.0.0.1:8001",
        fetchFn: () => Promise.reject(new Error("ECONNREFUSED")),
        timeoutMs: 1000,
        intervalMs: 100,
        sleep: () => Promise.resolve(),
        now: () => {
          t += 400;
          return t;
        },
      });
    } catch (err) {
      caught = err;
    }
    expect(caught).toBeInstanceOf(HealthCheckTimeout);
    const timeout = caught as HealthCheckTimeout;
    expect(timeout.service).toBe("ai-backend");
    expect(timeout.url).toBe("http://127.0.0.1:8001/v1/health");
  });
});
