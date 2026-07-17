// @vitest-environment node
import { describe, expect, it } from "vitest";

import { awaitLoopbackCode, awaitLoopbackHandoff } from "./loopback-server";

describe("awaitLoopbackCode", () => {
  it("resolves with the code when the redirect lands with a matching state", async () => {
    const handle = await awaitLoopbackCode({
      expectedState: "state-1",
      timeoutMs: 2000,
    });
    try {
      const fetchResponse = await fetch(
        `${handle.redirectUri}?code=abc123&state=state-1`,
      );
      expect(fetchResponse.status).toBe(200);
      const result = await handle.codePromise;
      expect(result.code).toBe("abc123");
      expect(result.state).toBe("state-1");
    } finally {
      handle.close();
    }
  });

  it("rejects when state does not match", async () => {
    const handle = await awaitLoopbackCode({
      expectedState: "state-good",
      timeoutMs: 2000,
    });
    try {
      await fetch(`${handle.redirectUri}?code=abc&state=state-bad`);
      await expect(handle.codePromise).rejects.toThrow(/state mismatch/u);
    } finally {
      handle.close();
    }
  });

  it("rejects when error param is present", async () => {
    const handle = await awaitLoopbackCode({
      expectedState: "state-1",
      timeoutMs: 2000,
    });
    try {
      await fetch(`${handle.redirectUri}?error=access_denied&state=state-1`);
      await expect(handle.codePromise).rejects.toThrow(/access_denied/u);
    } finally {
      handle.close();
    }
  });

  it("returns 404 for unrelated paths", async () => {
    const handle = await awaitLoopbackCode({
      expectedState: "state-1",
      timeoutMs: 2000,
    });
    try {
      const response = await fetch(`http://127.0.0.1:${handle.port}/other`);
      expect(response.status).toBe(404);
    } finally {
      handle.close();
    }
  });

  it("binds to an ephemeral port", async () => {
    const handle = await awaitLoopbackCode({
      expectedState: "x",
      timeoutMs: 1000,
    });
    try {
      expect(handle.port).toBeGreaterThan(0);
      expect(handle.redirectUri).toMatch(/^http:\/\/127\.0\.0\.1:\d+\/cb$/u);
    } finally {
      handle.close();
    }
  });

  it("close before any redirect rejects the code promise", async () => {
    const handle = await awaitLoopbackCode({
      expectedState: "x",
      timeoutMs: 60_000,
    });
    handle.close();
    await expect(handle.codePromise).rejects.toThrow(/closed before redirect/u);
  });
});

describe("awaitLoopbackCode — deferred state arming", () => {
  it("answers 400 without killing the flow before the state is armed, then resolves once armed", async () => {
    const handle = await awaitLoopbackCode({ timeoutMs: 5000 });
    try {
      // Pre-arm request: rejected at HTTP level, but codePromise survives.
      const early = await fetch(`${handle.redirectUri}?code=x&state=whatever`);
      expect(early.status).toBe(400);

      handle.armState("late-state");
      const ok = await fetch(`${handle.redirectUri}?code=c-1&state=late-state`);
      expect(ok.status).toBe(200);
      const result = await handle.codePromise;
      expect(result).toEqual({ code: "c-1", state: "late-state" });
    } finally {
      handle.close();
    }
  });

  it("rejects on state mismatch after arming", async () => {
    const handle = await awaitLoopbackCode({ timeoutMs: 5000 });
    try {
      handle.armState("expected");
      await fetch(`${handle.redirectUri}?code=c&state=forged`);
      await expect(handle.codePromise).rejects.toThrow(/state mismatch/u);
    } finally {
      handle.close();
    }
  });
});

