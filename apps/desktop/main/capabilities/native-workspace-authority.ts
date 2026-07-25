import type { NativeWorkspaceFs } from "../../native/workspace-fs";

import {
  type NativePreparedWorkspace,
  type NativeWorkspaceAuthority,
  type NativeWorkspaceCommitResult,
  type WorkspaceChangeEntry,
  type WorkspaceRootIdentity,
} from "./workspace-authority";

/**
 * The v2 native addon contract. This is deliberately richer than the legacy
 * ``openBeneath`` helper: every write lifecycle operation stays in native code
 * with retained handles. Electron main merely brokers opaque handles and bytes.
 */
export interface NativeWorkspaceV2Bindings extends NativeWorkspaceFs {
  workspaceRootIdentity(root: string): WorkspaceRootIdentity;
  workspacePrepare(
    root: string,
    entries: readonly WorkspaceChangeEntry[],
  ): NativePreparedWorkspace;
  workspaceWrite(preparedHandle: string, slot: string, chunk: Uint8Array): void;
  workspaceSeal(
    preparedHandle: string,
    slot: string,
  ): { readonly digest: string; readonly size: number };
  workspaceCommit(
    preparedHandle: string,
    claimId: string,
  ): NativeWorkspaceCommitResult;
  workspaceReconcile(
    preparedHandle: string,
    claimId: string,
  ): NativeWorkspaceCommitResult;
  workspaceReconcileClaim(claimId: string): NativeWorkspaceCommitResult;
  workspaceAbort(preparedHandle: string): void;
  workspaceProposeRecovery(preparedHandle: string): "proposed" | "conflict";
  workspaceProposeRecoveryClaim(claimId: string): "proposed" | "conflict";
}

/**
 * Fail-closed adapter for platforms/builds without the complete native v2
 * primitive set. It does not import or call Node filesystem APIs.
 */
export class UnavailableNativeWorkspaceAuthority implements NativeWorkspaceAuthority {
  readonly primitivesAvailable = false;

  async rootIdentity(_root: string): Promise<WorkspaceRootIdentity> {
    throw new Error("native workspace primitives are unavailable");
  }

  async prepare(
    _root: string,
    _entries: readonly WorkspaceChangeEntry[],
  ): Promise<NativePreparedWorkspace> {
    throw new Error("native workspace primitives are unavailable");
  }

  async writePrepared(
    _prepared: NativePreparedWorkspace,
    _slot: string,
    _chunk: Uint8Array,
  ): Promise<void> {
    throw new Error("native workspace primitives are unavailable");
  }

  async sealPrepared(
    _prepared: NativePreparedWorkspace,
    _slot: string,
  ): Promise<{ readonly digest: string; readonly size: number }> {
    throw new Error("native workspace primitives are unavailable");
  }

  async commitPrepared(
    _prepared: NativePreparedWorkspace,
    _claimId: string,
  ): Promise<NativeWorkspaceCommitResult> {
    throw new Error("native workspace primitives are unavailable");
  }

  async reconcilePrepared(
    _prepared: NativePreparedWorkspace,
    _claimId: string,
  ): Promise<NativeWorkspaceCommitResult> {
    throw new Error("native workspace primitives are unavailable");
  }

  async reconcileClaim(_claimId: string): Promise<NativeWorkspaceCommitResult> {
    throw new Error("native workspace primitives are unavailable");
  }

  async abortPrepared(_prepared: NativePreparedWorkspace): Promise<void> {
    throw new Error("native workspace primitives are unavailable");
  }

  async proposeRecovery(
    _prepared: NativePreparedWorkspace,
  ): Promise<"proposed" | "conflict"> {
    throw new Error("native workspace primitives are unavailable");
  }

  async proposeRecoveryClaim(
    _claimId: string,
  ): Promise<"proposed" | "conflict"> {
    throw new Error("native workspace primitives are unavailable");
  }
}

/**
 * A strict async projection of the addon. There is intentionally no fallback
 * to ``HostFs`` or any ``node:fs`` path operation: a missing v2 native method
 * is a launch-gate failure, not a degraded writable mode.
 */
export class AddonNativeWorkspaceAuthority implements NativeWorkspaceAuthority {
  readonly primitivesAvailable = true;
  readonly #bindings: NativeWorkspaceV2Bindings;

  constructor(bindings: NativeWorkspaceV2Bindings) {
    this.#bindings = bindings;
  }

  async rootIdentity(root: string): Promise<WorkspaceRootIdentity> {
    return this.#bindings.workspaceRootIdentity(root);
  }

  async prepare(
    root: string,
    entries: readonly WorkspaceChangeEntry[],
  ): Promise<NativePreparedWorkspace> {
    return this.#bindings.workspacePrepare(root, entries);
  }

  async writePrepared(
    prepared: NativePreparedWorkspace,
    slot: string,
    chunk: Uint8Array,
  ): Promise<void> {
    this.#bindings.workspaceWrite(prepared.handle, slot, chunk);
  }

  async sealPrepared(
    prepared: NativePreparedWorkspace,
    slot: string,
  ): Promise<{ readonly digest: string; readonly size: number }> {
    return this.#bindings.workspaceSeal(prepared.handle, slot);
  }

  async commitPrepared(
    prepared: NativePreparedWorkspace,
    claimId: string,
  ): Promise<NativeWorkspaceCommitResult> {
    return this.#bindings.workspaceCommit(prepared.handle, claimId);
  }

  async reconcilePrepared(
    prepared: NativePreparedWorkspace,
    claimId: string,
  ): Promise<NativeWorkspaceCommitResult> {
    return this.#bindings.workspaceReconcile(prepared.handle, claimId);
  }

  async reconcileClaim(claimId: string): Promise<NativeWorkspaceCommitResult> {
    return this.#bindings.workspaceReconcileClaim(claimId);
  }

  async abortPrepared(prepared: NativePreparedWorkspace): Promise<void> {
    this.#bindings.workspaceAbort(prepared.handle);
  }

  async proposeRecovery(
    prepared: NativePreparedWorkspace,
  ): Promise<"proposed" | "conflict"> {
    return this.#bindings.workspaceProposeRecovery(prepared.handle);
  }

  async proposeRecoveryClaim(
    claimId: string,
  ): Promise<"proposed" | "conflict"> {
    return this.#bindings.workspaceProposeRecoveryClaim(claimId);
  }
}

export function hasNativeWorkspaceV2Bindings(
  value: NativeWorkspaceFs | undefined,
): value is NativeWorkspaceV2Bindings {
  if (value === undefined) return false;
  return [
    "workspaceRootIdentity",
    "workspacePrepare",
    "workspaceWrite",
    "workspaceSeal",
    "workspaceCommit",
    "workspaceReconcile",
    "workspaceReconcileClaim",
    "workspaceAbort",
    "workspaceProposeRecovery",
    "workspaceProposeRecoveryClaim",
  ].every(
    (key) =>
      typeof (value as unknown as Record<string, unknown>)[key] === "function",
  );
}
