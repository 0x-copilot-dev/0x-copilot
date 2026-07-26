// @vitest-environment node
import { createHash } from "node:crypto";
import {
  mkdtempSync,
  mkdirSync,
  readFileSync,
  realpathSync,
  rmSync,
  symlinkSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterEach, describe, expect, it } from "vitest";

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

function digest(value: Uint8Array): string {
  return createHash("sha256").update(value).digest("hex");
}

function root(prefix: string): string {
  const value = realpathSync(mkdtempSync(join(tmpdir(), prefix)));
  roots.push(value);
  return value;
}

async function launch(): Promise<NativeWorkspaceCommitHelper> {
  const helper = await NativeWorkspaceCommitHelper.launch({
    executablePath: helperPath,
    timeoutMs: 2_000,
  });
  helpers.push(helper);
  return helper;
}

describeNative("native workspace commit helper", () => {
  afterEach(async () => {
    while (helpers.length > 0) await helpers.pop()!.close();
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
    const helper = await launch();
    const prepared = await helper.prepare(workspace, [
      {
        operation: "mkdir",
        relativePath: "drafts",
        precondition: { exists: false },
      },
    ]);
    await helper.close();
    helpers.pop();
    const restarted = await launch();
    await expect(
      restarted.reconcileClaim("claim_lost_1"),
    ).resolves.toMatchObject({ outcome: "indeterminate" });
    expect(() => readFileSync(join(workspace, "drafts"))).toThrow();
  });
});
