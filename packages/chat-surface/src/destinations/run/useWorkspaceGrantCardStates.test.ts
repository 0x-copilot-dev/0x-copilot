// useWorkspaceGrantCardStates — the sequencing that makes the ask mean something.
//
// The load-bearing assertions are the negative ones: a cancelled dialog and a
// failed grant must NOT call `onGranted`, because `onGranted` is what resumes the
// run. Resuming without a grant is the defect in its original form — the read
// proceeds, finds nothing it is allowed to see, and reports an empty folder.

import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type {
  WorkspaceGrant,
  WorkspaceGrantOutcome,
  WorkspaceGrantPort,
} from "../../ports/WorkspaceGrantPort";
import type { WorkspaceGrantRequest } from "../../approvals/presentation";
import { useWorkspaceGrantCardStates } from "./useWorkspaceGrantCardStates";

const DOWNLOADS: WorkspaceGrant = {
  grantId: "grant_01",
  mount: "m_9f2c",
  label: "Downloads",
  mode: "read_only",
};

const ASK: WorkspaceGrantRequest = {
  path: "/Users/parthpahwa/Downloads",
  folderName: "Downloads",
  mode: "read_only",
  reason: "to see what you downloaded today",
};

function makePort(
  outcome: WorkspaceGrantOutcome | (() => Promise<never>),
): WorkspaceGrantPort {
  return {
    requestGrant: vi.fn(
      typeof outcome === "function" ? outcome : async () => outcome,
    ),
    listGrants: vi.fn(async () => []),
    revokeGrant: vi.fn(async () => ({ status: "revoked" as const })),
  };
}

describe("useWorkspaceGrantCardStates", () => {
  it("sends the ask's path INTO the grant request, and resumes the run on success", async () => {
    const port = makePort({ status: "granted", grant: DOWNLOADS });
    const onGranted = vi.fn();
    const { result } = renderHook(() =>
      useWorkspaceGrantCardStates(port, { onGranted }),
    );

    act(() => result.current.grant("appr-fs-1", ASK));
    // Optimistic: the OS dialog is up, so the card must not look inert.
    expect(result.current.states["appr-fs-1"]).toBe("granting");

    await waitFor(() =>
      expect(result.current.states["appr-fs-1"]).toBe("granted"),
    );
    expect(port.requestGrant).toHaveBeenCalledWith({
      path: "/Users/parthpahwa/Downloads",
      mode: "read_only",
      reason: "to see what you downloaded today",
    });
    expect(onGranted).toHaveBeenCalledWith("appr-fs-1", DOWNLOADS);
  });

  it("returns to pending on a cancelled dialog, and does NOT resume the run", async () => {
    const port = makePort({ status: "cancelled" });
    const onGranted = vi.fn();
    const { result } = renderHook(() =>
      useWorkspaceGrantCardStates(port, { onGranted }),
    );

    act(() => result.current.grant("appr-fs-1", ASK));
    await waitFor(() =>
      expect(result.current.states["appr-fs-1"]).toBe("pending"),
    );
    expect(onGranted).not.toHaveBeenCalled();
    expect(result.current.failures["appr-fs-1"]).toBeUndefined();
  });

  it("keeps a failure showable and the run paused", async () => {
    const port = makePort({
      status: "failed",
      message: "macOS refused access to that folder.",
    });
    const onGranted = vi.fn();
    const { result } = renderHook(() =>
      useWorkspaceGrantCardStates(port, { onGranted }),
    );

    act(() => result.current.grant("appr-fs-1", ASK));
    await waitFor(() =>
      expect(result.current.states["appr-fs-1"]).toBe("failed"),
    );
    expect(result.current.failures["appr-fs-1"]).toBe(
      "macOS refused access to that folder.",
    );
    expect(onGranted).not.toHaveBeenCalled();
  });

  it("turns a thrown port into a sentence, not a stalled card", async () => {
    const port = makePort(async () => {
      throw new Error("The capability broker is not running.");
    });
    const { result } = renderHook(() => useWorkspaceGrantCardStates(port));

    act(() => result.current.grant("appr-fs-1", ASK));
    await waitFor(() =>
      expect(result.current.states["appr-fs-1"]).toBe("failed"),
    );
    expect(result.current.failures["appr-fs-1"]).toBe(
      "The capability broker is not running.",
    );
  });

  it("clears the previous failure when the user tries again", async () => {
    let outcome: WorkspaceGrantOutcome = {
      status: "failed",
      message: "The disk went away.",
    };
    const port: WorkspaceGrantPort = {
      requestGrant: vi.fn(async () => outcome),
      listGrants: vi.fn(async () => []),
      revokeGrant: vi.fn(async () => ({ status: "revoked" as const })),
    };
    const { result } = renderHook(() => useWorkspaceGrantCardStates(port));

    act(() => result.current.grant("appr-fs-1", ASK));
    await waitFor(() =>
      expect(result.current.failures["appr-fs-1"]).toBe("The disk went away."),
    );

    outcome = { status: "granted", grant: DOWNLOADS };
    act(() => result.current.grant("appr-fs-1", ASK));
    await waitFor(() =>
      expect(result.current.states["appr-fs-1"]).toBe("granted"),
    );
    expect(result.current.failures["appr-fs-1"]).toBeUndefined();
  });

  it("routes a decline through onDenied, and Cancel is local only", () => {
    const port = makePort({ status: "granted", grant: DOWNLOADS });
    const onDenied = vi.fn();
    const { result } = renderHook(() =>
      useWorkspaceGrantCardStates(port, { onDenied }),
    );

    act(() => result.current.deny("appr-fs-1"));
    expect(result.current.states["appr-fs-1"]).toBe("denied");
    expect(onDenied).toHaveBeenCalledWith("appr-fs-1");

    act(() => result.current.cancel("appr-fs-1"));
    expect(result.current.states["appr-fs-1"]).toBe("pending");
    // Cancel is not a port verb — the OS dialog has no abort.
    expect(port.requestGrant).not.toHaveBeenCalled();
  });

  it("claims nothing when no port is wired", () => {
    const onGranted = vi.fn();
    const { result } = renderHook(() =>
      useWorkspaceGrantCardStates(null, { onGranted }),
    );

    act(() => result.current.grant("appr-fs-1", ASK));
    // Never strands the card on `granting` waiting for a dialog nobody opened.
    expect(result.current.states["appr-fs-1"]).toBeUndefined();
    expect(onGranted).not.toHaveBeenCalled();
  });

  it("omits `mode` from the request when the ask named none", async () => {
    const port = makePort({ status: "granted", grant: DOWNLOADS });
    const { result } = renderHook(() => useWorkspaceGrantCardStates(port));

    act(() => result.current.grant("appr-fs-1", { ...ASK, mode: null }));
    await waitFor(() => expect(port.requestGrant).toHaveBeenCalled());
    // Absent, never `null`-as-a-mode: the host applies its own default
    // (read-only) rather than being handed an access nobody asked for.
    expect(port.requestGrant).toHaveBeenCalledWith({
      path: "/Users/parthpahwa/Downloads",
      reason: "to see what you downloaded today",
    });
  });
});
