import {
  createHmac,
  randomBytes as nodeRandomBytes,
  randomUUID,
} from "node:crypto";

import { FsError, normalizeVirtualPath } from "./path-validation";
import type { Grant, GrantProvider } from "./types";

// The v2 workspace authority is deliberately separate from HostFs.  HostFs is
// the read-only compatibility surface; this module is the *only* path allowed
// to prepare or commit an effect to a user-granted host workspace.  It performs
// no path-string filesystem calls.  Every disk operation below is delegated to
// a native, handle-relative port after authority has been checked in main.

export type WorkspaceIsolationState = "enforced" | "unavailable";
export type WorkspaceNativeState = "available" | "unavailable";

export interface WorkspaceWriteAttestation {
  readonly workspaceWriteIsolation: WorkspaceIsolationState;
  readonly nativeWorkspacePrimitives: WorkspaceNativeState;
  /**
   * Dev-only escape hatch. It is accepted only by an explicitly non-production
   * composition and is intentionally surfaced in the public attestation so it
   * cannot be confused with launch evidence.
   */
  readonly unsafeDevWorkspaceTcb?: boolean;
}

export interface WorkspaceRunFacts {
  readonly runId: string;
  readonly userId: string;
  readonly deviceId: string;
}

export interface WorkspaceRootIdentity {
  readonly volumeId: string;
  readonly fileId: string;
}

export type WorkspaceOperation =
  | "create"
  | "replace"
  | "delete"
  | "move"
  | "mkdir";

export interface WorkspacePrecondition {
  readonly exists: boolean;
  readonly kind?: "file" | "directory";
  readonly stableId?: string;
  readonly sha256?: string;
}

/** One reviewed host mutation. Paths are virtual and never exported by this module. */
export interface WorkspaceChangeEntry {
  readonly operation: WorkspaceOperation;
  readonly relativePath: string;
  readonly destinationRelativePath?: string;
  readonly contentDigest?: string;
  readonly contentSize?: number;
  /** Opaque upload slot declared by immutable proposal material. */
  readonly contentSlot?: string;
  readonly precondition: WorkspacePrecondition;
}

/**
 * C1's immutable workspace proposal materialized for the native authority.
 * It contains no physical root. The trusted caller supplies it only after
 * resolving the private target reference server-side.
 */
export interface WorkspaceChangeSet {
  readonly stageId: string;
  readonly revision: number;
  readonly decisionLedgerId: string;
  readonly grantId: string;
  readonly mount: string;
  readonly changeSetDigest: string;
  readonly targetDigest: string;
  readonly proposalDigest: string;
  readonly entries: readonly WorkspaceChangeEntry[];
}

export interface WorkspaceReadCapability {
  readonly capability: string;
  readonly runId: string;
  readonly userId: string;
  readonly deviceId: string;
  readonly grantIds: readonly string[];
  readonly expiresAt: number;
  readonly maxOperations: number;
  readonly maxBytes: number;
}

export interface WorkspacePreparedEffect {
  readonly preparedRef: string;
  readonly expiresAt: number;
  readonly observedTargetDigest: string;
  readonly uploadSlots: readonly WorkspaceUploadSlot[];
}

/**
 * Main-only binding facts for one prepared C2 effect.  This deliberately
 * excludes the native handle, root, read capability, and raw permit.  The
 * private broker uses it to consume a verified approval reservation without
 * accepting any approval facts from the worker.
 */
export interface WorkspacePreparedBinding {
  readonly preparedRef: string;
  readonly facts: WorkspaceRunFacts;
  readonly stageId: string;
  readonly revision: number;
  readonly decisionLedgerId: string;
  readonly changeSetDigest: string;
  readonly targetDigest: string;
  readonly proposalDigest: string;
}

/** Main-only read authority retained by the local broker host-session map. */
export interface WorkspacePrivateHostSession {
  readonly facts: WorkspaceRunFacts;
  readonly readCapability: string;
  readonly grantIds: readonly string[];
  readonly expiresAt: number;
}

export interface WorkspaceUploadSlot {
  readonly slot: string;
  readonly digest: string;
  readonly size: number;
}

export interface WorkspaceCommitPermit {
  readonly permit: string;
  readonly commitId: string;
  readonly preparedRef: string;
  readonly stageId: string;
  readonly revision: number;
  readonly decisionLedgerId: string;
  readonly changeSetDigest: string;
  readonly targetDigest: string;
  readonly proposalDigest: string;
  readonly runId: string;
  readonly userId: string;
  readonly deviceId: string;
  readonly expiresAt: number;
  readonly allowedOperations: number;
  readonly allowedBytes: number;
}

