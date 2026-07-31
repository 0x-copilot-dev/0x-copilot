// What this file is really testing: that nothing on the desktop folder-grant
// wire can answer with silence.
//
// The defect this subsystem exists to remove was an empty listing returned as
// success, so every case below asserts one of three honest answers — real data,
// an explicit `cancelled`, or a `failed` carrying a message a surface can show.
// The two shapes that must NEVER appear are an empty grant list standing in for
// a failure, and a host path reaching the renderer.

import { afterEach, describe, expect, it, vi } from "vitest";

import { CAPABILITY_CHANNELS } from "../main/capabilities/channels";
import type { WindowBridge } from "../preload/window-bridge-types";
import {
  bridgeWorkspaceGrantPort,
  createDesktopWorkspaceGrantPort,
} from "./workspaceGrantPort";

/** One `RendererGrant` exactly as `main/ipc/handlers.ts` emits it. */
function rendererGrant(
  overrides: Partial<{
    grantId: string;
    mode: string;
    label: string;
    status: string;
  }> = {},
): Record<string, unknown> {
  return {
    grantId: "11111111-1111-4111-8111-111111111111",
    mode: "read_only",
    label: "Downloads",
    status: "active",
    ...overrides,
  };
}

interface Harness {
  readonly bridge: WindowBridge;
  readonly invoke: ReturnType<typeof vi.fn>;
}

/** A bridge whose `invoke` answers per channel; a function answer may throw. */
function harness(answers: Record<string, unknown>): Harness {
  const invoke = vi.fn(async (channel: string, _payload: unknown) => {
    const answer = answers[channel];
    if (typeof answer === "function") {
      return (answer as () => unknown)();
    }
    return answer;
  });
  return {
    bridge: {
      ipc: {
        invoke: invoke as unknown as WindowBridge["ipc"]["invoke"],
        on: () => () => {},
      },
    },
    invoke,
  };
}

