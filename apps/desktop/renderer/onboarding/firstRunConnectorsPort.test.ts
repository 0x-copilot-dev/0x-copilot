// @vitest-environment node
import { describe, expect, it, vi } from "vitest";

import type { Transport, TypedRequest } from "@0x-copilot/chat-transport";

import { createFirstRunConnectorsPort } from "./firstRunConnectorsPort";

interface Recorded {
  readonly method: string;
  readonly path: string;
  readonly body: unknown;
}

function fakeTransport(byPath: Record<string, unknown>): {
  readonly transport: Transport;
  readonly calls: Recorded[];
} {
  const calls: Recorded[] = [];
  const request = vi.fn(async (req: TypedRequest) => {
    calls.push({ method: req.method, path: req.path, body: req.body });
    if (!(req.path in byPath)) {
      throw new Error(`unexpected path ${req.path}`);
    }
    return byPath[req.path];
  });
  return { transport: { request } as unknown as Transport, calls };
}

describe("createFirstRunConnectorsPort", () => {
  it("listServers unwraps {servers}, degrades null → []", async () => {
    const { transport, calls } = fakeTransport({
      "/v1/mcp/servers": { servers: [{ server_id: "seed:sheets" }] },
    });
    const servers = await createFirstRunConnectorsPort(transport).listServers();
    expect(servers).toEqual([{ server_id: "seed:sheets" }]);
    expect(calls[0]).toMatchObject({ method: "GET", path: "/v1/mcp/servers" });

    const empty = fakeTransport({ "/v1/mcp/servers": null });
    expect(
      await createFirstRunConnectorsPort(empty.transport).listServers(),
    ).toEqual([]);
  });

  it("listCatalog unwraps {entries}, degrades null → []", async () => {
    const { transport } = fakeTransport({
      "/v1/mcp/catalog": { entries: [{ slug: "safe" }] },
    });
    expect(await createFirstRunConnectorsPort(transport).listCatalog()).toEqual(
      [{ slug: "safe" }],
    );

    const empty = fakeTransport({ "/v1/mcp/catalog": null });
    expect(
      await createFirstRunConnectorsPort(empty.transport).listCatalog(),
    ).toEqual([]);
  });

  it("installFromCatalog POSTs a keyless {slug} (no identity)", async () => {
    const { transport, calls } = fakeTransport({
      "/v1/mcp/servers/install": { server_id: "seed:safe" },
    });
    await createFirstRunConnectorsPort(transport).installFromCatalog("safe");
    expect(calls[0]).toEqual({
      method: "POST",
      path: "/v1/mcp/servers/install",
      body: { slug: "safe" },
    });
  });

  it("addCustomServer POSTs {url} (+ oauth_client when supplied)", async () => {
    const { transport, calls } = fakeTransport({
      "/v1/mcp/servers": { server_id: "custom_1" },
    });
    await createFirstRunConnectorsPort(transport).addCustomServer(
      "https://mcp.test/sse",
      { client_id: "abc" },
    );
    expect(calls[0]).toEqual({
      method: "POST",
      path: "/v1/mcp/servers",
      body: { url: "https://mcp.test/sse", oauth_client: { client_id: "abc" } },
    });
  });

  it("beginAuth POSTs the encoded auth/start path", async () => {
    const { transport, calls } = fakeTransport({
      "/v1/mcp/servers/seed%3Asafe/auth/start": {
        server_id: "seed:safe",
        auth_url: "https://auth.test",
        expires_at: "2026-01-01T00:00:00Z",
      },
    });
    await createFirstRunConnectorsPort(transport).beginAuth("seed:safe");
    expect(calls[0]).toEqual({
      method: "POST",
      path: "/v1/mcp/servers/seed%3Asafe/auth/start",
      body: {},
    });
  });
});
