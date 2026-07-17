// @vitest-environment node
import { describe, expect, it } from "vitest";

import { awaitLoopbackCode } from "./loopback-server";

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
