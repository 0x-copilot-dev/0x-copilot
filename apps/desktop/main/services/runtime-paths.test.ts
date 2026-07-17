// @vitest-environment node
import { describe, expect, it } from "vitest";

import { resolveRuntimePaths } from "./runtime-paths";

describe("resolveRuntimePaths (packaged, darwin-arm64)", () => {
  const paths = resolveRuntimePaths({
    resourcesPath: "/Applications/0xCopilot.app/Contents/Resources",
    platform: "darwin",
    arch: "arm64",
  });

  it("roots the runtime at <resourcesPath>/runtime/<platform>-<arch>", () => {
    expect(paths.runtimeRoot).toBe(
      "/Applications/0xCopilot.app/Contents/Resources/runtime/darwin-arm64",
    );
  });

  it("resolves the python interpreter under python/bin/python3", () => {
    expect(paths.pythonBin).toBe(
      "/Applications/0xCopilot.app/Contents/Resources/runtime/darwin-arm64/python/bin/python3",
    );
  });

  it("resolves postgres binaries under postgres/bin (not pgsql)", () => {
    expect(paths.pgBin.initdb).toBe(
      "/Applications/0xCopilot.app/Contents/Resources/runtime/darwin-arm64/postgres/bin/initdb",
    );
    expect(paths.pgBin.pgCtl).toBe(
      "/Applications/0xCopilot.app/Contents/Resources/runtime/darwin-arm64/postgres/bin/pg_ctl",
    );
    // The bundle ships no psql/pg_isready, so the contract omits them.
    expect(Object.keys(paths.pgBin).sort()).toEqual(["initdb", "pgCtl"]);
  });

  it("locates staged service dirs under services/<name>", () => {
    expect(paths.serviceDir("ai-backend")).toBe(
      "/Applications/0xCopilot.app/Contents/Resources/runtime/darwin-arm64/services/ai-backend",
    );
  });
});

describe("resolveRuntimePaths (dev override matches stage.mjs/run-local.mjs)", () => {
  it("appends runtime/<platform>-<arch> to COPILOT_RUNTIME_DIR", () => {
    const paths = resolveRuntimePaths({
      resourcesPath: "/ignored/when/override/set",
      runtimeDirOverride: "/repo/apps/desktop/resources",
      platform: "darwin",
      arch: "arm64",
    });
    // Exactly what run-local.mjs computes: join(dest, "runtime", `${platform}-${arch}`).
    expect(paths.runtimeRoot).toBe(
      "/repo/apps/desktop/resources/runtime/darwin-arm64",
    );
    expect(paths.pythonBin).toBe(
      "/repo/apps/desktop/resources/runtime/darwin-arm64/python/bin/python3",
    );
  });

  it("ignores an empty override string and uses resourcesPath", () => {
    const paths = resolveRuntimePaths({
      resourcesPath: "/res",
      runtimeDirOverride: "",
      platform: "darwin",
      arch: "x64",
    });
    expect(paths.runtimeRoot).toBe("/res/runtime/darwin-x64");
  });
});

describe("resolveRuntimePaths (windows target)", () => {
  // NB: node:path.join uses the HOST separator, so on a POSIX test runner the
  // win32-target paths come back with "/". Normalize before asserting shape.
  const paths = resolveRuntimePaths({
    resourcesPath: "C:/Program Files/0xCopilot/resources",
    platform: "win32",
    arch: "x64",
  });
  const py = paths.pythonBin.replace(/\\/gu, "/");

  it("uses python.exe at the python/ root (not under bin/)", () => {
    // install_only Windows CPython puts python.exe at the tree root.
    expect(py.endsWith("/win32-x64/python/python.exe")).toBe(true);
    expect(py).not.toContain("/bin/");
  });

  it("suffixes the postgres binaries with .exe under postgres/bin", () => {
    expect(paths.pgBin.initdb.replace(/\\/gu, "/")).toContain(
      "/win32-x64/postgres/bin/initdb.exe",
    );
    expect(paths.pgBin.pgCtl.endsWith("pg_ctl.exe")).toBe(true);
  });
});
