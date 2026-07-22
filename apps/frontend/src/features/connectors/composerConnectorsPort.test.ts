// Web ComposerConnectorsPort — thin adapter over api/mcpApi. Confirms each port
// method delegates to the right facade route (identity threaded through), that
// listCatalog unwraps `.entries`, and that beginAuth full-page-redirects to the
// returned auth_url (mirroring useConnectors.authenticate).

import { beforeEach, describe, expect, it, vi } from "vitest";

import type {
  McpAuthStartResponse,
  McpCatalogEntry,
  McpCatalogResponse,
  McpServer,
} from "@0x-copilot/api-types";

vi.mock("../../api/mcpApi", () => ({
  listMcpServers: vi.fn(),
  listMcpCatalog: vi.fn(),
  installMcpServer: vi.fn(),
  createMcpServer: vi.fn(),
  startMcpAuth: vi.fn(),
}));

import {
  createMcpServer,
  installMcpServer,
  listMcpCatalog,
  listMcpServers,
  startMcpAuth,
} from "../../api/mcpApi";
import { createComposerConnectorsPort } from "./composerConnectorsPort";

const IDENTITY = { orgId: "org_1", userId: "user_1" };

const SERVER: McpServer = {
  server_id: "srv-1",
  display_name: "GitHub",
  url: "https://mcp.example.com",
  transport: "http",
  auth_mode: "oauth2",
  enabled: true,
} as unknown as McpServer;

const CATALOG_ENTRY = { slug: "github" } as unknown as McpCatalogEntry;

describe("createComposerConnectorsPort", () => {
  beforeEach(() => {
    vi.mocked(listMcpServers).mockReset();
    vi.mocked(listMcpCatalog).mockReset();
    vi.mocked(installMcpServer).mockReset();
    vi.mocked(createMcpServer).mockReset();
    vi.mocked(startMcpAuth).mockReset();
  });

  it("listServers delegates to listMcpServers with identity", async () => {
    vi.mocked(listMcpServers).mockResolvedValue([SERVER]);
    const port = createComposerConnectorsPort(IDENTITY);
    await expect(port.listServers()).resolves.toEqual([SERVER]);
    expect(listMcpServers).toHaveBeenCalledWith(IDENTITY);
  });

  it("listCatalog unwraps the response `.entries`", async () => {
    vi.mocked(listMcpCatalog).mockResolvedValue({
      entries: [CATALOG_ENTRY],
    } as McpCatalogResponse);
    const port = createComposerConnectorsPort(IDENTITY);
    await expect(port.listCatalog()).resolves.toEqual([CATALOG_ENTRY]);
  });

  it("installFromCatalog delegates by slug with identity", async () => {
    vi.mocked(installMcpServer).mockResolvedValue(SERVER);
    const port = createComposerConnectorsPort(IDENTITY);
    await port.installFromCatalog("github");
    expect(installMcpServer).toHaveBeenCalledWith(
      "github",
      IDENTITY,
      undefined,
    );
  });

  it("addCustomServer delegates by url with identity", async () => {
    vi.mocked(createMcpServer).mockResolvedValue(SERVER);
    const port = createComposerConnectorsPort(IDENTITY);
    await port.addCustomServer("https://mcp.example.com");
    expect(createMcpServer).toHaveBeenCalledWith(
      "https://mcp.example.com",
      IDENTITY,
      undefined,
    );
  });

  it("beginAuth starts OAuth and redirects to the returned auth_url", async () => {
    vi.mocked(startMcpAuth).mockResolvedValue({
      auth_url: "https://vendor.example.com/oauth",
    } as McpAuthStartResponse);
    // jsdom `location.href` is not directly assignable across all versions;
    // redefine it so the assignment inside beginAuth is observable.
    const original = window.location;
    Object.defineProperty(window, "location", {
      configurable: true,
      value: { ...original, href: "" },
    });
    try {
      const port = createComposerConnectorsPort(IDENTITY);
      await port.beginAuth("srv-1");
      expect(startMcpAuth).toHaveBeenCalledWith("srv-1", IDENTITY);
      expect(window.location.href).toBe("https://vendor.example.com/oauth");
    } finally {
      Object.defineProperty(window, "location", {
        configurable: true,
        value: original,
      });
    }
  });
});
