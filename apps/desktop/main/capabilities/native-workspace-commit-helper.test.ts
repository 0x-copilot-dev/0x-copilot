// @vitest-environment node
import { createHash, createHmac } from "node:crypto";
import { spawn, type ChildProcess } from "node:child_process";
import { once } from "node:events";
import {
  closeSync,
  mkdtempSync,
  mkdirSync,
  openSync,
  readdirSync,
  readFileSync,
  realpathSync,
  renameSync,
  rmSync,
  symlinkSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterEach, describe, expect, it, vi } from "vitest";

import {
  NativeWorkspaceCommitHelper,
  NativeWorkspaceCommitHelperError,
  resolveNativeWorkspaceCommitHelperPath,
} from "./native-workspace-commit-helper";

const helperPath = resolveNativeWorkspaceCommitHelperPath({
  packaged: false,
  appPath: process.cwd(),
});
const describeNative = process.platform === "darwin" ? describe : describe.skip;
const roots: string[] = [];
const helpers: NativeWorkspaceCommitHelper[] = [];
const privateStores: PrivateStore[] = [];
const rawChildren: ChildProcess[] = [];

interface PrivateStore {
  readonly staging: string;
  readonly journal: string;
  readonly stagingFd: number;
  readonly journalFd: number;
  readonly journalKey: Buffer;
}

function digest(value: Uint8Array): string {
  return createHash("sha256").update(value).digest("hex");
}

function root(prefix: string): string {
  const value = realpathSync(mkdtempSync(join(tmpdir(), prefix)));
  roots.push(value);
  return value;
}

function privateStore(): PrivateStore {
  const base = root("copilot-native-private-");
  const staging = join(base, "staging");
  const journal = join(base, "journal");
  mkdirSync(staging, { mode: 0o700 });
  mkdirSync(journal, { mode: 0o700 });
  const store = {
    staging,
    journal,
    stagingFd: openSync(staging, "r"),
    journalFd: openSync(journal, "r"),
    journalKey: Buffer.alloc(32, 7),
  };
  privateStores.push(store);
  return store;
}

async function launch(
  store = privateStore(),
  testCrashBoundary?: "prepared" | "authorized" | "committing" | "effect",
): Promise<NativeWorkspaceCommitHelper> {
  const helper = await NativeWorkspaceCommitHelper.launch({
    executablePath: helperPath,
    timeoutMs: 2_000,
    stagingDirectoryFd: store.stagingFd,
    journalDirectoryFd: store.journalFd,
    journalIntegrityKey: store.journalKey,
    attestation: {
      workspaceWriteIsolation: "enforced",
      nativeWorkspacePrimitives: "available",
    },
    testCrashBoundary,
  });
  helpers.push(helper);
  return helper;
}

