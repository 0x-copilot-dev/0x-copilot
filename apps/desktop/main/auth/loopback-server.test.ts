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
});
