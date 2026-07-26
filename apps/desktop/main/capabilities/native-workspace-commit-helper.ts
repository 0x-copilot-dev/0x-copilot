import { createHmac, randomBytes, timingSafeEqual } from "node:crypto";
import { existsSync } from "node:fs";
import { spawn, spawnSync, type ChildProcess } from "node:child_process";
import { once } from "node:events";
import { join } from "node:path";
import type { Writable } from "node:stream";

import type {
  NativePreparedWorkspace,
  NativeWorkspaceAuthority,
  NativeWorkspaceCommitResult,
  WorkspaceChangeEntry,
  WorkspaceRootIdentity,
} from "./workspace-authority";

/**
 * Main-owned client for the `workspace-commit-helper` native child.
 *
 * This is intentionally not a socket, a UDS, or a named pipe discovered via
 * TMPDIR. Electron creates both pipe ends, gives the child a one-shot secret
 * on inherited fd 3, and keeps the other ends private in main. The helper has
 * no environment-derived authority and accepts only authenticated framed
 * requests from this object. The supervised services never receive this
 * channel or a filesystem path.
 */
const MAX_FRAME_BYTES = 128 * 1024 * 1024;
const MAC_BYTES = 32;
const HELPER_PROTOCOL_VERSION = 2;
const CHANNEL_SEQUENCE_BYTES = 8;
const CHANNEL_KEY_BYTES = 32;
const WORKSPACE_HELPER_IDENTIFIER = "com.0x-copilot.workspace-commit-helper";

const enum Request {
  RootIdentity = 1,
  Prepare = 2,
  Write = 3,
  Seal = 4,
  Commit = 5,
  ReconcilePrepared = 6,
  ReconcileClaim = 7,
  Abort = 8,
  ProposeRecovery = 9,
  ProposeRecoveryClaim = 10,
  Close = 11,
  Ping = 12,
}

const enum NativeOperation {
  Create = 1,
  Replace = 2,
  Delete = 3,
  Move = 4,
  Mkdir = 5,
}

const enum NativeOutcome {
  Applied = 1,
  AlreadyApplied = 2,
  PreconditionDrift = 3,
  Failed = 4,
  Indeterminate = 5,
}

const enum NativeError {
  Invalid = 1,
  Unsupported = 2,
  Conflict = 3,
  Drift = 4,
  Internal = 5,
}

export class NativeWorkspaceCommitHelperError extends Error {
  readonly code:
    | "workspace_write_unsupported"
    | "workspace_conflict"
    | "workspace_precondition_drift"
    | "workspace_helper_failed";

  constructor(code: NativeWorkspaceCommitHelperError["code"]) {
    super(code);
    this.name = "NativeWorkspaceCommitHelperError";
    this.code = code;
  }
}

export interface NativeWorkspaceCommitHelperConfig {
  /** The packaged or development helper executable, resolved by main only. */
  readonly executablePath: string;
  /**
   * An already-open, mode-0700 app-private staging directory. It is inherited
   * by the helper as fd 4; no workspace path or stage filename is ever sent
   * over the command protocol.
   */
  readonly stagingDirectoryFd: number;
  /**
   * An already-open, mode-0700 app-private journal directory, inherited as
   * fd 5. The helper uses it for its authenticated durable lifecycle records.
   */
  readonly journalDirectoryFd: number;
  /** Persistent main-owned HMAC key for durable journal records (fd 6). */
  readonly journalIntegrityKey: Uint8Array;
  /**
   * C2 must never be instantiated when the supervised runtime has not proven
   * both its process isolation and native primitive availability.
   */
  readonly attestation: Readonly<{
    workspaceWriteIsolation: "enforced";
    nativeWorkspacePrimitives: "available";
  }>;
  /** Require Apple's strict designated-requirement verification at package runtime. */
  readonly packaged?: boolean;
  /** Test seam for the macOS verifier; production callers leave this unset. */
  readonly verifyPackagedExecutable?: (path: string) => boolean;
  /**
   * Native-only fault injection for crash-boundary tests. It is a denial-only
   * input carried on a private inherited fd and is deliberately not wired to
   * any renderer, service, or production composition.
   */
  readonly testCrashBoundary?:
    | "prepared"
    | "authorized"
    | "committing"
    | "effect";
  readonly timeoutMs?: number;
  readonly randomBytes?: (size: number) => Buffer;
}

