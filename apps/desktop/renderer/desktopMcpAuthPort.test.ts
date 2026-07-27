// The desktop connector consent port.
//
// Before this existed, `mcpAuthPort` was undefined on desktop, so the card's
// `actionable` was false and Connect/Deny were disabled. Nothing failed — which
// is exactly why it went unnoticed. These tests pin the behaviours that would
// regress silently the same way.

import { describe, expect, it, vi } from "vitest";

import {
  NO_CATALOG_IDENTITY,
  createDesktopMcpAuthPort,
  type DesktopMcpAuthPortDeps,
} from "./desktopMcpAuthPort";

function makeDeps(
  overrides: Partial<DesktopMcpAuthPortDeps> = {},
): DesktopMcpAuthPortDeps & {
  connect: ReturnType<typeof vi.fn>;
  recordSkip: ReturnType<typeof vi.fn>;
  onConnected: ReturnType<typeof vi.fn>;
  onError: ReturnType<typeof vi.fn>;
} {
  const deps = {
    connect: vi.fn(async (slug: string) => ({
      server_id: `seed:${slug}`,
      connector_slug: slug,
      display_group: "Work",
      auth_state: "authenticated",
    })),
    recordSkip: vi.fn(async () => undefined),
    onConnected: vi.fn(),
    onError: vi.fn(),
    ...overrides,
  };
  return deps as never;
}

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

describe("createDesktopMcpAuthPort", () => {
  it("connects by SLUG, not server id", async () => {
    // The whole reason this port exists. Desktop's flow is slug-keyed all the
    // way down: the backend reconstructs the loopback redirect from a validated
    // port rather than accepting one from the client, so there is no
    // server-keyed entry point to call.
    const deps = makeDeps();
    const port = createDesktopMcpAuthPort(deps);

    port.beginAuth("seed:linear", { connectorSlug: "linear" });
    await flush();

    expect(deps.connect).toHaveBeenCalledWith("linear");
  });

  it("reports the connected server id the backend confirmed", async () => {
    // For an uninstalled suggestion the server row is minted DURING the
    // connect, so the id the card knew is not necessarily the one that exists
    // afterwards. The consent machine is keyed by server id, so using the
    // stale one would leave the card stuck at `connecting`.
    const deps = makeDeps({
      connect: vi.fn(async () => ({
        server_id: "seed:notion",
        connector_slug: "notion",
        display_group: "Work",
        auth_state: "authenticated",
      })),
    } as never);
    const port = createDesktopMcpAuthPort(deps);

    port.installFromCatalog("notion");
    await flush();

    expect(deps.onConnected).toHaveBeenCalledWith("seed:notion");
  });

  it("says so when a gate names no connector, instead of guessing", async () => {
    // A custom MCP server (added by URL) has no catalog identity. Falling back
    // to the server id would 404 on the profile lookup — a broken button rather
    // than an honestly absent one.
    const deps = makeDeps();
    const port = createDesktopMcpAuthPort(deps);

    port.beginAuth("custom:abc123", { connectorSlug: null });
    await flush();

    expect(deps.connect).not.toHaveBeenCalled();
    expect(deps.onConnected).not.toHaveBeenCalled();
    expect(deps.onError).toHaveBeenCalledWith(
      expect.objectContaining({ message: NO_CATALOG_IDENTITY }),
    );
  });

  it("treats a missing options bag as no connector", async () => {
    const deps = makeDeps();
    const port = createDesktopMcpAuthPort(deps);

    port.beginAuth("custom:abc123");
    await flush();

    expect(deps.connect).not.toHaveBeenCalled();
    expect(deps.onError).toHaveBeenCalled();
  });

  it("does NOT report connected when the connect fails", async () => {
    // The card then stays at `connecting`, which is the truthful state: a
    // browser opened and we never heard back.
    const deps = makeDeps({
      connect: vi.fn(async () => {
        throw new Error("connector_oauth_denied");
      }),
    } as never);
    const port = createDesktopMcpAuthPort(deps);

    port.beginAuth("seed:linear", { connectorSlug: "linear" });
    await flush();

    expect(deps.onConnected).not.toHaveBeenCalled();
    expect(deps.onError).toHaveBeenCalled();
  });

  it("never throws into the render from a rejected connect", async () => {
    // Every verb is fire-and-forget from the card's perspective.
    const deps = makeDeps({
      connect: vi.fn(async () => {
        throw new Error("boom");
      }),
    } as never);
    const port = createDesktopMcpAuthPort(deps);

    expect(() =>
      port.beginAuth("seed:linear", { connectorSlug: "linear" }),
    ).not.toThrow();
    await flush();
  });

  it("records a deny against the server id", async () => {
    // Skip is server-keyed — it resolves the gate on a row that exists —
    // whereas connect is slug-keyed. The asymmetry is real, not an oversight.
    const deps = makeDeps();
    const port = createDesktopMcpAuthPort(deps);

    port.skipAuth("seed:linear");
    await flush();

    expect(deps.recordSkip).toHaveBeenCalledWith("seed:linear");
  });

  it("swallows a failed skip", async () => {
    // A discovery suggestion has no persisted approval row, so the POST can
    // legitimately 404. The card has already moved to `denied`.
    const deps = makeDeps({
      recordSkip: vi.fn(async () => {
        throw new Error("404");
      }),
    } as never);
    const port = createDesktopMcpAuthPort(deps);

    expect(() => port.skipAuth("seed:linear")).not.toThrow();
    await flush();
    expect(deps.onError).toHaveBeenCalled();
  });

  it("installs and authenticates in one brokered call", async () => {
    // No separate install step: the backend ensures the server row idempotently
    // before starting OAuth, so `installFromCatalog` IS `connect`.
    const deps = makeDeps();
    const port = createDesktopMcpAuthPort(deps);

    port.installFromCatalog("linear");
    await flush();

    expect(deps.connect).toHaveBeenCalledTimes(1);
    expect(deps.connect).toHaveBeenCalledWith("linear");
  });
});
