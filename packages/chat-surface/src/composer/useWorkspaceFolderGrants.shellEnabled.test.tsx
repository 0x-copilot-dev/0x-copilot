// PRD-shell-execution §7.3 — the hook half of the per-workspace command flag.
//
// The hook is the seam between "the host can do this" and "the surface draws a
// control for it", and it is also the last place that can turn a host's refusal
// into a visible sentence. Both are asserted here, because the failure mode of
// getting either wrong is the same: a toggle that looks on over a permission
// nothing recorded.

import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type {
  WorkspaceGrant,
  WorkspaceGrantPort,
  WorkspaceShellAccessOutcome,
} from "../ports/WorkspaceGrantPort";

import { useWorkspaceFolderGrants } from "./useWorkspaceFolderGrants";

function grant(shellEnabled = false): WorkspaceGrant {
  return {
    grantId: "g_atlas",
    mount: "m_atlas",
    label: "atlas",
    mode: "read_write",
    shellEnabled,
  };
}

/** A port WITHOUT `setShellEnabled` — the shape web and an old desktop have. */
function portWithoutShell(): WorkspaceGrantPort {
  return {
    requestGrant: vi.fn(async () => ({
      status: "granted" as const,
      grant: grant(),
    })),
    listGrants: vi.fn(async () => [grant()]),
    revokeGrant: vi.fn(async () => ({ status: "revoked" as const })),
  };
}

function portWithShell(
  outcome: WorkspaceShellAccessOutcome | (() => Promise<never>),
): WorkspaceGrantPort & { setShellEnabled: ReturnType<typeof vi.fn> } {
  const setShellEnabled = vi.fn(
    typeof outcome === "function" ? outcome : async () => outcome,
  );
  return { ...portWithoutShell(), setShellEnabled };
}

describe("useWorkspaceFolderGrants — shell enablement", () => {
  it("yields NULL when the host has no setShellEnabled", async () => {
    // Null, not a no-op function. A no-op would let a surface render a toggle
    // that silently does nothing; null forces the caller to answer "can this
    // host do it at all?" before it draws the control.
    const { result } = renderHook(() =>
      useWorkspaceFolderGrants(portWithoutShell()),
    );
    await waitFor(() => expect(result.current.grants).toHaveLength(1));
    expect(result.current.setShellEnabled).toBeNull();
  });

  it("yields null for a null port too", async () => {
    const { result } = renderHook(() => useWorkspaceFolderGrants(null));
    expect(result.current.setShellEnabled).toBeNull();
  });

  it("forwards the exact grantId and value, then re-reads the list", async () => {
    const port = portWithShell({ status: "ok", applied: true });
    const { result } = renderHook(() => useWorkspaceFolderGrants(port));
    await waitFor(() => expect(result.current.setShellEnabled).not.toBeNull());

    await act(async () => {
      await result.current.setShellEnabled?.("g_atlas", true);
    });

    expect(port.setShellEnabled).toHaveBeenCalledExactlyOnceWith(
      "g_atlas",
      true,
    );
    expect(result.current.error).toBeNull();
    // Re-read, so the rendered state is the host's and not our optimism.
    expect(port.listGrants).toHaveBeenCalledTimes(2);
  });

  it("surfaces a host failure as a sentence", async () => {
    const port = portWithShell({
      status: "failed",
      message: "That folder is no longer shared with the agent.",
    });
    const { result } = renderHook(() => useWorkspaceFolderGrants(port));
    await waitFor(() => expect(result.current.setShellEnabled).not.toBeNull());

    await act(async () => {
      await result.current.setShellEnabled?.("g_atlas", true);
    });

    expect(result.current.error).toBe(
      "That folder is no longer shared with the agent.",
    );
  });

  it("says so when the host applied something OTHER than what was asked", async () => {
    // The defect this exists to prevent: main refuses to enable commands on a
    // revoked or expired grant and answers with the record UNCHANGED, which is
    // `status: "ok"`. Reading only the status reports that refusal as a success,
    // and the toggle snaps back with no explanation — a UI glitch the user
    // would reasonably read as "it worked, the render is just slow".
    const port = portWithShell({ status: "ok", applied: false });
    const { result } = renderHook(() => useWorkspaceFolderGrants(port));
    await waitFor(() => expect(result.current.setShellEnabled).not.toBeNull());

    await act(async () => {
      await result.current.setShellEnabled?.("g_atlas", true);
    });

    expect(result.current.error).toMatch(/Couldn't allow commands/i);
  });

  it("an agreeing outcome sets no error", async () => {
    // The mirror of the test above, so it cannot pass by the error being set
    // unconditionally.
    const port = portWithShell({ status: "ok", applied: false });
    const { result } = renderHook(() => useWorkspaceFolderGrants(port));
    await waitFor(() => expect(result.current.setShellEnabled).not.toBeNull());

    await act(async () => {
      await result.current.setShellEnabled?.("g_atlas", false);
    });

    expect(result.current.error).toBeNull();
  });

  it("a thrown port call leaves an error and clears busy", async () => {
    const port = portWithShell(async () => {
      throw new Error("bridge is gone");
    });
    const { result } = renderHook(() => useWorkspaceFolderGrants(port));
    await waitFor(() => expect(result.current.setShellEnabled).not.toBeNull());

    await act(async () => {
      await result.current.setShellEnabled?.("g_atlas", true);
    });

    expect(result.current.error).toBe("bridge is gone");
    expect(result.current.busy).toBe(false);
  });
});
