// The desktop connector consent port.
//
// Before this existed, `mcpAuthPort` was undefined on desktop, so the card's
// `actionable` was false and Connect/Deny were disabled. Nothing failed — which
// is exactly why it went unnoticed. These tests pin the behaviours that would
// regress silently the same way.
//
// The earlier version of this file asserted the port "connects by SLUG, not
// server id" because "there is no server-keyed entry point to call". That was
// false — `connector.authorize-server` existed — and believing it is what made
// Connect a dead button for every catalog seed. The port now sends BOTH
// identities and main picks the route, so the tests pin THAT instead.

import { describe, expect, it, vi } from "vitest";

import {
  createDesktopMcpAuthPort,
  type DesktopMcpAuthPortDeps,
} from "./desktopMcpAuthPort";

function makeDeps(
  overrides: Partial<DesktopMcpAuthPortDeps> = {},
): DesktopMcpAuthPortDeps & {
  authorize: ReturnType<typeof vi.fn>;
  recordSkip: ReturnType<typeof vi.fn>;
  onConnected: ReturnType<typeof vi.fn>;
  onError: ReturnType<typeof vi.fn>;
} {
  const deps = {
    authorize: vi.fn(async (target: { slug?: string; serverId?: string }) => ({
      server_id: target.serverId ?? `seed:${target.slug ?? ""}`,
      connector_slug: target.slug ?? null,
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
  it("sends BOTH identities so main can pick the OAuth route", async () => {
    // The regression that made this port worth rewriting. Linear is a catalog
    // seed with no `desktop_profiles.yaml` entry, so a slug-only request went
    // down the profile route and 404'd before any browser opened. Passing the
    // server id too is what lets main authorize it over MCP OAuth instead.
    const deps = makeDeps();
    const port = createDesktopMcpAuthPort(deps);

    port.beginAuth("seed:linear", { connectorSlug: "linear" });
    await flush();

    expect(deps.authorize).toHaveBeenCalledWith({
      serverId: "seed:linear",
      slug: "linear",
    });
  });

  it("authorizes a gate that names no connector, by server id alone", async () => {
    // A custom MCP server added by URL has no catalog identity. That used to be
    // refused outright, on the reasoning that a slug-keyed call would 404 — but
    // the server id IS a usable identity on the MCP route, so the honest answer
    // is to authorize with it rather than to give up.
    const deps = makeDeps();
    const port = createDesktopMcpAuthPort(deps);

    port.beginAuth("custom:abc123", { connectorSlug: null });
    await flush();

    expect(deps.authorize).toHaveBeenCalledWith({ serverId: "custom:abc123" });
    expect(deps.onError).not.toHaveBeenCalled();
  });

  it("treats a missing options bag the same as an absent slug", async () => {
    const deps = makeDeps();
    const port = createDesktopMcpAuthPort(deps);

    port.beginAuth("custom:abc123");
    await flush();

    expect(deps.authorize).toHaveBeenCalledWith({ serverId: "custom:abc123" });
  });

  it("reports the connected server id main confirmed", async () => {
    // For an uninstalled suggestion the server row is minted DURING the
    // authorize, so the id the card knew is not necessarily the one that exists
    // afterwards. The consent machine is keyed by server id, so using the stale
    // one would leave the card stuck at `connecting`.
    const deps = makeDeps({
      authorize: vi.fn(async () => ({
        server_id: "seed:notion",
        connector_slug: "notion",
        auth_state: "authenticated",
      })),
    } as never);
    const port = createDesktopMcpAuthPort(deps);

    port.installFromCatalog("notion");
    await flush();

    expect(deps.onConnected).toHaveBeenCalledWith("seed:notion");
  });

  it("reports a failure against the id the CARD is keyed by", async () => {
    // The card moved to `connecting` on click, keyed by the gate's server id.
    // Reporting a failure under any other id would leave that card claiming a
    // consent screen is open forever — the exact silent-failure shape this
    // whole change exists to remove.
    const deps = makeDeps({
      authorize: vi.fn(async () => {
        throw new Error("connector_profile_unavailable");
      }),
    } as never);
    const port = createDesktopMcpAuthPort(deps);

    port.beginAuth("seed:linear", { connectorSlug: "linear" });
    await flush();

    expect(deps.onConnected).not.toHaveBeenCalled();
    expect(deps.onError).toHaveBeenCalledWith(
      "seed:linear",
      expect.objectContaining({ message: "connector_profile_unavailable" }),
    );
  });

  it("never throws into the render from a rejected authorize", async () => {
    // Every verb is fire-and-forget from the card's perspective.
    const deps = makeDeps({
      authorize: vi.fn(async () => {
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
    expect(deps.onError).toHaveBeenCalledWith("seed:linear", expect.anything());
  });

  it("installs and authenticates in one brokered call", async () => {
    // No separate install step: the backend ensures the server row idempotently
    // before starting OAuth, so `installFromCatalog` IS an authorize. There is
    // no server id yet, so the slug goes over alone.
    const deps = makeDeps();
    const port = createDesktopMcpAuthPort(deps);

    port.installFromCatalog("linear");
    await flush();

    expect(deps.authorize).toHaveBeenCalledTimes(1);
    expect(deps.authorize).toHaveBeenCalledWith({ slug: "linear" });
  });
});