export type WorkspaceCommitOutcome =
  | "applied"
  | "already_applied"
  | "precondition_drift"
  | "failed"
  | "indeterminate";

export interface WorkspaceCommitResult {
  readonly outcome: WorkspaceCommitOutcome;
  readonly receiptRef: string;
  readonly resultDigest?: string;
  readonly safeMessage?: string;
}

export type WorkspaceJournalState =
  | "prepared"
  | "authorized"
  | "committing"
  | "applied"
  | "failed_before_effect"
  | "indeterminate"
  | "recovery_proposed"
  | "rolled_back"
  | "recovery_conflict";

/** Safe/exportable journal facts. Paths are keyed tokens, never plaintext. */
export interface WorkspaceJournalRecord {
  readonly preparedRef: string;
  readonly state: WorkspaceJournalState;
  readonly runId: string;
  readonly userId: string;
  readonly deviceId: string;
  readonly stageId: string;
  readonly revision: number;
  readonly decisionLedgerId: string;
  readonly claimId?: string;
  readonly pathTokens: readonly string[];
  readonly changeSetDigest: string;
  readonly targetDigest: string;
  readonly proposalDigest: string;
  readonly createdAt: number;
  readonly updatedAt: number;
  readonly result?: WorkspaceCommitResult;
}

/**
 * Main-only native port. Every method operates on a retained root/parent
 * handle held by the native implementation; it intentionally accepts neither
 * absolute paths nor a caller-selected root after prepare.
 */
export interface NativeWorkspaceAuthority {
  readonly primitivesAvailable: boolean;
  rootIdentity(root: string): Promise<WorkspaceRootIdentity>;
  prepare(
    root: string,
    entries: readonly WorkspaceChangeEntry[],
  ): Promise<NativePreparedWorkspace>;
  writePrepared(
    prepared: NativePreparedWorkspace,
    slot: string,
    chunk: Uint8Array,
  ): Promise<void>;
  sealPrepared(
    prepared: NativePreparedWorkspace,
    slot: string,
  ): Promise<{ readonly digest: string; readonly size: number }>;
  commitPrepared(
    prepared: NativePreparedWorkspace,
    claimId: string,
  ): Promise<NativeWorkspaceCommitResult>;
  reconcilePrepared(
    prepared: NativePreparedWorkspace,
    claimId: string,
  ): Promise<NativeWorkspaceCommitResult>;
  /** Reconcile after an Electron restart from the native durable journal. */
  reconcileClaim(claimId: string): Promise<NativeWorkspaceCommitResult>;
  abortPrepared(prepared: NativePreparedWorkspace): Promise<void>;
  proposeRecovery(
    prepared: NativePreparedWorkspace,
  ): Promise<"proposed" | "conflict">;
  proposeRecoveryClaim(claimId: string): Promise<"proposed" | "conflict">;
}

/** An opaque native handle; it must never be serialized outside Electron main. */
export interface NativePreparedWorkspace {
  readonly handle: string;
  readonly observedTargetDigest: string;
  readonly slots: readonly WorkspaceUploadSlot[];
}

export interface NativeWorkspaceCommitResult {
  readonly outcome: WorkspaceCommitOutcome;
  readonly receiptRef: string;
  readonly resultDigest?: string;
  readonly safeMessage?: string;
}

export interface WorkspaceJournalStore {
  get(preparedRef: string): Promise<WorkspaceJournalRecord | null>;
  put(record: WorkspaceJournalRecord): Promise<void>;
  listNonterminal(): Promise<readonly WorkspaceJournalRecord[]>;
}

export class InMemoryWorkspaceJournalStore implements WorkspaceJournalStore {
  readonly #records = new Map<string, WorkspaceJournalRecord>();

  async get(preparedRef: string): Promise<WorkspaceJournalRecord | null> {
    return this.#records.get(preparedRef) ?? null;
  }

  async put(record: WorkspaceJournalRecord): Promise<void> {
    this.#records.set(record.preparedRef, Object.freeze({ ...record }));
  }

  async listNonterminal(): Promise<readonly WorkspaceJournalRecord[]> {
    return [...this.#records.values()].filter(
      (record) =>
        record.state !== "applied" &&
        record.state !== "failed_before_effect" &&
        record.state !== "rolled_back" &&
        record.state !== "recovery_conflict",
    );
  }
}

