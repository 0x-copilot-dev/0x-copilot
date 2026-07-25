import { describe, expect, it, vi } from "vitest";

import { CAPABILITY_CHANNELS } from "../main/capabilities/channels";
import type { WindowBridge } from "../preload/window-bridge-types";
import {
  DesktopWorkspaceApprovalPortError,
  createDesktopWorkspaceApprovalHostPort,
} from "./workspaceApprovalPort";

const SNAPSHOT = Object.freeze({
  runId: "run_workspace_1",
  stageId: "stage_workspace_1",
  revision: 2,
  proposalDigest: "a".repeat(64),
  targetDigest: "b".repeat(64),
});

function bridgeFor(result: unknown): {
  readonly bridge: WindowBridge;
  readonly invoke: ReturnType<typeof vi.fn>;
} {
  const invoke = vi.fn();
  const invokeBridge = async <T = unknown>(
    channel: string,
    payload: unknown,
  ): Promise<T> => {
    invoke(channel, payload);
    return result as T;
  };
  return {
    bridge: {
      ipc: {
        invoke: invokeBridge,
        on: () => () => {},
      },
    },
    invoke,
  };
}

describe("createDesktopWorkspaceApprovalHostPort", () => {
  it("invokes the existing narrow channel with only the digest-pinned snapshot", async () => {
    const { bridge, invoke } = bridgeFor({
      stageId: SNAPSHOT.stageId,
      revision: SNAPSHOT.revision,
      decision: "approve",
      status: "approved",
    });
    const port = createDesktopWorkspaceApprovalHostPort(bridge);

    await expect(
      port.decide({ snapshot: SNAPSHOT, decision: "approve" }),
    ).resolves.toEqual({
      stageId: SNAPSHOT.stageId,
      revision: SNAPSHOT.revision,
      decision: "approve",
      status: "approved",
    });

    expect(invoke).toHaveBeenCalledTimes(1);
    expect(invoke).toHaveBeenCalledWith(
      CAPABILITY_CHANNELS.decideWorkspaceApproval,
      {
        snapshot: SNAPSHOT,
        decision: "approve",
      },
    );
    const outbound = JSON.stringify(invoke.mock.calls[0]?.[1]);
    expect(outbound).not.toMatch(/path|root|permit|prepared|proposal_ref/i);
  });

  it("rejects a malformed or over-broad main response before it reaches the shared UI", async () => {
    const { bridge } = bridgeFor({
      stageId: SNAPSHOT.stageId,
      revision: SNAPSHOT.revision,
      decision: "approve",
      status: "approved",
      permit: "private-permit",
    });

    await expect(
      createDesktopWorkspaceApprovalHostPort(bridge).decide({
        snapshot: SNAPSHOT,
        decision: "approve",
      }),
    ).rejects.toBeInstanceOf(DesktopWorkspaceApprovalPortError);
  });
});
