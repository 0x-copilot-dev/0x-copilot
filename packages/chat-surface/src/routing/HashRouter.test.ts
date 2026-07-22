import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ArtifactRoute } from "./router";
import { HashRouter } from "./HashRouter";

function setHash(value: string): void {
  globalThis.window.location.hash = value;
}

function fireHashChange(): void {
  globalThis.window.dispatchEvent(new HashChangeEvent("hashchange"));
}

describe("HashRouter", () => {
  let router: HashRouter | undefined;

  beforeEach(() => {
    setHash("");
  });

  afterEach(() => {
    router?.dispose();
    router = undefined;
    setHash("");
  });

  describe("construction", () => {
    it("parses the current hash on construction", () => {
      setHash("#/chat/conv-1");
      router = new HashRouter();
      expect(router.current()).toEqual({
        kind: "chat",
        conversationId: "conv-1",
      });
    });

    it("returns null when hash is empty", () => {
      router = new HashRouter();
      expect(router.current()).toBeNull();
    });

    it("returns null for unknown schemes", () => {
      setHash("#/unknown/whatever");
      router = new HashRouter();
      expect(router.current()).toBeNull();
    });

    it("returns null for hashes whose scheme isn't a navigation kind", () => {
      // email is a registered ArtifactScheme but not an ArtifactRoute kind.
      setHash("#/email/draft-1");
      router = new HashRouter();
      expect(router.current()).toBeNull();
    });

    it("returns null for malformed hashes", () => {
      setHash("#malformed");
      router = new HashRouter();
      expect(router.current()).toBeNull();
    });

    it("returns null when body is missing", () => {
      setHash("#/chat/");
      router = new HashRouter();
      expect(router.current()).toBeNull();
    });

    it("accepts initialRoute and writes the canonical hash", () => {
      const route: ArtifactRoute = { kind: "run", runId: "run-1" };
      router = new HashRouter({ initialRoute: route });
      expect(router.current()).toEqual(route);
      expect(globalThis.window.location.hash).toBe("#/run/run-1");
    });

    it("accepts initialRoute = null and clears the hash", () => {
      setHash("#/chat/conv-1");
      router = new HashRouter({ initialRoute: null });
      expect(router.current()).toBeNull();
    });
  });

  describe("scheme coverage", () => {
    const cases: ReadonlyArray<{
      readonly hash: string;
      readonly route: ArtifactRoute;
    }> = [
      {
        hash: "#/chat/conv-1",
        route: { kind: "chat", conversationId: "conv-1" },
      },
      {
        hash: "#/convo/conv-1",
        route: { kind: "conversation", conversationId: "conv-1" },
      },
      { hash: "#/run/run-1", route: { kind: "run", runId: "run-1" } },
      {
        hash: "#/subagent/run-1/sub-1",
        route: { kind: "subagent", runId: "run-1", subagentId: "sub-1" },
      },
      {
        hash: "#/tool-result/run-1/step-1",
        route: { kind: "tool-result", runId: "run-1", stepId: "step-1" },
      },
      {
        hash: "#/mcp/server-1",
        route: { kind: "mcp", serverId: "server-1" },
      },
      {
        hash: "#/mcp-tool/server-1/tool-x",
        route: {
          kind: "mcp-tool",
          serverId: "server-1",
          toolName: "tool-x",
        },
      },
      {
        hash: "#/skill/skill-1",
        route: { kind: "skill", skillId: "skill-1" },
      },
      {
        hash: "#/workspace/wsp_acme",
        route: { kind: "workspace", workspaceId: "wsp_acme" },
      },
    ];

    for (const { hash, route } of cases) {
      it(`round-trips ${route.kind}`, () => {
        setHash(hash);
        router = new HashRouter();
        expect(router.current()).toEqual(route);

        router.dispose();
        setHash("");
        router = new HashRouter({ initialRoute: route });
        expect(globalThis.window.location.hash).toBe(hash);
        expect(router.current()).toEqual(route);
      });
    }
  });

  describe("conversation runId deep-link (§D1)", () => {
    it("round-trips a conversation route with a runId", () => {
      const route: ArtifactRoute = {
        kind: "conversation",
        conversationId: "conv-1",
        runId: "run-9",
      };
      setHash("#/convo/conv-1/run-9");
      router = new HashRouter();
      expect(router.current()).toEqual(route);

      router.dispose();
      setHash("");
      router = new HashRouter({ initialRoute: route });
      expect(globalThis.window.location.hash).toBe("#/convo/conv-1/run-9");
      expect(router.current()).toEqual(route);
    });

    it("decodes a bare conversation hash without a runId (back-compat)", () => {
      setHash("#/convo/conv-1");
      router = new HashRouter();
      const current = router.current();
      expect(current).toEqual({
        kind: "conversation",
        conversationId: "conv-1",
      });
      // runId is absent (undefined), not null/empty — old hashes are unaffected.
      expect(
        current !== null && current.kind === "conversation"
          ? current.runId
          : "unreachable",
      ).toBeUndefined();
    });
  });

  describe("navigate", () => {
    it("writes the hash", () => {
      router = new HashRouter();
      router.navigate({ kind: "chat", conversationId: "conv-1" });
      expect(globalThis.window.location.hash).toBe("#/chat/conv-1");
    });

    it("updates current() synchronously", () => {
      router = new HashRouter();
      router.navigate({ kind: "skill", skillId: "skill-1" });
      expect(router.current()).toEqual({ kind: "skill", skillId: "skill-1" });
    });

    it("notifies subscribers on the same navigate call (no duplicate via hashchange)", () => {
      router = new HashRouter();
      const listener = vi.fn();
      router.subscribe(listener);
      router.navigate({ kind: "run", runId: "run-1" });
      // Simulate the substrate firing hashchange in response to our write
      fireHashChange();
      expect(listener).toHaveBeenCalledTimes(1);
      expect(listener).toHaveBeenCalledWith({ kind: "run", runId: "run-1" });
    });

    it("navigate(null) clears the hash and notifies", () => {
      router = new HashRouter({
        initialRoute: { kind: "chat", conversationId: "conv-1" },
      });
      const listener = vi.fn();
      router.subscribe(listener);
      router.navigate(null);
      expect(globalThis.window.location.hash).toBe("");
      expect(router.current()).toBeNull();
      expect(listener).toHaveBeenCalledWith(null);
    });

    it("replace: true uses history.replaceState", () => {
      router = new HashRouter();
      const spy = vi.spyOn(globalThis.window.history, "replaceState");
      router.navigate(
        { kind: "chat", conversationId: "conv-1" },
        { replace: true },
      );
      expect(spy).toHaveBeenCalledTimes(1);
      spy.mockRestore();
    });

    it("throws on routes with an empty body field", () => {
      router = new HashRouter();
      expect(() =>
        router?.navigate({ kind: "chat", conversationId: "" }),
      ).toThrow(/empty body/);
    });
  });

  describe("subscribe", () => {
    it("returns an unsubscribe function", () => {
      router = new HashRouter();
      const listener = vi.fn();
      const unsubscribe = router.subscribe(listener);
      router.navigate({ kind: "chat", conversationId: "conv-1" });
      expect(listener).toHaveBeenCalledTimes(1);
      unsubscribe();
      router.navigate({ kind: "chat", conversationId: "conv-2" });
      expect(listener).toHaveBeenCalledTimes(1);
    });

    it("fires on external hashchange (browser back/forward, URL paste)", () => {
      router = new HashRouter();
      const listener = vi.fn();
      router.subscribe(listener);
      setHash("#/chat/conv-7");
      fireHashChange();
      expect(listener).toHaveBeenCalledTimes(1);
      expect(listener).toHaveBeenCalledWith({
        kind: "chat",
        conversationId: "conv-7",
      });
      expect(router.current()).toEqual({
        kind: "chat",
        conversationId: "conv-7",
      });
    });

    it("delivers null for an externally-set unknown hash", () => {
      router = new HashRouter({
        initialRoute: { kind: "chat", conversationId: "conv-1" },
      });
      const listener = vi.fn();
      router.subscribe(listener);
      setHash("#/unknown/x");
      fireHashChange();
      expect(listener).toHaveBeenCalledWith(null);
      expect(router.current()).toBeNull();
    });

    it("supports multiple subscribers", () => {
      router = new HashRouter();
      const a = vi.fn();
      const b = vi.fn();
      router.subscribe(a);
      router.subscribe(b);
      router.navigate({ kind: "run", runId: "run-1" });
      expect(a).toHaveBeenCalledTimes(1);
      expect(b).toHaveBeenCalledTimes(1);
    });
  });

  describe("dispose", () => {
    it("stops responding to hashchange", () => {
      router = new HashRouter();
      const listener = vi.fn();
      router.subscribe(listener);
      router.dispose();
      setHash("#/chat/conv-1");
      fireHashChange();
      expect(listener).not.toHaveBeenCalled();
    });
  });
});
