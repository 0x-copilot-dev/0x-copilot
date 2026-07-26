// @vitest-environment node
import { describe, expect, it, vi } from "vitest";

import {
  MacosWorkspaceConfinement,
  buildMacosWorkspaceSeatbeltProfile,
} from "./macos-workspace-confinement";

const config = {
  runtimeRoot: "/Applications/0xCopilot.app/Contents/Resources/runtime",
  webDir: "/Applications/0xCopilot.app/Contents/Resources/web",
  childDataDirs: [
    "/Users/alice/Library/Application Support/0xCopilot/agent-data/v1",
    "/Users/alice/Library/Application Support/0xCopilot/model-catalog",
  ],
  temporaryDir: "/private/var/folders/alice/T",
  pythonBin:
    "/Applications/0xCopilot.app/Contents/Resources/runtime/python/bin/python3",
  serviceDirs: [
    "/Applications/0xCopilot.app/Contents/Resources/runtime/services/ai-backend",
  ],
};

describe("MacosWorkspaceConfinement", () => {
  it("verifies the exact main-owned profile before it will wrap a child", async () => {
    const runSelfTest = vi.fn(() => ({ status: 0 }));
    const confinement = new MacosWorkspaceConfinement({
      ...config,
      platform: "darwin",
      sandboxExecPath: "/usr/bin/sandbox-exec",
      executableExists: () => true,
      runSelfTest,
    });
    await expect(confinement.verify()).resolves.toBe("enforced");
    expect(runSelfTest).toHaveBeenCalledWith(
      "/usr/bin/sandbox-exec",
      expect.arrayContaining(["-p", "/usr/bin/true"]),
    );
    expect(confinement.wrap("python3", ["-m", "uvicorn"])).toEqual({
      command: "/usr/bin/sandbox-exec",
      args: expect.arrayContaining(["-p", "python3", "-m", "uvicorn"]),
    });
  });

  it("fails closed when unsupported or when the profile self-test fails", async () => {
    const unsupported = new MacosWorkspaceConfinement({
      ...config,
      platform: "linux",
      executableExists: () => true,
    });
    await expect(unsupported.verify()).resolves.toBe("unavailable");
    expect(() => unsupported.wrap("python3", [])).toThrow(
      "workspace confinement is unavailable",
    );

    const rejected = new MacosWorkspaceConfinement({
      ...config,
      platform: "darwin",
      executableExists: () => true,
      runSelfTest: () => ({ status: 1 }),
    });
    await expect(rejected.verify()).resolves.toBe("unavailable");
    expect(() => rejected.wrap("python3", [])).toThrow(
      "workspace confinement is unavailable",
    );
  });

  it("never grants a confined Python child access to an ungranted workspace root", () => {
    const profile = buildMacosWorkspaceSeatbeltProfile(config);
    expect(profile).toContain("(deny default)");
    expect(profile).toContain(config.childDataDirs[0]);
    expect(profile).not.toContain("/Users/alice/Documents/customer-workspace");
    expect(profile).not.toContain(
      "/Users/alice/Library/Application Support/0xCopilot/capabilities/workspace-v2",
    );
    expect(profile).not.toContain(
      '(allow file-read* (subpath "/Users/alice"))',
    );
  });
});
