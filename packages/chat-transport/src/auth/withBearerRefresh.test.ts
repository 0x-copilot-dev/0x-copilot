import { describe, expect, it, vi } from "vitest";

import type { Transport } from "../transport";
import {
  type Session,
  type SseSubscribeOptions,
  type SseSubscription,
  type TransportCapabilities,
  type TypedRequest,
  UnauthorizedError,
} from "../types";
import { withBearerRefresh } from "./withBearerRefresh";

interface ScriptedResponse {
  readonly ok?: unknown;
  readonly throw?: Error;
}

class ScriptedTransport implements Transport {
  readonly requests: TypedRequest[] = [];
  readonly subscribeCalls: SseSubscribeOptions[] = [];
  readonly sessionCalls: number[] = [];
  readonly capabilitiesCalls: number[] = [];
  #responses: ScriptedResponse[] = [];

  setResponses(responses: ScriptedResponse[]): void {
    this.#responses = [...responses];
  }

  async request<T>(req: TypedRequest): Promise<T> {
    this.requests.push(req);
    const next = this.#responses.shift();
    if (!next) {
      throw new Error(
        `ScriptedTransport: no response queued for ${req.method} ${req.path}`,
      );
    }
    if (next.throw) {
      throw next.throw;
    }
    return next.ok as T;
  }

  subscribeServerSentEvents(opts: SseSubscribeOptions): SseSubscription {
    this.subscribeCalls.push(opts);
    return { close: () => {} };
  }

  getSession(): Session {
    this.sessionCalls.push(1);
    return { bearer: "session-bearer" };
  }

  capabilities(): TransportCapabilities {
    this.capabilitiesCalls.push(1);
    return {
      substrate: "web",
      nativeSecretStorage: false,
      fileSystemAccess: false,
      clipboardWrite: false,
      openExternal: false,
    };
  }
}

const SAMPLE_REQUEST: TypedRequest = {
  method: "GET",
  path: "/v1/me/profile",
};

describe("withBearerRefresh — happy path", () => {
  it("passes a 200 response through untouched", async () => {
    const inner = new ScriptedTransport();
    inner.setResponses([{ ok: { hello: "world" } }]);
    const refresh = vi.fn();
    const decorated = withBearerRefresh(inner, {
      workspaceId: "wsp_acme",
      refresh,
    });

    const res = await decorated.request<{ hello: string }>(SAMPLE_REQUEST);

    expect(res).toEqual({ hello: "world" });
    expect(refresh).not.toHaveBeenCalled();
    expect(inner.requests).toHaveLength(1);
  });
});

describe("withBearerRefresh — 401 then refresh succeeds", () => {
  it("refreshes, retries, and returns the 200", async () => {
    const inner = new ScriptedTransport();
    inner.setResponses([
      { throw: new UnauthorizedError("expired") },
      { ok: { hello: "after-refresh" } },
    ]);
    const refresh = vi.fn(async () => ({ ok: true }));
    const onRetry = vi.fn();
    const onRefreshFailure = vi.fn();

    const decorated = withBearerRefresh(inner, {
      workspaceId: "wsp_acme",
      refresh,
      onUnauthorizedRetry: onRetry,
      onRefreshFailure,
    });

    const res = await decorated.request<{ hello: string }>(SAMPLE_REQUEST);

    expect(res).toEqual({ hello: "after-refresh" });
    expect(refresh).toHaveBeenCalledTimes(1);
    expect(refresh).toHaveBeenCalledWith("wsp_acme");
    expect(inner.requests).toHaveLength(2);
    expect(onRetry).toHaveBeenCalledTimes(1);
    expect(onRetry).toHaveBeenCalledWith(SAMPLE_REQUEST);
    expect(onRefreshFailure).not.toHaveBeenCalled();
  });
});

describe("withBearerRefresh — 401 then refresh fails", () => {
  it("propagates the original 401 and notifies onRefreshFailure once", async () => {
    const inner = new ScriptedTransport();
    const original = new UnauthorizedError("expired");
    inner.setResponses([{ throw: original }]);
    const refresh = vi.fn(async () => ({
      ok: false,
      reason: "no refresh token",
    }));
    const onRefreshFailure = vi.fn();

    const decorated = withBearerRefresh(inner, {
      workspaceId: "wsp_acme",
      refresh,
      onRefreshFailure,
    });

    await expect(decorated.request(SAMPLE_REQUEST)).rejects.toBe(original);
    expect(refresh).toHaveBeenCalledTimes(1);
    expect(inner.requests).toHaveLength(1);
    expect(onRefreshFailure).toHaveBeenCalledTimes(1);
    expect(onRefreshFailure).toHaveBeenCalledWith("no refresh token");
  });
});

