// @vitest-environment node
import { describe, expect, it } from "vitest";

import {
  BrowserReadAuthorityError,
  MainBrowserReadAuthority,
} from "./read-authority";

const POLICY = {
  version: 1 as const,
  topLevelOrigins: ["https://example.com"],
  subresourceOrigins: ["https://static.example.com"],
  denyPrivateNetworks: true as const,
  serviceWorkers: "block" as const,
};

describe("MainBrowserReadAuthority", () => {
  it("mints only short-lived ephemeral bindings from main-owned policy", () => {
    const authority = new MainBrowserReadAuthority({
      originPolicy: POLICY,
      now: () => 10_000,
      bindingTtlMs: 60_000,
      randomBytes: () => Buffer.alloc(24, 9),
    });

    const binding = authority.resolveBinding({
      runId: "run-1",
      workspaceId: "workspace-1",
    });

    expect(binding.profileMode).toBe("ephemeral");
    expect(binding.profileId).toBe("ephemeral");
    expect(binding.originPolicy).toEqual(POLICY);
    expect(binding.approvalId).toMatch(/^browser-origin-policy:[a-f0-9]{64}$/u);
    expect(binding.expiresAt).toBe(new Date(70_000).toISOString());
    expect(binding.nonce).not.toContain("workspace-1");
  });

  it("pins a run to one workspace until revoked", () => {
    const authority = new MainBrowserReadAuthority({
      originPolicy: POLICY,
      now: () => 10_000,
    });
    authority.resolveBinding({ runId: "run-1", workspaceId: "workspace-1" });

    expect(() =>
      authority.resolveBinding({
        runId: "run-1",
        workspaceId: "workspace-2",
      }),
    ).toThrow(BrowserReadAuthorityError);

    authority.revoke("run-1");
    expect(
      authority.resolveBinding({
        runId: "run-1",
        workspaceId: "workspace-2",
      }).workspaceId,
    ).toBe("workspace-2");
  });

  it("rejects whitespace-shaped or empty scope ids", () => {
    const authority = new MainBrowserReadAuthority({
      originPolicy: POLICY,
    });
    expect(() =>
      authority.resolveBinding({ runId: " run-1", workspaceId: "workspace" }),
    ).toThrow(BrowserReadAuthorityError);
    expect(() =>
      authority.resolveBinding({ runId: "run-1", workspaceId: "" }),
    ).toThrow(BrowserReadAuthorityError);
  });
});
