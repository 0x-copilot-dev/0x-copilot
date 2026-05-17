// @vitest-environment node
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { adapterFilePath, loadAdapterSource } from "./loader";

const GOOD_SOURCE = `
  import { createElement } from 'react';
  module.exports = {
    scheme: 'demo',
    matches: (uri) => uri.indexOf('demo://') === 0,
    renderCurrent: () => createElement('div'),
    renderDiff: () => createElement('div'),
    metadata: { origin: 'agent-generated', schemaVersion: 1 },
  };
`;

const FORBIDDEN_SOURCE = `
  import { createElement } from 'react';
  fetch('https://evil.example/');
  module.exports = {
    scheme: 'demo',
    matches: () => true,
    renderCurrent: () => createElement('div'),
    renderDiff: () => createElement('div'),
    metadata: { origin: 'agent-generated', schemaVersion: 1 },
  };
`;

describe("loadAdapterSource", () => {
  let dir: string;

  beforeEach(async () => {
    dir = await mkdtemp(join(tmpdir(), "tier2-loader-test-"));
  });

  afterEach(async () => {
    await rm(dir, { recursive: true, force: true });
  });

  it("returns source for a valid adapter on disk", async () => {
    await writeFile(join(dir, "demo-v1.js"), GOOD_SOURCE, "utf-8");
    const result = await loadAdapterSource({
      adapterDir: dir,
      scheme: "demo",
      version: 1,
    });
    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.source).toBe(GOOD_SOURCE);
    }
  });

  it("returns ast-violation when the adapter touches a forbidden global", async () => {
    await writeFile(join(dir, "demo-v1.js"), FORBIDDEN_SOURCE, "utf-8");
    const result = await loadAdapterSource({
      adapterDir: dir,
      scheme: "demo",
      version: 1,
    });
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.reason).toBe("ast-violation");
      if (result.reason === "ast-violation") {
        expect(
          result.violations.some((v) => v.kind === "forbidden-global"),
        ).toBe(true);
      }
    }
  });

  it("returns file-error when the file is missing", async () => {
    const result = await loadAdapterSource({
      adapterDir: dir,
      scheme: "missing",
      version: 1,
    });
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.reason).toBe("file-error");
    }
  });

  it("returns ast-violation for require() in source", async () => {
    const source = `const fs = require('fs'); module.exports = {};`;
    await writeFile(join(dir, "demo-v2.js"), source, "utf-8");
    const result = await loadAdapterSource({
      adapterDir: dir,
      scheme: "demo",
      version: 2,
    });
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.reason).toBe("ast-violation");
    }
  });

  it("computes the expected on-disk path", () => {
    const path = adapterFilePath({
      adapterDir: "/some/dir",
      scheme: "scheme",
      version: 3,
    });
    expect(path).toBe(join("/some/dir", "scheme-v3.js"));
  });
});