describeNative("native workspace commit helper", () => {
  afterEach(async () => {
    while (helpers.length > 0) await helpers.pop()!.close();
    while (rawChildren.length > 0) rawChildren.pop()!.kill();
    while (privateStores.length > 0) {
      const store = privateStores.pop()!;
      closeSync(store.stagingFd);
      closeSync(store.journalFd);
    }
    while (roots.length > 0)
      rmSync(roots.pop()!, { force: true, recursive: true });
  });

  it("uses retained parent handles for a digest-sealed atomic create", async () => {
    const workspace = root("copilot-native-commit-");
    mkdirSync(join(workspace, "notes"));
    const body = Buffer.from("# The native boundary\n", "utf8");
    const helper = await launch();

    const prepared = await helper.prepare(workspace, [
      {
        operation: "create",
        relativePath: "notes/plan.md",
        contentSlot: "body",
        contentDigest: digest(body),
        contentSize: body.byteLength,
        precondition: { exists: false },
      },
    ]);
    await helper.writePrepared(prepared, "body", body.subarray(0, 6));
    await helper.writePrepared(prepared, "body", body.subarray(6));
    await expect(helper.sealPrepared(prepared, "body")).resolves.toEqual({
      digest: digest(body),
      size: body.byteLength,
    });
    await expect(
      helper.commitPrepared(prepared, "claim_create_1"),
    ).resolves.toMatchObject({ outcome: "applied" });
    expect(readFileSync(join(workspace, "notes", "plan.md"), "utf8")).toBe(
      "# The native boundary\n",
    );
    await expect(
      helper.commitPrepared(prepared, "claim_create_1"),
    ).resolves.toMatchObject({ outcome: "already_applied" });
  });

  it("rejects an authenticated replayed request sequence on the private channel", async () => {
    const store = privateStore();
    const key = Buffer.alloc(32, 11);
    const child = spawn(helperPath, [], {
      cwd: "/",
      env: {},
      shell: false,
      stdio: [
        "pipe",
        "pipe",
        "ignore",
        "pipe",
        store.stagingFd,
        store.journalFd,
        "pipe",
      ],
    });
    rawChildren.push(child);
    await once(child, "spawn");
    const handles = child.stdio as Array<NodeJS.WritableStream | null>;
    handles[3]!.end(key);
    handles[6]!.end(store.journalKey);
    const payload = Buffer.alloc(10);
    payload.writeBigUInt64BE(1n);
    payload[8] = 2; // native helper protocol version
    payload[9] = 12; // PING
    const length = Buffer.alloc(4);
    length.writeUInt32BE(payload.byteLength);
    const frame = Buffer.concat([
      length,
      createHmac("sha256", key).update(length).update(payload).digest(),
      payload,
    ]);
    const response = once(child.stdout!, "data");
    child.stdin!.write(frame);
    await response;
    const exited = once(child, "exit");
    child.stdin!.write(frame); // exact authenticated replay of sequence 1
    const [code] = await exited;
    expect(code).toBe(0);
    rawChildren.pop();
  });

  it("fails closed when packaged helper signature verification is not accepted", async () => {
    const store = privateStore();
    const verify = vi.fn(() => false);
    await expect(
      NativeWorkspaceCommitHelper.launch({
        executablePath: helperPath,
        timeoutMs: 2_000,
        stagingDirectoryFd: store.stagingFd,
        journalDirectoryFd: store.journalFd,
        journalIntegrityKey: store.journalKey,
        attestation: {
          workspaceWriteIsolation: "enforced",
          nativeWorkspacePrimitives: "available",
        },
        packaged: true,
        verifyPackagedExecutable: verify,
      }),
    ).rejects.toMatchObject({ code: "workspace_write_unsupported" });
    expect(verify).toHaveBeenCalledWith(helperPath);
  });

  it("never commits attacker bytes after a sealed stage name is renamed and replaced", async () => {
    const workspace = root("copilot-native-sealed-swap-");
    const store = privateStore();
    const expected = Buffer.from("approved bytes only", "utf8");
    const helper = await launch(store);
    const prepared = await helper.prepare(workspace, [
      {
        operation: "create",
        relativePath: "sealed.md",
        contentSlot: "payload",
        contentDigest: digest(expected),
        contentSize: expected.byteLength,
        precondition: { exists: false },
      },
    ]);
    await helper.writePrepared(prepared, "payload", expected);
    await helper.sealPrepared(prepared, "payload");

    // This deliberately simulates a same-UID adversary that finds and swaps a
    // private stage filename after seal. The effect must use the retained FD,
    // re-attest its inode+digest, and never read the replacement pathname.
    const stageDir = join(store.staging, readdirSync(store.staging)[0]!);
    const [stageFile] = readdirSync(stageDir);
    const attacker = join(stageDir, "attacker-replacement");
    writeFileSync(attacker, "attacker bytes", "utf8");
    renameSync(attacker, join(stageDir, stageFile!));

    await expect(
      helper.commitPrepared(prepared, "claim_sealed_swap_1"),
    ).resolves.toMatchObject({ outcome: "applied" });
    expect(readFileSync(join(workspace, "sealed.md"), "utf8")).toBe(
      "approved bytes only",
    );
    expect(readFileSync(join(stageDir, stageFile!), "utf8")).toBe(
      "attacker bytes",
    );
  });

  it("rejects symlink traversal, parent replacement, and external create races", async () => {
    const workspace = root("copilot-native-race-");
    const outside = root("copilot-native-outside-");
    mkdirSync(join(workspace, "safe"));
    symlinkSync(outside, join(workspace, "safe", "escape"));
    const body = Buffer.from("safe", "utf8");
    const helper = await launch();

    await expect(
      helper.prepare(workspace, [
        {
          operation: "create",
          relativePath: "safe/escape/pwned.md",
          contentSlot: "body",
          contentDigest: digest(body),
          contentSize: body.byteLength,
          precondition: { exists: false },
        },
      ]),
    ).rejects.toBeInstanceOf(NativeWorkspaceCommitHelperError);

    mkdirSync(join(workspace, "Notes"));
    await expect(
      helper.prepare(workspace, [
        {
          operation: "create",
          relativePath: "notes/case.md",
          contentSlot: "body",
          contentDigest: digest(body),
          contentSize: body.byteLength,
          precondition: { exists: false },
        },
      ]),
    ).rejects.toMatchObject({ code: "workspace_conflict" });
    await expect(
      helper.prepare(workspace, [
        {
          operation: "create",
          relativePath: "safe/cafe\u0301.md",
          contentSlot: "body",
          contentDigest: digest(body),
          contentSize: body.byteLength,
          precondition: { exists: false },
        },
      ]),
    ).rejects.toMatchObject({ code: "workspace_conflict" });

    const prepared = await helper.prepare(workspace, [
      {
        operation: "create",
        relativePath: "safe/race.md",
        contentSlot: "body",
        contentDigest: digest(body),
        contentSize: body.byteLength,
        precondition: { exists: false },
      },
    ]);
    await helper.writePrepared(prepared, "body", body);
    await helper.sealPrepared(prepared, "body");
    writeFileSync(join(workspace, "safe", "race.md"), "outside writer", "utf8");
    await expect(
      helper.commitPrepared(prepared, "claim_race_1"),
    ).resolves.toMatchObject({ outcome: "precondition_drift" });
    expect(readFileSync(join(workspace, "safe", "race.md"), "utf8")).toBe(
      "outside writer",
    );
  });

  it("enforces exact size/digest before the no-replace filesystem effect", async () => {
    const workspace = root("copilot-native-digest-");
    const expected = Buffer.from("expected", "utf8");
    const helper = await launch();
    const prepared = await helper.prepare(workspace, [
      {
        operation: "create",
        relativePath: "report.csv",
        contentSlot: "payload",
        contentDigest: digest(expected),
        contentSize: expected.byteLength,
        precondition: { exists: false },
      },
    ]);
    await helper.writePrepared(
      prepared,
      "payload",
      Buffer.from("wrong", "utf8"),
    );
    await expect(
      helper.sealPrepared(prepared, "payload"),
    ).rejects.toMatchObject({ code: "workspace_conflict" });
    await expect(
      helper.commitPrepared(prepared, "claim_wrong_1"),
    ).resolves.toMatchObject({ outcome: "precondition_drift" });
    expect(() => readFileSync(join(workspace, "report.csv"))).toThrow();
  });

  it("records identity-checked private staging cleanup on abort", async () => {
    const workspace = root("copilot-native-cleanup-");
    const store = privateStore();
    const helper = await launch(store);
    const body = Buffer.from("discarded", "utf8");
    const prepared = await helper.prepare(workspace, [
      {
        operation: "create",
        relativePath: "discarded.md",
        contentSlot: "payload",
        contentDigest: digest(body),
        contentSize: body.byteLength,
        precondition: { exists: false },
      },
    ]);
    const stageDir = join(store.staging, readdirSync(store.staging)[0]!);
    expect(readdirSync(stageDir)).toHaveLength(1);
    await helper.abortPrepared(prepared);
    expect(readdirSync(stageDir)).toHaveLength(0);
    expect(() => readFileSync(join(workspace, "discarded.md"))).toThrow();
  });

  it("fails closed for non-CAS replace/delete/move rather than using an advisory lock", async () => {
    const workspace = root("copilot-native-no-cas-");
    writeFileSync(join(workspace, "existing.md"), "original", "utf8");
    const body = Buffer.from("replacement", "utf8");
    const helper = await launch();
    await expect(
      helper.prepare(workspace, [
        {
          operation: "replace",
          relativePath: "existing.md",
          contentSlot: "payload",
          contentDigest: digest(body),
          contentSize: body.byteLength,
          precondition: {
            exists: true,
            kind: "file",
            sha256: digest(Buffer.from("original")),
          },
        },
      ]),
    ).rejects.toMatchObject({ code: "workspace_conflict" });
    expect(readFileSync(join(workspace, "existing.md"), "utf8")).toBe(
      "original",
    );
  });

  it("never guesses after helper loss: a new private channel reconciles only as indeterminate", async () => {
    const workspace = root("copilot-native-crash-");
    const store = privateStore();
    const helper = await launch(store);
    const prepared = await helper.prepare(workspace, [
      {
        operation: "mkdir",
        relativePath: "drafts",
        precondition: { exists: false },
      },
    ]);
    await helper.close();
    helpers.pop();
    const restarted = await launch(store);
    await expect(
      restarted.reconcileClaim("claim_lost_1"),
    ).resolves.toMatchObject({ outcome: "indeterminate" });
    expect(() => readFileSync(join(workspace, "drafts"))).toThrow();
  });

  it("persists applied claims across a helper restart instead of relying on volatile memory", async () => {
    const workspace = root("copilot-native-journal-applied-");
    const store = privateStore();
    const helper = await launch(store);
    const prepared = await helper.prepare(workspace, [
      {
        operation: "mkdir",
        relativePath: "durable",
        precondition: { exists: false },
      },
    ]);
    await expect(
      helper.commitPrepared(prepared, "claim_durable_applied_1"),
    ).resolves.toMatchObject({ outcome: "applied" });
    await helper.close();
    helpers.pop();
    const restarted = await launch(store);
    await expect(
      restarted.reconcileClaim("claim_durable_applied_1"),
    ).resolves.toMatchObject({ outcome: "already_applied" });
  });

  it("atomically admits exactly one of two helpers for the same approved create", async () => {
    const workspace = root("copilot-native-claim-race-");
    const store = privateStore();
    const body = Buffer.from("one durable native effect", "utf8");
    const [first, second] = await Promise.all([launch(store), launch(store)]);
    const entries = [
      {
        operation: "create" as const,
        relativePath: "race.md",
        contentSlot: "payload",
        contentDigest: digest(body),
        contentSize: body.byteLength,
        precondition: { exists: false as const },
      },
    ];
    const [firstPrepared, secondPrepared] = await Promise.all([
      first.prepare(workspace, entries),
      second.prepare(workspace, entries),
    ]);
    await Promise.all([
      first.writePrepared(firstPrepared, "payload", body),
      second.writePrepared(secondPrepared, "payload", body),
    ]);
    await Promise.all([
      first.sealPrepared(firstPrepared, "payload"),
      second.sealPrepared(secondPrepared, "payload"),
    ]);

    // This is a true cross-process race over one private journal directory.
    // Exactly one O_EXCL creator may reach fclonefileat; the other helper sees
    // the winner's record and returns an existing/recovery outcome.
    const results = await Promise.all([
      first.commitPrepared(firstPrepared, "claim_two_helper_create_1"),
      second.commitPrepared(secondPrepared, "claim_two_helper_create_1"),
    ]);
    const outcomes = results.map((result) => result.outcome);
    expect(outcomes.filter((outcome) => outcome === "applied")).toHaveLength(1);
    expect(
      outcomes.filter(
        (outcome) =>
          outcome === "already_applied" || outcome === "indeterminate",
      ),
    ).toHaveLength(1);
    expect(readFileSync(join(workspace, "race.md"), "utf8")).toBe(
      "one durable native effect",
    );
    expect(
      readdirSync(store.journal).filter((name) => name.startsWith("c2c-")),
    ).toHaveLength(1);

    await first.close();
    helpers.splice(helpers.indexOf(first), 1);
    await second.close();
    helpers.splice(helpers.indexOf(second), 1);
    const recovered = await launch(store);
    await expect(
      recovered.reconcileClaim("claim_two_helper_create_1"),
    ).resolves.toMatchObject({ outcome: "already_applied" });
  });

  it("fails closed when a second helper binds an existing claim to another effect", async () => {
    const workspace = root("copilot-native-claim-binding-");
    const store = privateStore();
    const approved = Buffer.from("approved effect A", "utf8");
    const foreign = Buffer.from("foreign effect B", "utf8");
    const [owner, foreignHelper] = await Promise.all([
      launch(store),
      launch(store),
    ]);
    const prepareCreate = async (
      helper: NativeWorkspaceCommitHelper,
      body: Buffer,
    ) => {
      const prepared = await helper.prepare(workspace, [
        {
          operation: "create",
          relativePath: "binding.md",
          contentSlot: "payload",
          contentDigest: digest(body),
          contentSize: body.byteLength,
          precondition: { exists: false },
        },
      ]);
      await helper.writePrepared(prepared, "payload", body);
      await helper.sealPrepared(prepared, "payload");
      return prepared;
    };
    const [ownedPrepared, foreignPrepared] = await Promise.all([
      prepareCreate(owner, approved),
      prepareCreate(foreignHelper, foreign),
    ]);
    await expect(
      owner.commitPrepared(ownedPrepared, "claim_binding_conflict_1"),
    ).resolves.toMatchObject({ outcome: "applied" });
    await expect(
      foreignHelper.commitPrepared(foreignPrepared, "claim_binding_conflict_1"),
    ).rejects.toMatchObject({ code: "workspace_conflict" });
    expect(readFileSync(join(workspace, "binding.md"), "utf8")).toBe(
      "approved effect A",
    );
    expect(
      readdirSync(store.journal).filter((name) => name.startsWith("c2c-")),
    ).toHaveLength(1);
  });

  it("fails closed on a tampered durable lifecycle record", async () => {
    const workspace = root("copilot-native-journal-tamper-");
    const store = privateStore();
    const helper = await launch(store);
    const prepared = await helper.prepare(workspace, [
      {
        operation: "mkdir",
        relativePath: "tamper",
        precondition: { exists: false },
      },
    ]);
    await helper.commitPrepared(prepared, "claim_tamper_1");
    await helper.close();
    helpers.pop();
    const journalFile = readdirSync(store.journal).find((name) =>
      name.startsWith("c2j-"),
    );
    const journalPath = join(store.journal, journalFile!);
    const tampered = readFileSync(journalPath);
    tampered[0] ^= 0xff;
    writeFileSync(journalPath, tampered);
    await expect(launch(store)).rejects.toMatchObject({
      code: "workspace_helper_failed",
    });
  });

  it("marks a crash after durable committing as indeterminate and never replays it", async () => {
    const workspace = root("copilot-native-journal-committing-");
    const store = privateStore();
    const helper = await launch(store, "committing");
    const body = Buffer.from("must not arrive", "utf8");
    const prepared = await helper.prepare(workspace, [
      {
        operation: "create",
        relativePath: "boundary.md",
        contentSlot: "payload",
        contentDigest: digest(body),
        contentSize: body.byteLength,
        precondition: { exists: false },
      },
    ]);
    await helper.writePrepared(prepared, "payload", body);
    await helper.sealPrepared(prepared, "payload");
    await expect(
      helper.commitPrepared(prepared, "claim_crash_committing_1"),
    ).rejects.toMatchObject({ code: "workspace_helper_failed" });
    const restarted = await launch(store);
    await expect(
      restarted.reconcileClaim("claim_crash_committing_1"),
    ).resolves.toMatchObject({ outcome: "indeterminate" });
    expect(() => readFileSync(join(workspace, "boundary.md"))).toThrow();
  });

  it("records effect-boundary loss as indeterminate without a second mutation on restart", async () => {
    const workspace = root("copilot-native-journal-effect-");
    const store = privateStore();
    const helper = await launch(store, "effect");
    const body = Buffer.from("single durable side effect", "utf8");
    const prepared = await helper.prepare(workspace, [
      {
        operation: "create",
        relativePath: "effect.md",
        contentSlot: "payload",
        contentDigest: digest(body),
        contentSize: body.byteLength,
        precondition: { exists: false },
      },
    ]);
    await helper.writePrepared(prepared, "payload", body);
    await helper.sealPrepared(prepared, "payload");
    await expect(
      helper.commitPrepared(prepared, "claim_crash_effect_1"),
    ).rejects.toMatchObject({ code: "workspace_helper_failed" });
    expect(readFileSync(join(workspace, "effect.md"), "utf8")).toBe(
      "single durable side effect",
    );
    const restarted = await launch(store);
    await expect(
      restarted.reconcileClaim("claim_crash_effect_1"),
    ).resolves.toMatchObject({ outcome: "indeterminate" });
    expect(readFileSync(join(workspace, "effect.md"), "utf8")).toBe(
      "single durable side effect",
    );
  });
});
