// WorkspaceGrantPort contract. The shapes matter more than usual here: the
// defect this port exists to fix was a filesystem question answered with an
// empty success, so the outcome union has to make "you cancelled" and "it
// failed" impossible to confuse — and the listing side has to stay path-free.

import { describe, expect, it, vi } from "vitest";

import type {
  WorkspaceGrant,
  WorkspaceGrantOutcome,
  WorkspaceGrantPort,
  WorkspaceGrantRequestInput,
  WorkspaceRevokeOutcome,
} from "./WorkspaceGrantPort";

const DOWNLOADS: WorkspaceGrant = {
  grantId: "grant_01",
  mount: "m_9f2c",
  label: "Downloads",
  mode: "read_only",
  shellEnabled: false,
};

// The path-free property, enforced at COMPILE time rather than asserted over a
// fixture we wrote ourselves. Adding a `path` (or `root`, or `absolutePath`) to
// `WorkspaceGrant` breaks this line, which is the only way this guard can bite:
// a runtime `Object.keys` check on a local literal only ever proves the literal.
type PathFree<T> =
  Extract<keyof T, "path" | "root" | "absolutePath"> extends never
    ? true
    : never;
const grantsCarryNoHostPath: PathFree<WorkspaceGrant> = true;

describe("WorkspaceGrantPort contract", () => {
  it("resolves `granted` with the minted grant when the user picks a folder", async () => {
    const requestGrant = vi.fn(
      async (
        _input?: WorkspaceGrantRequestInput,
      ): Promise<WorkspaceGrantOutcome> => ({
        status: "granted",
        grant: DOWNLOADS,
      }),
    );
    const port: WorkspaceGrantPort = {
      requestGrant,
      listGrants: async () => [DOWNLOADS],
      revokeGrant: async () => ({ status: "revoked" }),
    };

    const outcome = await port.requestGrant();
    expect(outcome.status).toBe("granted");
    if (outcome.status !== "granted") {
      throw new Error("expected a granted outcome");
    }
    expect(outcome.grant).toEqual(DOWNLOADS);
    // No path anywhere in the grant: the read path stays mount + relative. The
    // real guard is the compile-time one above; this just keeps it visible here.
    expect(grantsCarryNoHostPath).toBe(true);
    expect(Object.keys(outcome.grant)).not.toContain("path");
  });

  it("carries a host-absolute path INTO the request (the ask), never back out of it", async () => {
    const requestGrant = vi.fn(
      async (
        _input?: WorkspaceGrantRequestInput,
      ): Promise<WorkspaceGrantOutcome> => ({
        status: "granted",
        grant: DOWNLOADS,
      }),
    );
    const port: WorkspaceGrantPort = {
      requestGrant,
      listGrants: async () => [],
      revokeGrant: async () => ({ status: "revoked" }),
    };

    await port.requestGrant({
      path: "/Users/ada/Downloads",
      mode: "read_only",
      reason: "to list what you downloaded today",
    });
    expect(requestGrant).toHaveBeenCalledWith({
      path: "/Users/ada/Downloads",
      mode: "read_only",
      reason: "to list what you downloaded today",
    });
  });

  it("distinguishes a cancelled ask from a failed one, and a failure names itself", async () => {
    const cancelled: WorkspaceGrantPort = {
      requestGrant: async () => ({ status: "cancelled" }),
      listGrants: async () => [],
      revokeGrant: async () => ({ status: "revoked" }),
    };
    const failed: WorkspaceGrantPort = {
      requestGrant: async () => ({
        status: "failed",
        message: "The folder permission was refused by macOS.",
      }),
      listGrants: async () => [],
      revokeGrant: async () => ({
        status: "failed",
        message: "The broker is not running.",
      }),
    };

    expect(await cancelled.requestGrant()).toEqual({ status: "cancelled" });

    const outcome = await failed.requestGrant();
    expect(outcome.status).toBe("failed");
    if (outcome.status !== "failed") {
      throw new Error("expected a failed outcome");
    }
    expect(outcome.message).toContain("refused");

    const revoke: WorkspaceRevokeOutcome = await failed.revokeGrant("grant_01");
    expect(revoke).toEqual({
      status: "failed",
      message: "The broker is not running.",
    });
  });

  it("lists the CURRENT active set and revokes by grant id", async () => {
    let active: WorkspaceGrant[] = [
      DOWNLOADS,
      {
        grantId: "grant_02",
        mount: "m_31aa",
        label: "notes",
        mode: "read_write",
        shellEnabled: false,
      },
    ];
    const port: WorkspaceGrantPort = {
      requestGrant: async () => ({ status: "cancelled" }),
      listGrants: async () => active,
      revokeGrant: async (grantId) => {
        active = active.filter((grant) => grant.grantId !== grantId);
        return { status: "revoked" };
      },
    };

    expect(await port.listGrants()).toHaveLength(2);
    expect(await port.revokeGrant("grant_01")).toEqual({ status: "revoked" });
    expect((await port.listGrants()).map((grant) => grant.label)).toEqual([
      "notes",
    ]);
  });
});