interface PendingResponse {
  readonly sequence: bigint;
  readonly resolve: (value: Buffer) => void;
  readonly reject: (reason: Error) => void;
  readonly timer: ReturnType<typeof setTimeout>;
}

/**
 * A constrained native helper client. It is deliberately serial: one helper
 * owns one retained-handle transaction at a time, which makes request/response
 * ownership, cancellation, and crash outcomes deterministic. Parallel writes
 * use independently launched helpers at a higher-level coordinator if needed.
 */
export class NativeWorkspaceCommitHelper implements NativeWorkspaceAuthority {
  readonly primitivesAvailable = true;
  readonly #child: ChildProcess;
  readonly #key: Buffer;
  readonly #timeoutMs: number;
  #stdout = Buffer.alloc(0);
  #pending: PendingResponse | null = null;
  #closed = false;
  #nextSequence = 1n;

  private constructor(child: ChildProcess, key: Buffer, timeoutMs: number) {
    this.#child = child;
    this.#key = key;
    this.#timeoutMs = timeoutMs;
    child.stdout?.on("data", (chunk: Buffer) => {
      try {
        this.#onData(chunk);
      } catch {
        this.#closed = true;
        this.#child.kill("SIGKILL");
        this.#failPending();
      }
    });
    child.once("exit", () => this.#failPending());
    child.once("error", () => this.#failPending());
  }

  static async launch(
    config: NativeWorkspaceCommitHelperConfig,
  ): Promise<NativeWorkspaceCommitHelper> {
    if (
      process.platform !== "darwin" ||
      !existsSync(config.executablePath) ||
      config.attestation.workspaceWriteIsolation !== "enforced" ||
      config.attestation.nativeWorkspacePrimitives !== "available" ||
      config.journalIntegrityKey.byteLength !== CHANNEL_KEY_BYTES ||
      !Number.isInteger(config.stagingDirectoryFd) ||
      !Number.isInteger(config.journalDirectoryFd)
    ) {
      throw new NativeWorkspaceCommitHelperError("workspace_write_unsupported");
    }
    if (
      config.packaged === true &&
      !(config.verifyPackagedExecutable ?? verifyPackagedWorkspaceCommitHelper)(
        config.executablePath,
      )
    ) {
      throw new NativeWorkspaceCommitHelperError("workspace_write_unsupported");
    }
    const key = (config.randomBytes ?? randomBytes)(CHANNEL_KEY_BYTES);
    if (key.byteLength !== CHANNEL_KEY_BYTES) {
      throw new NativeWorkspaceCommitHelperError("workspace_write_unsupported");
    }
    const testFault = config.testCrashBoundary;
    const stdio: Array<"pipe" | "ignore" | number> = [
      "pipe",
      "pipe",
      "ignore",
      "pipe",
      config.stagingDirectoryFd,
      config.journalDirectoryFd,
      "pipe",
      ...(testFault === undefined ? [] : ["pipe" as const]),
    ];
    const child: ChildProcess = spawn(config.executablePath, [], {
      // No inherited environment, cwd, shell, or ambient descriptor. stdin and
      // stdout are the private authenticated command channel; fd 3 delivers a
      // one-time boot key, fd 4 is private staging, fd 5 is the private
      // durable journal, and fd 6 delivers a persistent journal HMAC key.
      // Neither a service nor a renderer receives any of these descriptors.
      cwd: "/",
      env: {},
      shell: false,
      windowsHide: true,
      stdio,
    });
    const handles = child.stdio as Array<Writable | null | undefined>;
    const secret = handles[3];
    const journalSecret = handles[6];
    const fault = handles[7];
    if (
      secret === null ||
      secret === undefined ||
      journalSecret === null ||
      journalSecret === undefined ||
      (testFault !== undefined && (fault === null || fault === undefined))
    ) {
      child.kill();
      throw new NativeWorkspaceCommitHelperError("workspace_write_unsupported");
    }
    secret.end(key);
    journalSecret.end(config.journalIntegrityKey);
    if (testFault !== undefined)
      fault!.end(Buffer.from([faultCode(testFault)]));
    await Promise.race([
      once(child, "spawn"),
      once(child, "error").then(() => {
        throw new NativeWorkspaceCommitHelperError(
          "workspace_write_unsupported",
        );
      }),
    ]);
    const client = new NativeWorkspaceCommitHelper(
      child,
      key,
      config.timeoutMs ?? 15_000,
    );
    // Force an authenticated protocol exchange before exposing the helper.
    await client.#call(Request.Ping, Buffer.alloc(0));
    return client;
  }

  async rootIdentity(root: string): Promise<WorkspaceRootIdentity> {
    const result = await this.#call(
      Request.RootIdentity,
      new Encoder().string(root).build(),
    );
    const reader = new Decoder(result);
    return Object.freeze({
      volumeId: reader.string(),
      fileId: reader.string(),
    });
  }

  async prepare(
    root: string,
    entries: readonly WorkspaceChangeEntry[],
  ): Promise<NativePreparedWorkspace> {
    const encoder = new Encoder().string(root).u32(entries.length);
    for (const entry of entries) encodeEntry(encoder, entry);
    const result = await this.#call(Request.Prepare, encoder.build());
    const reader = new Decoder(result);
    const handle = reader.string();
    const observedTargetDigest = reader.string();
    const count = reader.u32();
    const slots = Array.from({ length: count }, () =>
      Object.freeze({
        slot: reader.string(),
        digest: reader.string(),
        size: reader.u64(),
      }),
    );
    reader.done();
    return Object.freeze({ handle, observedTargetDigest, slots });
  }

  async writePrepared(
    prepared: NativePreparedWorkspace,
    slot: string,
    chunk: Uint8Array,
  ): Promise<void> {
    await this.#call(
      Request.Write,
      new Encoder().string(prepared.handle).string(slot).bytes(chunk).build(),
    );
  }