function payloadOf(h: Harness, channel: string): unknown {
  const call = h.invoke.mock.calls.find(([c]) => c === channel);
  if (call === undefined) throw new Error(`channel not invoked: ${channel}`);
  return call[1];
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("requestGrant", () => {
  it("mints a grant and reports it, with the grantId as the opaque mount", async () => {
    const h = harness({
      [CAPABILITY_CHANNELS.requestFolderGrant]: rendererGrant(),
    });
    const outcome = await createDesktopWorkspaceGrantPort(
      h.bridge,
    ).requestGrant();
    expect(outcome).toEqual({
      status: "granted",
      grant: {
        grantId: "11111111-1111-4111-8111-111111111111",
        mount: "11111111-1111-4111-8111-111111111111",
        label: "Downloads",
        mode: "read_only",
      },
    });
  });

  it("asks for read-only when the ask named no access", async () => {
    const h = harness({
      [CAPABILITY_CHANNELS.requestFolderGrant]: rendererGrant(),
    });
    await createDesktopWorkspaceGrantPort(h.bridge).requestGrant();
    expect(payloadOf(h, CAPABILITY_CHANNELS.requestFolderGrant)).toEqual({
      mode: "read_only",
    });
  });

  it("forwards the requested mode when the ask named one", async () => {
    const h = harness({
      [CAPABILITY_CHANNELS.requestFolderGrant]: rendererGrant({
        mode: "read_write_no_delete",
      }),
    });
    const outcome = await createDesktopWorkspaceGrantPort(
      h.bridge,
    ).requestGrant({
      mode: "read_write_no_delete",
    });
    expect(payloadOf(h, CAPABILITY_CHANNELS.requestFolderGrant)).toEqual({
      mode: "read_write_no_delete",
    });
    expect(outcome.status).toBe("granted");
  });

  it("sends the asked-for PATH, because that is the folder being agreed to", async () => {
    // The property `capabilities/desktop/workspace_backend.py` states — only
    // mount names and root-relative paths cross to the broker — is a property of
    // the READ path. A grant request is the one place a host-absolute path
    // legitimately appears, and it travels in exactly one direction: toward
    // consent, never toward bytes.
    //
    // It used to be dropped here, which sent "always allow" to a free picker:
    // the user was asked to find the folder again and could land on its parent,
    // and the pill would then claim access to a tree nobody agreed to. Main
    // re-resolves the path, forces read_only and still runs
    // `assertGrantableRoot`, so naming a folder cannot widen what it will grant.
    const h = harness({
      [CAPABILITY_CHANNELS.requestFolderGrant]: rendererGrant(),
    });
    await createDesktopWorkspaceGrantPort(h.bridge).requestGrant({
      path: "/Users/ada/Downloads",
      mode: "read_only",
      reason: "the user asked me to read their downloads",
    });
    const payload = payloadOf(h, CAPABILITY_CHANNELS.requestFolderGrant);
    // `reason` has no home on the channel, and a `label` would WIN over the
    // basename main derives — making a pill read "Downloads" over a grant on
    // Documents. Neither is forwarded.
    expect(payload).toEqual({
      mode: "read_only",
      path: "/Users/ada/Downloads",
    });
  });

  it("still opens the picker when the ask named no folder", async () => {
    // The composer's "attach a folder" button has nothing to name.
    const h = harness({
      [CAPABILITY_CHANNELS.requestFolderGrant]: rendererGrant(),
    });
    await createDesktopWorkspaceGrantPort(h.bridge).requestGrant({
      path: null,
      mode: "read_only",
    });
    expect(payloadOf(h, CAPABILITY_CHANNELS.requestFolderGrant)).toEqual({
      mode: "read_only",
    });
  });

  it("reports a dismissed dialog as cancelled — a decision, not a failure", async () => {
    const h = harness({ [CAPABILITY_CHANNELS.requestFolderGrant]: null });
    const outcome = await createDesktopWorkspaceGrantPort(
      h.bridge,
    ).requestGrant();
    expect(outcome).toEqual({ status: "cancelled" });
  });

  it("fails with a showable message when main answers a shape we can't read", async () => {
    for (const answer of [
      rendererGrant({ mode: "read_everything" }),
      rendererGrant({ grantId: "" }),
      { grantId: "g-1", mode: "read_only", label: "Downloads" }, // no status
      // A host root appearing here would be the leak `RendererGrantSchema`
      // exists to prevent; the strict key check refuses it rather than render it.
      { ...rendererGrant(), root: "/Users/ada/Downloads" },
      "granted",
      [],
    ]) {
      const h = harness({ [CAPABILITY_CHANNELS.requestFolderGrant]: answer });
      const outcome = await createDesktopWorkspaceGrantPort(
        h.bridge,
      ).requestGrant();
      expect(outcome.status).toBe("failed");
      expect(
        outcome.status === "failed" ? outcome.message.length : 0,
      ).toBeGreaterThan(0);
    }
  });

  it("fails rather than claims access when a grant arrives already revoked", async () => {
    const h = harness({
      [CAPABILITY_CHANNELS.requestFolderGrant]: rendererGrant({
        status: "revoked",
      }),
    });
    const outcome = await createDesktopWorkspaceGrantPort(
      h.bridge,
    ).requestGrant();
    expect(outcome.status).toBe("failed");
  });

  it("surfaces the bridge's own error when the channel is not registered", async () => {
    // This is the opted-OUT build: `main/index.ts` leaves `capabilityService`
    // null, so the channel was never handled and Electron rejects the invoke.
    // The user must see a failure — a `cancelled` here would read as "you
    // dismissed a dialog" that was never shown.
    const h = harness({
      [CAPABILITY_CHANNELS.requestFolderGrant]: () => {
        throw new Error(
          "No handler registered for 'capability.request-folder-grant'",
        );
      },
    });
    const outcome = await createDesktopWorkspaceGrantPort(
      h.bridge,
    ).requestGrant();
    expect(outcome).toEqual({
      status: "failed",
      message: "No handler registered for 'capability.request-folder-grant'",
    });
  });
});

describe("listGrants", () => {
  it("returns the ACTIVE grants and drops revoked rows", async () => {
    const h = harness({
      [CAPABILITY_CHANNELS.listGrants]: [
        rendererGrant({ grantId: "g-active", label: "Downloads" }),
        rendererGrant({
          grantId: "g-gone",
          label: "Documents",
          status: "revoked",
        }),
      ],
    });
    const grants = await createDesktopWorkspaceGrantPort(h.bridge).listGrants();
    expect(grants).toEqual([
      {
        grantId: "g-active",
        mount: "g-active",
        label: "Downloads",
        mode: "read_only",
      },
    ]);
  });

  it("returns an empty list ONLY when there really are no grants", async () => {
    const h = harness({ [CAPABILITY_CHANNELS.listGrants]: [] });
    await expect(
      createDesktopWorkspaceGrantPort(h.bridge).listGrants(),
    ).resolves.toEqual([]);
  });

  it("throws instead of answering [] when the reply is not a list", async () => {
    for (const answer of [null, undefined, {}, "[]", 0]) {
      const h = harness({ [CAPABILITY_CHANNELS.listGrants]: answer });
      await expect(
        createDesktopWorkspaceGrantPort(h.bridge).listGrants(),
      ).rejects.toThrow(/shared folders/u);
    }
  });

  it("throws on ONE unreadable row rather than rendering a shorter list", async () => {
    // Skipping the bad row would show fewer folders than the user granted —
    // a quiet false claim about what the agent can reach, in the same family as
    // the empty listing.
    const h = harness({
      [CAPABILITY_CHANNELS.listGrants]: [
        rendererGrant({ grantId: "g-good" }),
        { grantId: "g-bad", mode: "read_only" },
      ],
    });
    await expect(
      createDesktopWorkspaceGrantPort(h.bridge).listGrants(),
    ).rejects.toThrow(/shared folders/u);
  });

  it("throws if a row ever carries a host root", async () => {
    const h = harness({
      [CAPABILITY_CHANNELS.listGrants]: [
        { ...rendererGrant(), root: "/Users/ada/Downloads" },
      ],
    });
    await expect(
      createDesktopWorkspaceGrantPort(h.bridge).listGrants(),
    ).rejects.toThrow(/shared folders/u);
  });

  it("lets a thrown bridge propagate (the opted-out build is a failure, not an empty list)", async () => {
    const h = harness({
      [CAPABILITY_CHANNELS.listGrants]: () => {
        throw new Error("No handler registered for 'capability.list-grants'");
      },
    });
    await expect(
      createDesktopWorkspaceGrantPort(h.bridge).listGrants(),
    ).rejects.toThrow(/No handler registered/u);
  });
});

describe("revokeGrant", () => {
  it("revokes by id and confirms the end state", async () => {
    const h = harness({
      [CAPABILITY_CHANNELS.revokeGrant]: rendererGrant({
        grantId: "g-1",
        status: "revoked",
      }),
    });
    const outcome = await createDesktopWorkspaceGrantPort(h.bridge).revokeGrant(
      "g-1",
    );
    expect(payloadOf(h, CAPABILITY_CHANNELS.revokeGrant)).toEqual({
      grantId: "g-1",
    });
    expect(outcome).toEqual({ status: "revoked" });
  });

  it("treats an unknown id as revoked (idempotent — the end state is the asked-for one)", async () => {
    const h = harness({ [CAPABILITY_CHANNELS.revokeGrant]: null });
    await expect(
      createDesktopWorkspaceGrantPort(h.bridge).revokeGrant("g-gone"),
    ).resolves.toEqual({ status: "revoked" });
  });

  it("fails when the grant comes back still active", async () => {
    const h = harness({
      [CAPABILITY_CHANNELS.revokeGrant]: rendererGrant({ status: "active" }),
    });
    const outcome = await createDesktopWorkspaceGrantPort(h.bridge).revokeGrant(
      "g-1",
    );
    expect(outcome.status).toBe("failed");
    expect(outcome.status === "failed" ? outcome.message : "").toMatch(
      /still shared/u,
    );
  });

  it("fails with a showable message on an unreadable answer or a thrown bridge", async () => {
    const bad = harness({ [CAPABILITY_CHANNELS.revokeGrant]: { grantId: 1 } });
    expect(
      (await createDesktopWorkspaceGrantPort(bad.bridge).revokeGrant("g-1"))
        .status,
    ).toBe("failed");

    const thrown = harness({
      [CAPABILITY_CHANNELS.revokeGrant]: () => {
        throw new Error("bridge gone");
      },
    });
    expect(
      await createDesktopWorkspaceGrantPort(thrown.bridge).revokeGrant("g-1"),
    ).toEqual({ status: "failed", message: "bridge gone" });
  });
});

describe("bridgeWorkspaceGrantPort", () => {
  const win = globalThis.window as unknown as { bridge?: WindowBridge };

  afterEach(() => {
    delete win.bridge;
  });

  it("is undefined with no Electron bridge (web / MockTransport dev)", () => {
    delete win.bridge;
    expect(bridgeWorkspaceGrantPort()).toBeUndefined();
  });

  it("returns the SAME port for the same bridge, so hooks don't re-read on every render", () => {
    win.bridge = harness({}).bridge;
    const first = bridgeWorkspaceGrantPort();
    expect(first).toBeDefined();
    expect(bridgeWorkspaceGrantPort()).toBe(first);
  });

  it("rebinds when the bridge itself changes (no stale closure)", () => {
    win.bridge = harness({}).bridge;
    const first = bridgeWorkspaceGrantPort();
    const second = harness({
      [CAPABILITY_CHANNELS.listGrants]: [],
    });
    win.bridge = second.bridge;
    const rebound = bridgeWorkspaceGrantPort();
    expect(rebound).not.toBe(first);
    // And it talks to the NEW bridge.
    void rebound?.listGrants();
    expect(second.invoke).toHaveBeenCalledWith(
      CAPABILITY_CHANNELS.listGrants,
      {},
    );
  });
});