describe("awaitLoopbackHandoff — wallet bearer handoff", () => {
  function handoffQuery(overrides: Record<string, string> = {}): string {
    const params = new URLSearchParams({
      state: "st-w",
      bearer_token: "bearer-w",
      user_id: "usr_w",
      session_id: "ses_w",
      expires_at: "2026-07-17T00:00:00.000Z",
      requires_mfa: "false",
      ...overrides,
    });
    for (const [key, value] of Object.entries(overrides)) {
      if (value === "") params.delete(key);
    }
    return params.toString();
  }

  it("resolves with the parsed session handoff when the state matches", async () => {
    const handle = await awaitLoopbackHandoff({
      expectedState: "st-w",
      callbackPath: "/wallet/cb",
      timeoutMs: 2000,
    });
    try {
      expect(handle.redirectUri).toMatch(
        /^http:\/\/127\.0\.0\.1:\d+\/wallet\/cb$/u,
      );
      const response = await fetch(
        `${handle.redirectUri}?${handoffQuery({ return_to: "atlas-desktop" })}`,
      );
      expect(response.status).toBe(200);
      const handoff = await handle.handoffPromise;
      expect(handoff).toEqual({
        bearerToken: "bearer-w",
        userId: "usr_w",
        sessionId: "ses_w",
        expiresAt: "2026-07-17T00:00:00.000Z",
        requiresMfa: false,
        returnTo: "atlas-desktop",
        state: "st-w",
      });
    } finally {
      handle.close();
    }
  });

  it("parses requires_mfa=true and leaves return_to null when absent", async () => {
    const handle = await awaitLoopbackHandoff({
      expectedState: "st-w",
      timeoutMs: 2000,
    });
    try {
      await fetch(
        `${handle.redirectUri}?${handoffQuery({ requires_mfa: "true" })}`,
      );
      const handoff = await handle.handoffPromise;
      expect(handoff.requiresMfa).toBe(true);
      expect(handoff.returnTo).toBeNull();
    } finally {
      handle.close();
    }
  });

  it("rejects when the state does not match (forged handoff)", async () => {
    const handle = await awaitLoopbackHandoff({
      expectedState: "st-good",
      timeoutMs: 2000,
    });
    try {
      const response = await fetch(
        `${handle.redirectUri}?${handoffQuery({ state: "st-bad" })}`,
      );
      expect(response.status).toBe(400);
      await expect(handle.handoffPromise).rejects.toThrow(/state mismatch/u);
    } finally {
      handle.close();
    }
  });

  it("rejects when required session fields are missing", async () => {
    const handle = await awaitLoopbackHandoff({
      expectedState: "st-w",
      timeoutMs: 2000,
    });
    try {
      const response = await fetch(
        `${handle.redirectUri}?${handoffQuery({ bearer_token: "" })}`,
      );
      expect(response.status).toBe(400);
      await expect(handle.handoffPromise).rejects.toThrow(
        /missing required session fields/u,
      );
    } finally {
      handle.close();
    }
  });

  it("rejects a malformed requires_mfa value", async () => {
    const handle = await awaitLoopbackHandoff({
      expectedState: "st-w",
      timeoutMs: 2000,
    });
    try {
      await fetch(
        `${handle.redirectUri}?${handoffQuery({ requires_mfa: "maybe" })}`,
      );
      await expect(handle.handoffPromise).rejects.toThrow(
        /malformed requires_mfa/u,
      );
    } finally {
      handle.close();
    }
  });

  it("rejects when an error param is present", async () => {
    const handle = await awaitLoopbackHandoff({
      expectedState: "st-w",
      timeoutMs: 2000,
    });
    try {
      await fetch(`${handle.redirectUri}?error=user_rejected&state=st-w`);
      await expect(handle.handoffPromise).rejects.toThrow(/user_rejected/u);
    } finally {
      handle.close();
    }
  });

  it("returns 404 for unrelated paths", async () => {
    const handle = await awaitLoopbackHandoff({
      expectedState: "st-w",
      timeoutMs: 2000,
    });
    try {
      const response = await fetch(`http://127.0.0.1:${handle.port}/other`);
      expect(response.status).toBe(404);
    } finally {
      handle.close();
    }
  });

  it("close before any redirect rejects the handoff promise", async () => {
    const handle = await awaitLoopbackHandoff({
      expectedState: "st-w",
      timeoutMs: 60_000,
    });
    handle.close();
    await expect(handle.handoffPromise).rejects.toThrow(
      /closed before redirect/u,
    );
  });

  it("supports random-port binding with conflict retry like the code flow", async () => {
    const blocker = await awaitLoopbackHandoff({
      expectedState: "b",
      timeoutMs: 5000,
      randomPorts: {},
    });
    const picks = [blocker.port, 0];
    try {
      const handle = await awaitLoopbackHandoff({
        expectedState: "x",
        timeoutMs: 5000,
        randomPorts: { pick: () => picks.shift() ?? 0 },
      });
      try {
        expect(picks).toHaveLength(0);
        expect(handle.port).not.toBe(blocker.port);
      } finally {
        handle.close();
      }
    } finally {
      blocker.close();
    }
  });
});

describe("awaitLoopbackCode — random-port binding", () => {
  it("retries on EADDRINUSE and binds the next picked port", async () => {
    // Occupy a port with a first loopback server, then force the second
    // to pick the occupied port first.
    const blocker = await awaitLoopbackCode({
      expectedState: "b",
      timeoutMs: 5000,
      randomPorts: {},
    });
    const picks = [blocker.port, 0]; // 0 → OS-assigned, always free
    try {
      const handle = await awaitLoopbackCode({
        expectedState: "x",
        timeoutMs: 5000,
        randomPorts: { pick: () => picks.shift() ?? 0 },
      });
      try {
        expect(picks).toHaveLength(0); // both picks consumed → one retry
        expect(handle.port).not.toBe(blocker.port);
      } finally {
        handle.close();
      }
    } finally {
      blocker.close();
    }
  });

  it("gives up after the configured number of conflicting attempts", async () => {
    const blocker = await awaitLoopbackCode({
      expectedState: "b",
      timeoutMs: 5000,
      randomPorts: {},
    });
    try {
      await expect(
        awaitLoopbackCode({
          expectedState: "x",
          timeoutMs: 5000,
          randomPorts: { attempts: 3, pick: () => blocker.port },
        }),
      ).rejects.toThrow(/no free port after 3 attempts/u);
    } finally {
      blocker.close();
    }
  });
});