export class WorkspaceAuthorityError extends Error {
  readonly code:
    | "workspace_write_unsupported"
    | "workspace_capability_denied"
    | "workspace_permit_denied"
    | "workspace_prepared_not_found"
    | "workspace_precondition_drift"
    | "workspace_conflict";

  constructor(code: WorkspaceAuthorityError["code"]) {
    super(code);
    this.name = "WorkspaceAuthorityError";
    this.code = code;
  }
}

interface PreparedState {
  readonly prepared: WorkspacePreparedEffect;
  readonly native: NativePreparedWorkspace;
  readonly facts: WorkspaceRunFacts;
  readonly changeSet: WorkspaceChangeSet;
  readonly capability: WorkspaceReadCapability;
  readonly rootIdentity: WorkspaceRootIdentity;
  readonly journalCreatedAt: number;
  /** Main-owned upload accounting: every declared payload must be sealed. */
  readonly uploads: Map<string, { bytesWritten: number; sealed: boolean }>;
}

interface PermitState {
  readonly permit: WorkspaceCommitPermit;
  consumed: boolean;
}

export interface LocalWorkspaceAuthorityConfig {
  readonly grants: GrantProvider;
  readonly native: NativeWorkspaceAuthority;
  readonly journal: WorkspaceJournalStore;
  readonly attestation: WorkspaceWriteAttestation;
  readonly production: boolean;
  readonly deviceId: string;
  readonly now?: () => number;
  readonly randomBytes?: (size: number) => Buffer;
  readonly id?: () => string;
  /** Per-installation journal MAC key. Main-owned and never exportable. */
  readonly journalTokenKey?: Buffer;
}

/**
 * Electron-main local-file authority.  The boot broker authenticates a child
 * process but grants no filesystem right by itself.  A read capability and an
 * exact one-use permit are separately required for every write lifecycle.
 */
export class LocalWorkspaceAuthority {
  readonly #grants: GrantProvider;
  readonly #native: NativeWorkspaceAuthority;
  readonly #journal: WorkspaceJournalStore;
  readonly #attestation: WorkspaceWriteAttestation;
  readonly #production: boolean;
  readonly #deviceId: string;
  readonly #now: () => number;
  readonly #randomBytes: (size: number) => Buffer;
  readonly #id: () => string;
  readonly #journalTokenKey: Buffer;
  readonly #readCapabilities = new Map<string, WorkspaceReadCapability>();
  readonly #prepared = new Map<string, PreparedState>();
  readonly #permits = new Map<string, PermitState>();

  constructor(config: LocalWorkspaceAuthorityConfig) {
    this.#grants = config.grants;
    this.#native = config.native;
    this.#journal = config.journal;
    this.#attestation = config.attestation;
    this.#production = config.production;
    this.#deviceId = config.deviceId;
    this.#now = config.now ?? Date.now;
    this.#randomBytes = config.randomBytes ?? nodeRandomBytes;
    this.#id = config.id ?? randomUUID;
    this.#journalTokenKey = config.journalTokenKey ?? this.#randomBytes(32);
  }

