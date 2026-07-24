// @vitest-environment node
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { SafeStorageLike } from "../auth/secret-storage";
import type { WorkspaceJournalRecord } from "./workspace-authority";
import { EncryptedWorkspaceJournalStore } from "./workspace-journal";

function storage(available = true): SafeStorageLike {
  return {
    isEncryptionAvailable: () => available,
    encryptString: (value) =>
      Buffer.from(Buffer.from(value, "utf8").map((byte) => byte ^ 0x6d)),
    decryptString: (value) =>
      Buffer.from(value.map((byte) => byte ^ 0x6d)).toString("utf8"),
  };
}

function record(): WorkspaceJournalRecord {
  return {
    preparedRef: "workspace-prepared://prepared_1",
    state: "prepared",
    runId: "run_1",
    userId: "user_1",
    deviceId: "device_1",
    stageId: "stage_1",
    revision: 1,
    decisionLedgerId: "rrun·7",
    pathTokens: ["path_safe-token"],
    changeSetDigest: "a".repeat(64),
    targetDigest: "b".repeat(64),
    proposalDigest: "c".repeat(64),
    createdAt: 1,
    updatedAt: 1,
  };
}

describe("EncryptedWorkspaceJournalStore", () => {
  let userData: string;

  beforeEach(() => {
    userData = mkdtempSync(join(tmpdir(), "workspace-journal-"));
  });

  afterEach(() => {
    rmSync(userData, { recursive: true, force: true });
  });

  function build(available = true) {
    return new EncryptedWorkspaceJournalStore({
      userDataDir: userData,
      safeStorage: storage(available),
      integrityKey: Buffer.alloc(32, 7),
    });
  }

  it("atomically persists encrypted path-free records and reopens them", async () => {
    const store = build();
    await store.put(record());
    const disk = readFileSync(
      join(userData, "capabilities", "workspace-journal.bin"),
    );
    expect(disk.toString("utf8")).toContain(
      "COPILOT_WORKSPACE_JOURNAL_V1:cipher:",
    );
    expect(disk.toString("utf8")).not.toContain("workspace-prepared://");
    expect(disk.toString("utf8")).not.toContain("/Users/");

    const reopened = build();
    expect(await reopened.get("workspace-prepared://prepared_1")).toEqual(
      record(),
    );
  });

  it("fails closed when journal ciphertext is tampered", async () => {
    const store = build();
    await store.put(record());
    const path = join(userData, "capabilities", "workspace-journal.bin");
    const raw = readFileSync(path);
    raw[raw.length - 1] = raw[raw.length - 1] === 0x61 ? 0x62 : 0x61;
    writeFileSync(path, raw);

    await expect(build().listNonterminal()).rejects.toThrow(/integrity/u);
  });

  it("does not permit plaintext storage without an explicit dev fallback", async () => {
    await expect(build(false).put(record())).rejects.toThrow(/plaintext/u);
  });
});
