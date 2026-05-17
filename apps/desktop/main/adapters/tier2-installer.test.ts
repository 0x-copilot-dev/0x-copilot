// @vitest-environment node
import { mkdtempSync } from "node:fs";
import { mkdir, readFile, unlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { beforeEach, describe, expect, it } from "vitest";

import { loadAdapterSource } from "./loader";
import {
  adapterFilePath,
  persistAdapterSource,
  uninstallAdapterFile,
  type InstallerDeps,
} from "./tier2-installer";

let tmpDir: string;

beforeEach(() => {
  tmpDir = mkdtempSync(join(tmpdir(), "tier2-installer-"));
});

function deps(): InstallerDeps {
  return { fs: { writeFile, mkdir, unlink } };
}

const ALLOWED_SOURCE = [
  "const adapter = {",
  '  scheme: "email",',
  '  matches: (uri) => uri.startsWith("email://"),',
  '  renderCurrent: () => ({ tag: "div" }),',
  '  renderDiff: () => ({ tag: "div" }),',
  '  metadata: { origin: "agent-generated", schemaVersion: 1 },',
  "};",
  "module.exports = adapter;",
].join("\n");

describe("persistAdapterSource", () => {
  it("writes the source at the canonical path", async () => {
    const adapterDir = join(tmpDir, "adapters");
    await persistAdapterSource(
      { adapterDir, scheme: "email", version: 1, source: ALLOWED_SOURCE },
      deps(),
    );
    const path = adapterFilePath({ adapterDir, scheme: "email", version: 1 });
    const onDisk = await readFile(path, "utf8");
    expect(onDisk).toBe(ALLOWED_SOURCE);
  });

  it("creates the parent directory recursively", async () => {
    const adapterDir = join(tmpDir, "nested", "deeper", "adapters");
    await persistAdapterSource(
      { adapterDir, scheme: "email", version: 1, source: ALLOWED_SOURCE },
      deps(),
    );
    const path = adapterFilePath({ adapterDir, scheme: "email", version: 1 });
    const onDisk = await readFile(path, "utf8");
    expect(onDisk).toBe(ALLOWED_SOURCE);
  });

  it("filename layout matches 6A's loader (round-trip)", async () => {
    const adapterDir = join(tmpDir, "adapters");
    await persistAdapterSource(
      { adapterDir, scheme: "email", version: 3, source: ALLOWED_SOURCE },
      deps(),
    );
    const result = await loadAdapterSource({
      adapterDir,
      scheme: "email",
      version: 3,
    });
    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.source).toBe(ALLOWED_SOURCE);
    }
  });

  it("replaces an existing version atomically", async () => {
    const adapterDir = join(tmpDir, "adapters");
    await persistAdapterSource(
      { adapterDir, scheme: "email", version: 2, source: "module.exports={};" },
      deps(),
    );
    await persistAdapterSource(
      { adapterDir, scheme: "email", version: 2, source: ALLOWED_SOURCE },
      deps(),
    );
    const path = adapterFilePath({ adapterDir, scheme: "email", version: 2 });
    const onDisk = await readFile(path, "utf8");
    expect(onDisk).toBe(ALLOWED_SOURCE);
  });
});

describe("uninstallAdapterFile", () => {
  it("removes the file at the canonical path", async () => {
    const adapterDir = join(tmpDir, "adapters");
    await persistAdapterSource(
      { adapterDir, scheme: "email", version: 1, source: ALLOWED_SOURCE },
      deps(),
    );
    await uninstallAdapterFile(
      { adapterDir, scheme: "email", version: 1 },
      deps(),
    );
    const result = await loadAdapterSource({
      adapterDir,
      scheme: "email",
      version: 1,
    });
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.reason).toBe("file-error");
    }
  });

  it("is a no-op when the file is missing", async () => {
    const adapterDir = join(tmpDir, "adapters");
    await uninstallAdapterFile(
      { adapterDir, scheme: "email", version: 1 },
      deps(),
    );
    expect(true).toBe(true);
  });
});