  startupAttestation(): WorkspaceWriteAttestation {
    return Object.freeze({ ...this.#attestation });
  }

  writableAvailable(): boolean {
    if (!this.#native.primitivesAvailable) return false;
    if (this.#attestation.nativeWorkspacePrimitives !== "available")
      return false;
    if (this.#attestation.workspaceWriteIsolation === "enforced") return true;
    return (
      !this.#production && this.#attestation.unsafeDevWorkspaceTcb === true
    );
  }

  async createReadCapability(
    facts: WorkspaceRunFacts,
    grantIds: readonly string[],
    opts: {
      readonly ttlMs?: number;
      readonly maxOperations?: number;
      readonly maxBytes?: number;
    } = {},
  ): Promise<WorkspaceReadCapability> {
    this.#assertFacts(facts);
    const live = await this.#liveGrants(facts, grantIds);
    if (live.length !== new Set(grantIds).size || live.length === 0) {
      throw new WorkspaceAuthorityError("workspace_capability_denied");
    }
    const capability: WorkspaceReadCapability = Object.freeze({
      capability: `wrc_${this.#randomBytes(32).toString("base64url")}`,
      runId: facts.runId,
      userId: facts.userId,
      deviceId: facts.deviceId,
      grantIds: Object.freeze([...grantIds]),
      expiresAt: this.#now() + (opts.ttlMs ?? 15 * 60_000),
      maxOperations: opts.maxOperations ?? 1_000,
      maxBytes: opts.maxBytes ?? 128 * 1024 * 1024,
    });
    this.#readCapabilities.set(capability.capability, capability);
    return capability;
  }

  /**
   * Main-only bootstrap for one authenticated worker host session.  The
   * worker supplies only run/user identifiers over the broker's per-boot
   * authenticated channel; the device id and the actual read capability stay
   * in Electron main.  This is intentionally not an IPC/preload surface.
   */
  async createPrivateHostSession(
    runId: string,
    userId: string,
  ): Promise<WorkspacePrivateHostSession> {
    this.#requireWritable();
    const facts: WorkspaceRunFacts = {
      runId,
      userId,
      deviceId: this.#deviceId,
    };
    const now = this.#now();
    const grantIds = (await this.#grants.listAll())
      .filter(
        (grant) =>
          grant.status === "active" &&
          grant.profileId === userId &&
          grant.deviceId === this.#deviceId &&
          grant.expiresAt !== undefined &&
          grant.expiresAt > now &&
          grant.rootIdentity !== undefined &&
          grant.allowedPathPrefixes !== undefined,
      )
      .map((grant) => grant.grantId);
    const capability = await this.createReadCapability(facts, grantIds);
    return Object.freeze({
      facts,
      readCapability: capability.capability,
      grantIds: capability.grantIds,
      expiresAt: capability.expiresAt,
    });
  }

  async prepareChangeSet(
    capabilityId: string,
    changeSet: WorkspaceChangeSet,
  ): Promise<WorkspacePreparedEffect> {
    this.#requireWritable();
    const capability = this.#requireCapability(capabilityId);
    const facts: WorkspaceRunFacts = {
      runId: capability.runId,
      userId: capability.userId,
      deviceId: capability.deviceId,
    };
    this.#validateChangeSet(changeSet, capability);
    const grant = await this.#resolveLiveGrant(
      facts,
      capability,
      changeSet.grantId,
    );
    this.#assertGrantAllowsChangeSet(grant, changeSet);
    const rootIdentity = await this.#native.rootIdentity(grant.root);
    this.#assertGrantIdentity(grant, rootIdentity);
    const nativePrepared = await this.#native.prepare(
      grant.root,
      changeSet.entries,
    );
    const preparedRef = `workspace-prepared://${this.#id()}`;
    const prepared: WorkspacePreparedEffect = Object.freeze({
      preparedRef,
      expiresAt: Math.min(capability.expiresAt, this.#now() + 10 * 60_000),
      observedTargetDigest: nativePrepared.observedTargetDigest,
      uploadSlots: Object.freeze([...nativePrepared.slots]),
    });
    const state: PreparedState = {
      prepared,
      native: nativePrepared,
      facts,
      changeSet,
      capability,
      rootIdentity,
      journalCreatedAt: this.#now(),
      uploads: new Map(
        prepared.uploadSlots.map((slot) => [
          slot.slot,
          { bytesWritten: 0, sealed: false },
        ]),
      ),
    };
    this.#prepared.set(preparedRef, state);
    await this.#journal.put(this.#journalRecord(state, "prepared"));
    return prepared;
  }

  /**
   * Return the exact non-sensitive binding for a prepared effect.  This is a
   * main-only lookup: the worker never receives these values as caller input
   * for commit, and no native/root/capability state is exposed.
   */
  preparedBinding(preparedRef: string): WorkspacePreparedBinding {
    const state = this.#requirePrepared(preparedRef);
    return Object.freeze({
      preparedRef: state.prepared.preparedRef,
      facts: Object.freeze({ ...state.facts }),
      stageId: state.changeSet.stageId,
      revision: state.changeSet.revision,
      decisionLedgerId: state.changeSet.decisionLedgerId,
      changeSetDigest: state.changeSet.changeSetDigest,
      targetDigest: state.changeSet.targetDigest,
      proposalDigest: state.changeSet.proposalDigest,
    });
  }

  /**
   * Return a prepared binding only when C2 has a live, sealed reservation that
   * is still eligible to consume an approval.  The broker calls this before
   * taking a one-use receipt reservation, so a failed upload/CAS conflict
   * cannot burn a valid user approval or masquerade as an authorization error.
   */
  async commitEligibleBinding(
    preparedRef: string,
  ): Promise<WorkspacePreparedBinding> {
    this.#requireWritable();
    const state = this.#requirePrepared(preparedRef);
    await this.#assertPreparedLive(state);
    const journal = await this.#journal.get(preparedRef);
    if (
      journal?.state !== "prepared" ||
      [...state.uploads.values()].some((progress) => !progress.sealed)
    ) {
      throw new WorkspaceAuthorityError("workspace_conflict");
    }
    return this.preparedBinding(preparedRef);
  }

  async uploadPreparedContent(
    preparedRef: string,
    slot: string,
    chunk: Uint8Array,
  ): Promise<void> {
    const state = this.#requirePrepared(preparedRef);
    await this.#assertPreparedLive(state);
    const expected = state.prepared.uploadSlots.find(
      (candidate) => candidate.slot === slot,
    );
    const progress = state.uploads.get(slot);
    if (
      expected === undefined ||
      progress === undefined ||
      progress.sealed ||
      chunk.byteLength > expected.size - progress.bytesWritten
    ) {
      throw new WorkspaceAuthorityError("workspace_conflict");
    }
    await this.#native.writePrepared(state.native, slot, chunk);
    progress.bytesWritten += chunk.byteLength;
  }

  async sealPreparedContent(preparedRef: string, slot: string): Promise<void> {
    const state = this.#requirePrepared(preparedRef);
    await this.#assertPreparedLive(state);
    const expected = state.prepared.uploadSlots.find(
      (candidate) => candidate.slot === slot,
    );
    const progress = state.uploads.get(slot);
    if (
      expected === undefined ||
      progress === undefined ||
      progress.sealed ||
      progress.bytesWritten !== expected.size
    ) {
      throw new WorkspaceAuthorityError("workspace_conflict");
    }
    const observed = await this.#native.sealPrepared(state.native, slot);
    if (
      observed.digest !== expected.digest ||
      observed.size !== expected.size
    ) {
      await this.#native.abortPrepared(state.native);
      await this.#journal.put(
        this.#journalRecord(state, "failed_before_effect"),
      );
      throw new WorkspaceAuthorityError("workspace_conflict");
    }
    progress.sealed = true;
  }

  /** Main-only: invoked after the desktop host verifies the server decision. */
  async authorizeCommitFromUserDecision(
    facts: WorkspaceRunFacts,
    preparedRef: string,
    decision: {
      readonly stageId: string;
      readonly revision: number;
      readonly decisionLedgerId: string;
    },
  ): Promise<WorkspaceCommitPermit> {
    this.#requireWritable();
    const state = this.#requirePrepared(preparedRef);
    await this.#assertPreparedLive(state);
    const journal = await this.#journal.get(preparedRef);
    // A sealed staging object may receive exactly one authorization. Failed,
    // aborted, and already-committing objects are never revivable through a
    // fresh permit.
    if (journal?.state !== "prepared") {
      throw new WorkspaceAuthorityError("workspace_conflict");
    }
    if (
      facts.runId !== state.facts.runId ||
      facts.userId !== state.facts.userId ||
      facts.deviceId !== this.#deviceId ||
      decision.stageId !== state.changeSet.stageId ||
      decision.revision !== state.changeSet.revision ||
      decision.decisionLedgerId !== state.changeSet.decisionLedgerId
    ) {
      throw new WorkspaceAuthorityError("workspace_permit_denied");
    }
    const permit: WorkspaceCommitPermit = Object.freeze({
      permit: `wcp_${this.#randomBytes(32).toString("base64url")}`,
      commitId: `wcc_${this.#id()}`,
      preparedRef,
      stageId: state.changeSet.stageId,
      revision: state.changeSet.revision,
      decisionLedgerId: state.changeSet.decisionLedgerId,
      changeSetDigest: state.changeSet.changeSetDigest,
      targetDigest: state.changeSet.targetDigest,
      proposalDigest: state.changeSet.proposalDigest,
      runId: state.facts.runId,
      userId: state.facts.userId,
      deviceId: state.facts.deviceId,
      expiresAt: state.prepared.expiresAt,
      allowedOperations: state.changeSet.entries.length,
      allowedBytes: state.prepared.uploadSlots.reduce(
        (sum, slot) => sum + slot.size,
        0,
      ),
    });
    this.#permits.set(permit.permit, { permit, consumed: false });
    await this.#journal.put(this.#journalRecord(state, "authorized"));
    return permit;
  }

  async commitPreparedChangeSet(
    preparedRef: string,
    permitValue: string,
  ): Promise<WorkspaceCommitResult> {
    this.#requireWritable();
    const state = this.#requirePrepared(preparedRef);
    const permitState = this.#permits.get(permitValue);
    if (
      permitState === undefined ||
      !this.#permitMatches(permitState.permit, state)
    ) {
      throw new WorkspaceAuthorityError("workspace_permit_denied");
    }
    const recorded = await this.#journal.get(preparedRef);
    if (permitState.consumed) {
      if (recorded?.result !== undefined) return recorded.result;
      throw new WorkspaceAuthorityError("workspace_conflict");
    }
    // Re-evaluate revocation, expiry, root identity, and subpath policy before
    // disclosing any other failure mode or consuming the exact one-use permit.
    await this.#assertPreparedLive(state);
    if ([...state.uploads.values()].some((progress) => !progress.sealed)) {
      throw new WorkspaceAuthorityError("workspace_conflict");
    }
    permitState.consumed = true;
    await this.#journal.put(
      this.#journalRecord(state, "committing", permitState.permit.commitId),
    );
    try {
      const result = await this.#native.commitPrepared(
        state.native,
        permitState.permit.commitId,
      );
      const mapped = toCommitResult(result);
      const terminal: WorkspaceJournalState =
        mapped.outcome === "indeterminate" ? "indeterminate" : "applied";
      await this.#journal.put(
        this.#journalRecord(
          state,
          terminal,
          permitState.permit.commitId,
          mapped,
        ),
      );
      return mapped;
    } catch {
      const result: WorkspaceCommitResult = Object.freeze({
        outcome: "indeterminate",
        receiptRef: `workspace-receipt://${permitState.permit.commitId}`,
        safeMessage: "The workspace change outcome could not be confirmed.",
      });
      await this.#journal.put(
        this.#journalRecord(
          state,
          "indeterminate",
          permitState.permit.commitId,
          result,
        ),
      );
      return result;
    }
  }

  async reconcileCommit(claimId: string): Promise<WorkspaceCommitResult> {
    const record = (await this.#journal.listNonterminal()).find(
      (row) => row.claimId === claimId,
    );
    if (record === undefined)
      throw new WorkspaceAuthorityError("workspace_prepared_not_found");
    const result = toCommitResult(await this.#native.reconcileClaim(claimId));
    const terminal: WorkspaceJournalState =
      result.outcome === "indeterminate" ? "indeterminate" : "applied";
    await this.#journal.put(this.#transitionRecord(record, terminal, result));
    return result;
  }

  async abortPreparedChangeSet(preparedRef: string): Promise<void> {
    const state = this.#requirePrepared(preparedRef);
    const record = await this.#journal.get(preparedRef);
    if (record?.state === "committing" || record?.state === "applied") {
      throw new WorkspaceAuthorityError("workspace_conflict");
    }
    await this.#native.abortPrepared(state.native);
    await this.#journal.put(this.#journalRecord(state, "failed_before_effect"));
  }

  async proposeRecovery(preparedRef: string): Promise<"proposed" | "conflict"> {
    const state = this.#requirePrepared(preparedRef);
    const result = await this.#native.proposeRecovery(state.native);
    await this.#journal.put(
      this.#journalRecord(
        state,
        result === "proposed" ? "recovery_proposed" : "recovery_conflict",
      ),
    );
    return result;
  }

  async proposeRecoveryForClaim(
    claimId: string,
  ): Promise<"proposed" | "conflict"> {
    const record = (await this.#journal.listNonterminal()).find(
      (row) => row.claimId === claimId,
    );
    if (record === undefined)
      throw new WorkspaceAuthorityError("workspace_prepared_not_found");
    const result = await this.#native.proposeRecoveryClaim(claimId);
    await this.#journal.put(
      this.#transitionRecord(
        record,
        result === "proposed" ? "recovery_proposed" : "recovery_conflict",
      ),
    );
    return result;
  }

  #requireWritable(): void {
    if (!this.writableAvailable()) {
      throw new WorkspaceAuthorityError("workspace_write_unsupported");
    }
  }

  #assertFacts(facts: WorkspaceRunFacts): void {
    if (!facts.runId || !facts.userId || facts.deviceId !== this.#deviceId) {
      throw new WorkspaceAuthorityError("workspace_capability_denied");
    }
  }

  #requireCapability(value: string): WorkspaceReadCapability {
    const capability = this.#readCapabilities.get(value);
    if (
      capability === undefined ||
      capability.expiresAt <= this.#now() ||
      capability.deviceId !== this.#deviceId
    ) {
      throw new WorkspaceAuthorityError("workspace_capability_denied");
    }
    return capability;
  }

  async #liveGrants(
    facts: WorkspaceRunFacts,
    grantIds: readonly string[],
  ): Promise<Grant[]> {
    this.#assertFacts(facts);
    const wanted = new Set(grantIds);
    const all = await this.#grants.listAll();
    const now = this.#now();
    return all.filter(
      (grant) =>
        wanted.has(grant.grantId) &&
        grant.status === "active" &&
        grant.profileId === facts.userId &&
        grant.deviceId === facts.deviceId &&
        grant.expiresAt !== undefined &&
        grant.expiresAt > now &&
        grant.rootIdentity !== undefined &&
        grant.allowedPathPrefixes !== undefined,
    );
  }

  async #resolveLiveGrant(
    facts: WorkspaceRunFacts,
    capability: WorkspaceReadCapability,
    grantId: string,
  ): Promise<Grant> {
    if (!capability.grantIds.includes(grantId)) {
      throw new WorkspaceAuthorityError("workspace_capability_denied");
    }
    const grant = (await this.#liveGrants(facts, [grantId]))[0];
    if (grant === undefined)
      throw new WorkspaceAuthorityError("workspace_capability_denied");
    return grant;
  }

  #assertGrantIdentity(grant: Grant, observed: WorkspaceRootIdentity): void {
    const expected = grant.rootIdentity;
    if (
      expected === undefined ||
      expected.volumeId !== observed.volumeId ||
      expected.fileId !== observed.fileId
    ) {
      throw new WorkspaceAuthorityError("workspace_capability_denied");
    }
  }

  #assertGrantAllowsChangeSet(
    grant: Grant,
    changeSet: WorkspaceChangeSet,
  ): void {
    const prefixes = grant.allowedPathPrefixes;
    if (
      prefixes === undefined ||
      prefixes.length === 0 ||
      grant.mode === "read_only"
    ) {
      throw new WorkspaceAuthorityError("workspace_capability_denied");
    }
    for (const entry of changeSet.entries) {
      const paths = [entry.relativePath, entry.destinationRelativePath].filter(
        (value): value is string => value !== undefined,
      );
      if (
        !paths.every((relativePath) =>
          prefixes.some((prefix) => isWithinPrefix(relativePath, prefix)),
        )
      ) {
        throw new WorkspaceAuthorityError("workspace_capability_denied");
      }
      if (
        grant.mode === "read_write_no_delete" &&
        (entry.operation === "delete" || entry.operation === "move")
      ) {
        throw new WorkspaceAuthorityError("workspace_capability_denied");
      }
    }
  }

  #validateChangeSet(
    changeSet: WorkspaceChangeSet,
    capability: WorkspaceReadCapability,
  ): void {
    if (
      !changeSet.stageId ||
      !changeSet.decisionLedgerId ||
      changeSet.revision < 1 ||
      changeSet.entries.length === 0 ||
      changeSet.entries.length > capability.maxOperations
    ) {
      throw new WorkspaceAuthorityError("workspace_conflict");
    }
    let bytes = 0;
    for (const entry of changeSet.entries) {
      try {
        if (entry.relativePath === "") {
          throw new FsError(
            "invalid_path",
            "workspace entry needs a leaf path",
          );
        }
        normalizeVirtualPath(entry.relativePath);
        if (entry.destinationRelativePath !== undefined)
          normalizeVirtualPath(entry.destinationRelativePath);
      } catch (error) {
        if (error instanceof FsError)
          throw new WorkspaceAuthorityError("workspace_conflict");
        throw error;
      }
      if (entry.contentSize !== undefined) bytes += entry.contentSize;
      const hasContent =
        entry.contentDigest !== undefined ||
        entry.contentSize !== undefined ||
        entry.contentSlot !== undefined;
      if (
        (entry.operation === "create" || entry.operation === "replace") !==
          hasContent ||
        (hasContent &&
          (entry.contentDigest === undefined ||
            entry.contentSize === undefined ||
            entry.contentSlot === undefined ||
            !/^[A-Za-z0-9_-]{1,120}$/u.test(entry.contentSlot)))
      ) {
        throw new WorkspaceAuthorityError("workspace_conflict");
      }
      if (
        entry.operation === "move" &&
        entry.destinationRelativePath === undefined
      ) {
        throw new WorkspaceAuthorityError("workspace_conflict");
      }
      if (
        entry.operation !== "move" &&
        entry.destinationRelativePath !== undefined
      ) {
        throw new WorkspaceAuthorityError("workspace_conflict");
      }
    }
    const contentSlots = changeSet.entries
      .map((entry) => entry.contentSlot)
      .filter((slot): slot is string => slot !== undefined);
    if (new Set(contentSlots).size !== contentSlots.length) {
      throw new WorkspaceAuthorityError("workspace_conflict");
    }
    if (bytes > capability.maxBytes)
      throw new WorkspaceAuthorityError("workspace_conflict");
  }

  #requirePrepared(preparedRef: string): PreparedState {
    const state = this.#prepared.get(preparedRef);
    if (state === undefined)
      throw new WorkspaceAuthorityError("workspace_prepared_not_found");
    return state;
  }

  async #assertPreparedLive(state: PreparedState): Promise<void> {
    if (state.prepared.expiresAt <= this.#now()) {
      throw new WorkspaceAuthorityError("workspace_capability_denied");
    }
    const grant = await this.#resolveLiveGrant(
      state.facts,
      state.capability,
      state.changeSet.grantId,
    );
    this.#assertGrantAllowsChangeSet(grant, state.changeSet);
    const observed = await this.#native.rootIdentity(grant.root);
    this.#assertGrantIdentity(grant, observed);
    if (
      observed.volumeId !== state.rootIdentity.volumeId ||
      observed.fileId !== state.rootIdentity.fileId
    ) {
      throw new WorkspaceAuthorityError("workspace_capability_denied");
    }
  }

  #permitMatches(permit: WorkspaceCommitPermit, state: PreparedState): boolean {
    return (
      permit.preparedRef === state.prepared.preparedRef &&
      permit.expiresAt > this.#now() &&
      permit.runId === state.facts.runId &&
      permit.userId === state.facts.userId &&
      permit.deviceId === state.facts.deviceId &&
      permit.stageId === state.changeSet.stageId &&
      permit.revision === state.changeSet.revision &&
      permit.decisionLedgerId === state.changeSet.decisionLedgerId &&
      permit.changeSetDigest === state.changeSet.changeSetDigest &&
      permit.targetDigest === state.changeSet.targetDigest &&
      permit.proposalDigest === state.changeSet.proposalDigest &&
      permit.allowedOperations === state.changeSet.entries.length &&
      permit.allowedBytes ===
        state.prepared.uploadSlots.reduce((sum, slot) => sum + slot.size, 0)
    );
  }

  #journalRecord(
    state: PreparedState,
    journalState: WorkspaceJournalState,
    claimId?: string,
    result?: WorkspaceCommitResult,
  ): WorkspaceJournalRecord {
    const now = this.#now();
    return Object.freeze({
      preparedRef: state.prepared.preparedRef,
      state: journalState,
      runId: state.facts.runId,
      userId: state.facts.userId,
      deviceId: state.facts.deviceId,
      stageId: state.changeSet.stageId,
      revision: state.changeSet.revision,
      decisionLedgerId: state.changeSet.decisionLedgerId,
      claimId,
      pathTokens: Object.freeze(
        state.changeSet.entries.map((entry) =>
          this.#pathToken(entry.relativePath),
        ),
      ),
      changeSetDigest: state.changeSet.changeSetDigest,
      targetDigest: state.changeSet.targetDigest,
      proposalDigest: state.changeSet.proposalDigest,
      createdAt: state.journalCreatedAt,
      updatedAt: now,
      result,
    });
  }

  #transitionRecord(
    record: WorkspaceJournalRecord,
    state: WorkspaceJournalState,
    result?: WorkspaceCommitResult,
  ): WorkspaceJournalRecord {
    return Object.freeze({
      ...record,
      state,
      updatedAt: this.#now(),
      result: result ?? record.result,
    });
  }

  #pathToken(relativePath: string): string {
    return `path_${createHmac("sha256", this.#journalTokenKey)
      .update(relativePath, "utf8")
      .digest("base64url")}`;
  }
}

function toCommitResult(
  result: NativeWorkspaceCommitResult,
): WorkspaceCommitResult {
  return Object.freeze({
    outcome: result.outcome,
    receiptRef: result.receiptRef,
    resultDigest: result.resultDigest,
    safeMessage: result.safeMessage,
  });
}

function isWithinPrefix(relativePath: string, prefix: string): boolean {
  if (prefix === "") return true;
  return relativePath === prefix || relativePath.startsWith(`${prefix}/`);
}