  async sealPrepared(
    prepared: NativePreparedWorkspace,
    slot: string,
  ): Promise<{ readonly digest: string; readonly size: number }> {
    const result = await this.#call(
      Request.Seal,
      new Encoder().string(prepared.handle).string(slot).build(),
    );
    const reader = new Decoder(result);
    const sealed = Object.freeze({
      digest: reader.string(),
      size: reader.u64(),
    });
    reader.done();
    return sealed;
  }

  commitPrepared(
    prepared: NativePreparedWorkspace,
    claimId: string,
  ): Promise<NativeWorkspaceCommitResult> {
    return this.#commitLike(Request.Commit, prepared.handle, claimId);
  }

  reconcilePrepared(
    prepared: NativePreparedWorkspace,
    claimId: string,
  ): Promise<NativeWorkspaceCommitResult> {
    return this.#commitLike(
      Request.ReconcilePrepared,
      prepared.handle,
      claimId,
    );
  }

  async reconcileClaim(claimId: string): Promise<NativeWorkspaceCommitResult> {
    const result = await this.#call(
      Request.ReconcileClaim,
      new Encoder().string(claimId).build(),
    );
    return decodeCommitResult(result);
  }

  async abortPrepared(prepared: NativePreparedWorkspace): Promise<void> {
    await this.#call(
      Request.Abort,
      new Encoder().string(prepared.handle).build(),
    );
  }

  async proposeRecovery(
    prepared: NativePreparedWorkspace,
  ): Promise<"proposed" | "conflict"> {
    const response = await this.#call(
      Request.ProposeRecovery,
      new Encoder().string(prepared.handle).build(),
    );
    return decodeRecovery(response);
  }

  async proposeRecoveryClaim(
    claimId: string,
  ): Promise<"proposed" | "conflict"> {
    const response = await this.#call(
      Request.ProposeRecoveryClaim,
      new Encoder().string(claimId).build(),
    );
    return decodeRecovery(response);
  }

  /** Shutdown is main-owned. Pending work becomes indeterminate, never replayed. */
  async close(): Promise<void> {
    if (this.#closed) return;
    try {
      await this.#call(Request.Close, Buffer.alloc(0));
    } catch {
      // A crashing helper is deliberately equivalent to an indeterminate
      // effect. It is not retried by this client.
    }
    this.#closed = true;
    this.#child.kill();
  }

  async #commitLike(
    request: Request,
    handle: string,
    claimId: string,
  ): Promise<NativeWorkspaceCommitResult> {
    const result = await this.#call(
      request,
      new Encoder().string(handle).string(claimId).build(),
    );
    return decodeCommitResult(result);
  }

  #call(type: Request, body: Buffer): Promise<Buffer> {
    if (
      this.#closed ||
      this.#pending !== null ||
      body.byteLength > MAX_FRAME_BYTES
    ) {
      return Promise.reject(
        new NativeWorkspaceCommitHelperError("workspace_helper_failed"),
      );
    }
    const sequence = this.#nextSequence++;
    const sequenceBytes = Buffer.allocUnsafe(CHANNEL_SEQUENCE_BYTES);
    sequenceBytes.writeBigUInt64BE(sequence);
    const payload = Buffer.concat([
      sequenceBytes,
      Buffer.from([HELPER_PROTOCOL_VERSION, type]),
      body,
    ]);
    const frame = encodeFrame(this.#key, payload);
    return new Promise<Buffer>((resolve, reject) => {
      const timer = setTimeout(() => {
        this.#closed = true;
        this.#child.kill("SIGKILL");
        this.#failPending();
      }, this.#timeoutMs);
      this.#pending = { sequence, resolve, reject, timer };
      const stdin = this.#child.stdin;
      if (stdin === null) {
        this.#failPending();
        return;
      }
      stdin.write(frame, (error) => {
        if (error !== undefined && error !== null) this.#failPending();
      });
    });
  }

  #onData(chunk: Buffer): void {
    this.#stdout = Buffer.concat([this.#stdout, chunk]);
    const frame = decodeFrame(this.#key, this.#stdout);
    if (frame === null) return;
    this.#stdout = this.#stdout.subarray(frame.consumed);
    if (
      this.#pending === null ||
      frame.payload.byteLength < CHANNEL_SEQUENCE_BYTES + 2
    ) {
      this.#closed = true;
      this.#child.kill("SIGKILL");
      return;
    }
    const pending = this.#pending;
    this.#pending = null;
    clearTimeout(pending.timer);
    const sequence = frame.payload.readBigUInt64BE(0);
    if (sequence !== pending.sequence) {
      this.#closed = true;
      this.#child.kill("SIGKILL");
      pending.reject(
        new NativeWorkspaceCommitHelperError("workspace_helper_failed"),
      );
      return;
    }
    const version = frame.payload[CHANNEL_SEQUENCE_BYTES];
    const status = frame.payload[CHANNEL_SEQUENCE_BYTES + 1];
    if (version !== HELPER_PROTOCOL_VERSION) {
      pending.reject(
        new NativeWorkspaceCommitHelperError("workspace_helper_failed"),
      );
      return;
    }
    if (status !== 0) {
      pending.reject(toHelperError(frame.payload[CHANNEL_SEQUENCE_BYTES + 2]));
      return;
    }
    pending.resolve(frame.payload.subarray(CHANNEL_SEQUENCE_BYTES + 2));
  }

  #failPending(): void {
    const pending = this.#pending;
    this.#pending = null;
    if (pending === null) return;
    clearTimeout(pending.timer);
    pending.reject(
      new NativeWorkspaceCommitHelperError("workspace_helper_failed"),
    );
  }
}

