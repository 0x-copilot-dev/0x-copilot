// WC-P5b — web MCP-OAuth launcher tests.
//
// The launcher is the host half of the `mcp_auth` Connect card: `beginAuth`
// stashes the pending action (so App's `/mcp/oauth/callback` effect can resume
// run→conversation, AD-8) and redirects to the vendor consent screen; `skipAuth`
// dismisses the gate WITHOUT the `/v1/agent/approvals/{id}/decision` POST (AD-7).
// The connector operations + redirect are injected, so these tests mock them and
// assert the stash + launch behaviour directly against the real `mcpAuthAction`
// sessionStorage helpers (reused unchanged).

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { readPendingMcpAuthAction } from "../chat/mcpAuthAction";
import { createWebMcpAuthPort } from "./webMcpAuthPort";

function makeDeps(
  overrides: Partial<Parameters<typeof createWebMcpAuthPort>[0]> = {},
) {
  return {
    resolveActiveRunId: vi.fn(async () => "run-1" as string | null),
    startAuth: vi.fn(async () => "https://vendor.example/consent?state=abc"),
    recordSkip: vi.fn(async () => undefined),
    installConnector: vi.fn(async () => "srv-installed"),
    redirect: vi.fn((_url: string) => undefined),
    onError: vi.fn(),
    ...overrides,
  };
}

beforeEach(() => {
  window.sessionStorage.clear();
});

afterEach(() => {
  vi.restoreAllMocks();
  window.sessionStorage.clear();
});

describe("createWebMcpAuthPort", () => {
  it("beginAuth stashes the pending action (run id resolved) then redirects to the auth URL", async () => {
    const deps = makeDeps();
    const port = createWebMcpAuthPort(deps);

    port.beginAuth("srv-1");

    // The launcher resolves the active run, stashes, then redirects — all async.
    await vi.waitFor(() => expect(deps.redirect).toHaveBeenCalledTimes(1));

    // The stash carries this server AND a run id derived from the reconstructed
    // `mcp_auth:<run_id>:<server_id>` approval id — the breadcrumb the callback
    // reads back to resume (AD-8).
    const stashed = readPendingMcpAuthAction("srv-1");
    expect(stashed).not.toBeNull();
    expect(stashed?.serverId).toBe("srv-1");
    expect(stashed?.runId).toBe("run-1");
    expect(stashed?.approvalId).toBe("mcp_auth:run-1:srv-1");

    // OAuth was started for this server and the host redirected to the returned URL.
    expect(deps.startAuth).toHaveBeenCalledWith("srv-1");
    expect(deps.redirect).toHaveBeenCalledWith(
      "https://vendor.example/consent?state=abc",
    );
    expect(deps.onError).not.toHaveBeenCalled();
  });

  it("beginAuth still launches OAuth when there is no active run (stash skipped, no throw)", async () => {
    const deps = makeDeps({ resolveActiveRunId: vi.fn(async () => null) });
    const port = createWebMcpAuthPort(deps);

    port.beginAuth("srv-1");

    await vi.waitFor(() => expect(deps.redirect).toHaveBeenCalledTimes(1));
    // No run id → nothing to resume from → no stash, but OAuth still starts.
    expect(readPendingMcpAuthAction("srv-1")).toBeNull();
    expect(deps.startAuth).toHaveBeenCalledWith("srv-1");
    expect(deps.onError).not.toHaveBeenCalled();
  });

  it("skipAuth clears the stash and records the skip WITHOUT any /decision POST", async () => {
    const deps = makeDeps();
    const port = createWebMcpAuthPort(deps);

    // Seed a stash for this server (as beginAuth would have).
    port.beginAuth("srv-1");
    await vi.waitFor(() =>
      expect(readPendingMcpAuthAction("srv-1")).not.toBeNull(),
    );

    port.skipAuth("srv-1");

    // The stash is cleared synchronously; the skip is recorded best-effort.
    expect(readPendingMcpAuthAction("srv-1")).toBeNull();
    await vi.waitFor(() =>
      expect(deps.recordSkip).toHaveBeenCalledWith("srv-1"),
    );
    // The launcher has no `/decision` path at all — it never redirects on skip.
    expect(deps.redirect).toHaveBeenCalledTimes(1); // only the begin above
    expect(deps.onError).not.toHaveBeenCalled();
  });

  it("skipAuth swallows a recordSkip failure (e.g. a discovery suggestion has no row)", async () => {
    const deps = makeDeps({
      recordSkip: vi.fn(async () => {
        throw new Error("404 no server row");
      }),
    });
    const port = createWebMcpAuthPort(deps);

    // No throw into the card; the error is routed to onError instead.
    expect(() => port.skipAuth("srv-x")).not.toThrow();
    await vi.waitFor(() => expect(deps.onError).toHaveBeenCalledTimes(1));
  });

  it("installFromCatalog installs then begins auth on the freshly minted server", async () => {
    const deps = makeDeps({
      installConnector: vi.fn(async () => "srv-new"),
    });
    const port = createWebMcpAuthPort(deps);

    port.installFromCatalog("linear");

    await vi.waitFor(() => expect(deps.redirect).toHaveBeenCalledTimes(1));
    expect(deps.installConnector).toHaveBeenCalledWith("linear");
    // beginAuth ran on the installed server: OAuth started + stash placed for it.
    expect(deps.startAuth).toHaveBeenCalledWith("srv-new");
    expect(readPendingMcpAuthAction("srv-new")?.serverId).toBe("srv-new");
  });
});
