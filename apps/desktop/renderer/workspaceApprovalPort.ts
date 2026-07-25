// Renderer adapter for C3's narrow workspace approval bridge.
//
// This file is intentionally the only product-UI call site for
// `capability.decide-workspace-approval`. It takes a preloaded WindowBridge as
// an injected dependency (no shared code reaches for `window`) and sends only
// the digest-pinned snapshot that Electron main validates before it records a
// facade decision and performs its private permit handoff.

import type {
  WorkspaceApprovalDecision,
  WorkspaceApprovalHostDecisionResult,
  WorkspaceApprovalHostPort,
  WorkspaceApprovalSnapshot,
} from "@0x-copilot/chat-surface";

import { CAPABILITY_CHANNELS } from "../main/capabilities/channels";
import type { WindowBridge } from "../preload/window-bridge-types";

const OPAQUE_ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$/u;
const SHA256_HEX = /^[a-f0-9]{64}$/u;

export class DesktopWorkspaceApprovalPortError extends Error {
  constructor() {
    super("workspace approval bridge unavailable");
    this.name = "DesktopWorkspaceApprovalPortError";
  }
}

/**
 * Build the shared cockpit's desktop-only host port.
 *
 * The explicit object literal is intentional: it prevents a future caller
 * from smuggling a renderer-only field, local path, permit, or prepared ref
 * across IPC through object spreading.
 */
export function createDesktopWorkspaceApprovalHostPort(
  bridge: WindowBridge,
): WorkspaceApprovalHostPort {
  return Object.freeze({
    async decide(input: {
      readonly snapshot: WorkspaceApprovalSnapshot;
      readonly decision: WorkspaceApprovalDecision;
    }) {
      if (!validRequest(input)) throw new DesktopWorkspaceApprovalPortError();
      const request = {
        snapshot: {
          runId: input.snapshot.runId,
          stageId: input.snapshot.stageId,
          revision: input.snapshot.revision,
          proposalDigest: input.snapshot.proposalDigest,
          targetDigest: input.snapshot.targetDigest,
        },
        decision: input.decision,
      } as const;
      const raw = await bridge.ipc.invoke<unknown>(
        CAPABILITY_CHANNELS.decideWorkspaceApproval,
        request,
      );
      const result = parseResult(raw);
      if (result === null) throw new DesktopWorkspaceApprovalPortError();
      return result;
    },
  });
}

function validRequest(input: {
  readonly snapshot: {
    readonly runId: string;
    readonly stageId: string;
    readonly revision: number;
    readonly proposalDigest: string;
    readonly targetDigest: string;
  };
  readonly decision: WorkspaceApprovalDecision;
}): boolean {
  const snapshot = input.snapshot;
  return (
    OPAQUE_ID.test(snapshot.runId) &&
    OPAQUE_ID.test(snapshot.stageId) &&
    Number.isSafeInteger(snapshot.revision) &&
    snapshot.revision > 0 &&
    SHA256_HEX.test(snapshot.proposalDigest) &&
    SHA256_HEX.test(snapshot.targetDigest) &&
    (input.decision === "approve" || input.decision === "reject")
  );
}

function parseResult(
  value: unknown,
): WorkspaceApprovalHostDecisionResult | null {
  if (!isRecord(value)) return null;
  const keys = Object.keys(value).sort();
  if (
    keys.length !== 4 ||
    keys[0] !== "decision" ||
    keys[1] !== "revision" ||
    keys[2] !== "stageId" ||
    keys[3] !== "status"
  ) {
    return null;
  }
  if (
    typeof value.stageId !== "string" ||
    !OPAQUE_ID.test(value.stageId) ||
    typeof value.revision !== "number" ||
    !Number.isSafeInteger(value.revision) ||
    value.revision <= 0 ||
    (value.decision !== "approve" && value.decision !== "reject") ||
    (value.status !== "approved" &&
      value.status !== "rejected" &&
      value.status !== "cancelled")
  ) {
    return null;
  }
  return Object.freeze({
    stageId: value.stageId,
    revision: value.revision,
    decision: value.decision,
    status: value.status,
  });
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