/** Main-only resolution. The executable is never renderer-configurable. */
export function resolveNativeWorkspaceCommitHelperPath(input: {
  readonly packaged: boolean;
  readonly resourcesPath?: string;
  readonly appPath: string;
}): string {
  return input.packaged
    ? join(
        input.resourcesPath ?? "",
        "workspace-commit-helper",
        "workspace-commit-helper",
      )
    : join(
        input.appPath,
        "native",
        "workspace-commit-helper",
        "bin",
        "workspace-commit-helper",
      );
}

/**
 * Verify the exact nested helper that the macOS package builder signs. The
 * designated requirement prevents a merely-valid arbitrary local binary from
 * inheriting C2 authority when the application is packaged.
 */
export function verifyPackagedWorkspaceCommitHelper(path: string): boolean {
  const requirement = `anchor apple generic and identifier \"${WORKSPACE_HELPER_IDENTIFIER}\"`;
  const result = spawnSync(
    "/usr/bin/codesign",
    ["--verify", "--strict", "--verbose=2", "-R", requirement, path],
    { stdio: "ignore" },
  );
  return result.error === undefined && result.status === 0;
}

class Encoder {
  readonly #chunks: Buffer[] = [];

  u8(value: number): this {
    this.#chunks.push(Buffer.from([value]));
    return this;
  }
  u32(value: number): this {
    const out = Buffer.allocUnsafe(4);
    out.writeUInt32BE(value);
    this.#chunks.push(out);
    return this;
  }
  u64(value: number): this {
    const out = Buffer.allocUnsafe(8);
    out.writeBigUInt64BE(BigInt(value));
    this.#chunks.push(out);
    return this;
  }
  string(value: string): this {
    const encoded = Buffer.from(value, "utf8");
    return this.bytes(encoded);
  }
  bytes(value: Uint8Array): this {
    const data = Buffer.from(value);
    this.u32(data.byteLength);
    this.#chunks.push(data);
    return this;
  }
  build(): Buffer {
    return Buffer.concat(this.#chunks);
  }
}

class Decoder {
  #offset = 0;
  readonly #data: Buffer;
  constructor(data: Buffer) {
    this.#data = data;
  }
  u8(): number {
    this.#need(1);
    return this.#data[this.#offset++];
  }
  u32(): number {
    this.#need(4);
    const value = this.#data.readUInt32BE(this.#offset);
    this.#offset += 4;
    return value;
  }
  u64(): number {
    this.#need(8);
    const value = this.#data.readBigUInt64BE(this.#offset);
    this.#offset += 8;
    if (value > BigInt(Number.MAX_SAFE_INTEGER))
      throw new NativeWorkspaceCommitHelperError("workspace_helper_failed");
    return Number(value);
  }
  bytes(): Buffer {
    const length = this.u32();
    this.#need(length);
    const value = this.#data.subarray(this.#offset, this.#offset + length);
    this.#offset += length;
    return value;
  }
  string(): string {
    return this.bytes().toString("utf8");
  }
  done(): void {
    if (this.#offset !== this.#data.byteLength)
      throw new NativeWorkspaceCommitHelperError("workspace_helper_failed");
  }
  #need(length: number): void {
    if (length < 0 || this.#offset + length > this.#data.byteLength)
      throw new NativeWorkspaceCommitHelperError("workspace_helper_failed");
  }
}

function encodeEntry(encoder: Encoder, entry: WorkspaceChangeEntry): void {
  encoder.u8(operationCode(entry.operation)).string(entry.relativePath);
  encoder.u8(entry.destinationRelativePath === undefined ? 0 : 1);
  if (entry.destinationRelativePath !== undefined)
    encoder.string(entry.destinationRelativePath);
  encoder
    .u8(entry.precondition.exists ? 1 : 0)
    .u8(kindCode(entry.precondition.kind));
  encoder.string(entry.precondition.sha256 ?? "");
  encoder.u8(entry.contentSlot === undefined ? 0 : 1);
  if (entry.contentSlot !== undefined) {
    encoder
      .string(entry.contentSlot)
      .string(entry.contentDigest ?? "")
      .u64(entry.contentSize ?? 0);
  }
}

function operationCode(
  operation: WorkspaceChangeEntry["operation"],
): NativeOperation {
  switch (operation) {
    case "create":
      return NativeOperation.Create;
    case "replace":
      return NativeOperation.Replace;
    case "delete":
      return NativeOperation.Delete;
    case "move":
      return NativeOperation.Move;
    case "mkdir":
      return NativeOperation.Mkdir;
  }
}

function kindCode(kind: WorkspaceChangeEntry["precondition"]["kind"]): number {
  return kind === "file" ? 1 : kind === "directory" ? 2 : 0;
}

function decodeCommitResult(data: Buffer): NativeWorkspaceCommitResult {
  const reader = new Decoder(data);
  const outcome = outcomeFromCode(reader.u8());
  const receiptRef = reader.string();
  const resultDigest = reader.string() || undefined;
  const safeMessage = reader.string() || undefined;
  reader.done();
  return Object.freeze({ outcome, receiptRef, resultDigest, safeMessage });
}

function decodeRecovery(data: Buffer): "proposed" | "conflict" {
  const reader = new Decoder(data);
  const result = reader.u8() === 1 ? "proposed" : "conflict";
  reader.done();
  return result;
}

function outcomeFromCode(
  value: number,
): NativeWorkspaceCommitResult["outcome"] {
  switch (value) {
    case NativeOutcome.Applied:
      return "applied";
    case NativeOutcome.AlreadyApplied:
      return "already_applied";
    case NativeOutcome.PreconditionDrift:
      return "precondition_drift";
    case NativeOutcome.Failed:
      return "failed";
    default:
      return "indeterminate";
  }
}

function toHelperError(value: number): NativeWorkspaceCommitHelperError {
  if (value === NativeError.Unsupported)
    return new NativeWorkspaceCommitHelperError("workspace_write_unsupported");
  if (value === NativeError.Conflict)
    return new NativeWorkspaceCommitHelperError("workspace_conflict");
  if (value === NativeError.Drift)
    return new NativeWorkspaceCommitHelperError("workspace_precondition_drift");
  return new NativeWorkspaceCommitHelperError("workspace_helper_failed");
}

function encodeFrame(key: Buffer, payload: Buffer): Buffer {
  const length = Buffer.allocUnsafe(4);
  length.writeUInt32BE(payload.byteLength);
  const mac = createHmac("sha256", key).update(length).update(payload).digest();
  return Buffer.concat([length, mac, payload]);
}

function faultCode(
  boundary: NonNullable<NativeWorkspaceCommitHelperConfig["testCrashBoundary"]>,
): number {
  switch (boundary) {
    case "prepared":
      return 1;
    case "authorized":
      return 2;
    case "committing":
      return 3;
    case "effect":
      return 4;
  }
}

function decodeFrame(
  key: Buffer,
  data: Buffer,
): { readonly payload: Buffer; readonly consumed: number } | null {
  if (data.byteLength < 4 + MAC_BYTES) return null;
  const length = data.readUInt32BE(0);
  if (length > MAX_FRAME_BYTES)
    throw new NativeWorkspaceCommitHelperError("workspace_helper_failed");
  const total = 4 + MAC_BYTES + length;
  if (data.byteLength < total) return null;
  const expected = createHmac("sha256", key)
    .update(data.subarray(0, 4))
    .update(data.subarray(4 + MAC_BYTES, total))
    .digest();
  const actual = data.subarray(4, 4 + MAC_BYTES);
  if (
    actual.byteLength !== expected.byteLength ||
    !timingSafeEqual(expected, actual)
  ) {
    throw new NativeWorkspaceCommitHelperError("workspace_helper_failed");
  }
  return { payload: data.subarray(4 + MAC_BYTES, total), consumed: total };
}
