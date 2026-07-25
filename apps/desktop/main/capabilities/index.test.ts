// @vitest-environment node
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { SafeStorageLike } from "../auth/secret-storage";
import { CAPABILITY_BROKER_PROTOCOL, createCapabilityService } from ".";

function safeStorage(): SafeStorageLike {
  return {
    isEncryptionAvailable: () => true,
    encryptString: (value) => Buffer.from(value, "utf8"),
    decryptString: (value) => value.toString("utf8"),
  };
}

describe("createCapabilityService workspace composition", () => {
  let userDataDir: string;

  beforeEach(() => {
    userDataDir = mkdtempSync(join(tmpdir(), "workspace-authority-"));
  });

  afterEach(() => {
    rmSync(userDataDir, { recursive: true, force: true });
  });

  it("installs a fail-closed v2 authority by default and retires boot-bearer filesystem access", async () => {
    const service = createCapabilityService({
      userDataDir,
      safeStorage: safeStorage(),
      showOpenDialog: async () => ({ canceled: true, filePaths: [] }),
    });
    const broker = await service.startBroker();
    try {
      expect(service.workspaceWriteAttestation()).toEqual({
        workspaceWriteIsolation: "unavailable",
        nativeWorkspacePrimitives: "unavailable",
      });
      expect(service.workspaceWritesAvailable()).toBe(false);
      const headers = {
        authorization: `Bearer ${service.brokerAuthToken()}`,
        "x-capability-protocol": CAPABILITY_BROKER_PROTOCOL,
        "content-type": "application/json",
      };
      const response = await fetch(`${broker.baseUrl}/v1/fs/write`, {
        method: "POST",
        headers,
        body: JSON.stringify({
          grant_id: "grant_1",
          path: "must-not-write.md",
          content_base64: "eA==",
        }),
      });
      expect(response.status).toBe(404);
      expect(await response.json()).toEqual({ error: "unsupported" });

      const read = await fetch(`${broker.baseUrl}/v1/fs/read`, {
        method: "POST",
        headers,
        body: JSON.stringify({ grant_id: "grant_1", path: "must-not-read.md" }),
      });
      expect(read.status).toBe(404);
      expect(await read.json()).toEqual({ error: "unsupported" });

      const handshake = await fetch(`${broker.baseUrl}/v1/handshake`, {
        method: "POST",
        headers,
        body: "{}",
      });
      const advertised = (await handshake.json()) as {
        methods: readonly string[];
      };
      expect(advertised.methods).not.toContain("readFile");
      expect(advertised.methods).not.toContain("writeFile");
      expect(advertised.methods).not.toContain("prepareWorkspaceEffect");
    } finally {
      await service.stopBroker();
    }
  });
});
