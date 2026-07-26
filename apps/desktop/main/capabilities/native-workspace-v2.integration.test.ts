// @vitest-environment node
//
// This suite drives the packaged native addon itself. It is intentionally
// skipped when the local host has not built a Darwin addon; source-level
// contract tests still cover the unavailable path on every platform.

import { createHash } from "node:crypto";
import {
  mkdtempSync,
  mkdirSync,
  readFileSync,
  realpathSync,
  rmSync,
  symlinkSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createRequire } from "node:module";

import { afterEach, describe, expect, it } from "vitest";

import type { NativeWorkspaceV2Bindings } from "./native-workspace-authority";

const require = createRequire(import.meta.url);

function loadV2(): NativeWorkspaceV2Bindings | undefined {
  if (process.platform !== "darwin") return undefined;
  const module = require("../../native/workspace-fs/index.cjs") as {
    loadNative?: () => unknown;
  };
  const value = module.loadNative?.();
  if (value === undefined || typeof value !== "object") return undefined;
  const candidate = value as Record<string, unknown>;
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
  ].every((name) => typeof candidate[name] === "function")
    ? (value as NativeWorkspaceV2Bindings)
    : undefined;
}

const native = loadV2();
const describeNative = native === undefined ? describe.skip : describe;

describeNative("Darwin retained-handle workspace authority", () => {
  const roots: string[] = [];

  afterEach(() => {
    while (roots.length > 0) {
      rmSync(roots.pop()!, { force: true, recursive: true });
    }
  });

  function root(prefix: string): string {
    const value = realpathSync(mkdtempSync(join(tmpdir(), prefix)));
    roots.push(value);
    return value;
  }

  function digest(value: Uint8Array): string {
    return createHash("sha256").update(value).digest("hex");
  }

  it("writes only through its retained root handle and commits a sealed payload once", () => {
    const workspace = root("copilot-native-workspace-");
    const body = new TextEncoder().encode("# Native workspace\n");
    // The parent must exist. This is an intentional workspace operation rather
    // than an implicit path-creation side effect.
    const makeDirectory = native!.workspacePrepare(workspace, [
      {
        operation: "mkdir",
        relativePath: "notes",
        precondition: { exists: false },
      },
    ]);
    expect(
      native!.workspaceCommit(makeDirectory.handle, "claim_mkdir"),
    ).toMatchObject({
      outcome: "applied",
    });

    const staged = native!.workspacePrepare(workspace, [
      {
        operation: "create",
        relativePath: "notes/plan.md",
        contentSlot: "plan_body",
        contentDigest: digest(body),
        contentSize: body.byteLength,
        precondition: { exists: false },
      },
    ]);
    native!.workspaceWrite(staged.handle, "plan_body", body);
    expect(native!.workspaceSeal(staged.handle, "plan_body")).toEqual({
      digest: digest(body),
      size: body.byteLength,
    });
    expect(native!.workspaceCommit(staged.handle, "claim_plan")).toMatchObject({
      outcome: "applied",
    });
    expect(readFileSync(join(workspace, "notes", "plan.md"), "utf8")).toBe(
      "# Native workspace\n",
    );
    expect(native!.workspaceReconcileClaim("claim_plan")).toMatchObject({
      outcome: "already_applied",
    });
  });

  it("rejects traversal and every symlink escape before a staged handle exists", () => {
    const workspace = root("copilot-native-workspace-");
    const outside = root("copilot-native-outside-");
    mkdirSync(join(workspace, "safe"));
    symlinkSync(outside, join(workspace, "safe", "escape"));
    const body = new TextEncoder().encode("blocked");

    expect(() =>
      native!.workspacePrepare(workspace, [
        {
          operation: "create",
          relativePath: "../outside.md",
          contentSlot: "bad_path",
          contentDigest: digest(body),
          contentSize: body.byteLength,
          precondition: { exists: false },
        },
      ]),
    ).toThrow();
    expect(() =>
      native!.workspacePrepare(workspace, [
        {
          operation: "create",
          relativePath: "safe/escape/outside.md",
          contentSlot: "symlink_escape",
          contentDigest: digest(body),
          contentSize: body.byteLength,
          precondition: { exists: false },
        },
      ]),
    ).toThrow();
    expect(() => readFileSync(join(outside, "outside.md"))).toThrow();
  });
});