describe("withBearerRefresh — 401 then refresh ok then 401 again", () => {
  it("does not loop: refreshes exactly once, propagates the second 401", async () => {
    const inner = new ScriptedTransport();
    const second401 = new UnauthorizedError("still unauthorized");
    inner.setResponses([
      { throw: new UnauthorizedError("first") },
      { throw: second401 },
    ]);
    const refresh = vi.fn(async () => ({ ok: true }));

    const decorated = withBearerRefresh(inner, {
      workspaceId: "wsp_acme",
      refresh,
    });

    await expect(decorated.request(SAMPLE_REQUEST)).rejects.toBe(second401);
    expect(refresh).toHaveBeenCalledTimes(1);
    expect(inner.requests).toHaveLength(2);
  });
});

describe("withBearerRefresh — refresh budget is per-request", () => {
  it("a fresh request after a 401 gets its own refresh budget", async () => {
    const inner = new ScriptedTransport();
    inner.setResponses([
      { throw: new UnauthorizedError("expired-1") },
      { ok: { n: 1 } },
      { throw: new UnauthorizedError("expired-2") },
      { ok: { n: 2 } },
    ]);
    const refresh = vi.fn(async () => ({ ok: true }));

    const decorated = withBearerRefresh(inner, {
      workspaceId: "wsp_acme",
      refresh,
    });

    const a = await decorated.request<{ n: number }>(SAMPLE_REQUEST);
    const b = await decorated.request<{ n: number }>(SAMPLE_REQUEST);

    expect(a).toEqual({ n: 1 });
    expect(b).toEqual({ n: 2 });
    expect(refresh).toHaveBeenCalledTimes(2);
    expect(inner.requests).toHaveLength(4);
  });
});

describe("withBearerRefresh — non-401 errors are not refreshed", () => {
  it("passes a 500-style error through without calling refresh", async () => {
    const inner = new ScriptedTransport();
    const boom = new Error("server exploded");
    inner.setResponses([{ throw: boom }]);
    const refresh = vi.fn();

    const decorated = withBearerRefresh(inner, {
      workspaceId: "wsp_acme",
      refresh,
    });

    await expect(decorated.request(SAMPLE_REQUEST)).rejects.toBe(boom);
    expect(refresh).not.toHaveBeenCalled();
  });
});

describe("withBearerRefresh — pass-through methods", () => {
  it("subscribeServerSentEvents is pass-through", () => {
    const inner = new ScriptedTransport();
    const decorated = withBearerRefresh(inner, {
      workspaceId: "wsp_acme",
      refresh: async () => ({ ok: true }),
    });

    decorated.subscribeServerSentEvents({
      path: "/events",
      onMessage: () => {},
    });

    expect(inner.subscribeCalls).toHaveLength(1);
    expect(inner.subscribeCalls[0].path).toBe("/events");
  });

  it("getSession is pass-through", () => {
    const inner = new ScriptedTransport();
    const decorated = withBearerRefresh(inner, {
      workspaceId: "wsp_acme",
      refresh: async () => ({ ok: true }),
    });

    const session = decorated.getSession();

    expect(session.bearer).toBe("session-bearer");
    expect(inner.sessionCalls).toHaveLength(1);
  });

  it("capabilities is pass-through", () => {
    const inner = new ScriptedTransport();
    const decorated = withBearerRefresh(inner, {
      workspaceId: "wsp_acme",
      refresh: async () => ({ ok: true }),
    });

    const caps = decorated.capabilities();

    expect(caps.substrate).toBe("web");
    expect(inner.capabilitiesCalls).toHaveLength(1);
  });
});

describe("withBearerRefresh — observer safety", () => {
  it("a throwing onUnauthorizedRetry does not mask the success path", async () => {
    const inner = new ScriptedTransport();
    inner.setResponses([
      { throw: new UnauthorizedError("expired") },
      { ok: { hello: "ok" } },
    ]);

    const decorated = withBearerRefresh(inner, {
      workspaceId: "wsp_acme",
      refresh: async () => ({ ok: true }),
      onUnauthorizedRetry: () => {
        throw new Error("observer crashed");
      },
    });

    await expect(decorated.request(SAMPLE_REQUEST)).resolves.toEqual({
      hello: "ok",
    });
  });

  it("a throwing onRefreshFailure does not mask the original 401", async () => {
    const inner = new ScriptedTransport();
    const original = new UnauthorizedError("expired");
    inner.setResponses([{ throw: original }]);

    const decorated = withBearerRefresh(inner, {
      workspaceId: "wsp_acme",
      refresh: async () => ({ ok: false, reason: "no-go" }),
      onRefreshFailure: () => {
        throw new Error("observer crashed");
      },
    });

    await expect(decorated.request(SAMPLE_REQUEST)).rejects.toBe(original);
  });
});
