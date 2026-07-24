// @vitest-environment node
import { describe, expect, it, vi } from "vitest";

import type { NativeWorkspaceFs } from "../../native/workspace-fs";
import {
  AddonNativeWorkspaceAuthority,
  UnavailableNativeWorkspaceAuthority,
  hasNativeWorkspaceV2Bindings,
  type NativeWorkspaceV2Bindings,
} from "./native-workspace-authority";

const ENTRY = {
  operation: "create" as const,
  relativePath: "notes.md",
  contentDigest: "a".repeat(64),
  contentSize: 1,
  contentSlot: "slot_1",
  precondition: { exists: false },
};

function bindings(): NativeWorkspaceV2Bindings {
  return {
    platform: process.platform,
    openBeneath: vi.fn(() => 1),
    workspaceRootIdentity: vi.fn(() => ({
      volumeId: "vol_1",
      fileId: "root_1",
    })),
    workspacePrepare: vi.fn(() => ({
      handle: "native_1",
      observedTargetDigest: "b".repeat(64),
      slots: [{ slot: "slot_1", digest: "a".repeat(64), size: 1 }],
    })),
    workspaceWrite: vi.fn(),
    workspaceSeal: vi.fn(() => ({ digest: "a".repeat(64), size: 1 })),
    workspaceCommit: vi.fn(() => ({
      outcome: "applied" as const,
      receiptRef: "workspace-receipt://claim_1",
    })),
    workspaceReconcile: vi.fn(() => ({
      outcome: "already_applied" as const,
      receiptRef: "workspace-receipt://claim_1",
    })),
    workspaceReconcileClaim: vi.fn(() => ({
      outcome: "already_applied" as const,
      receiptRef: "workspace-receipt://claim_1",
    })),
    workspaceAbort: vi.fn(),
    workspaceProposeRecovery: vi.fn(() => "proposed" as const),
    workspaceProposeRecoveryClaim: vi.fn(() => "conflict" as const),
  };
}

describe("native workspace v2 authority adapter", () => {
  it("requires the full native lifecycle before declaring writes available", () => {
    const legacy: NativeWorkspaceFs = {
      platform: process.platform,
      openBeneath: () => 1,
    };
    expect(hasNativeWorkspaceV2Bindings(legacy)).toBe(false);
    expect(hasNativeWorkspaceV2Bindings(bindings())).toBe(true);
  });

  it("has no Node filesystem fallback when native v2 primitives are absent", async () => {
    const authority = new UnavailableNativeWorkspaceAuthority();
    expect(authority.primitivesAvailable).toBe(false);
    await expect(authority.prepare("/never-used", [ENTRY])).rejects.toThrow(
      "native workspace primitives are unavailable",
    );
  });

  it("forwards only opaque handles and validated bytes to the native addon", async () => {
    const addon = bindings();
    const authority = new AddonNativeWorkspaceAuthority(addon);
    const prepared = await authority.prepare("/main-only-root", [ENTRY]);
    await authority.writePrepared(prepared, "slot_1", Buffer.from("x"));
    await authority.sealPrepared(prepared, "slot_1");
    await authority.commitPrepared(prepared, "claim_1");
    await authority.abortPrepared(prepared);

    expect(addon.workspacePrepare).toHaveBeenCalledWith("/main-only-root", [
      ENTRY,
    ]);
    expect(addon.workspaceWrite).toHaveBeenCalledWith(
      "native_1",
      "slot_1",
      Buffer.from("x"),
    );
    expect(addon.workspaceCommit).toHaveBeenCalledWith("native_1", "claim_1");
    expect(addon.workspaceAbort).toHaveBeenCalledWith("native_1");
  });
});
