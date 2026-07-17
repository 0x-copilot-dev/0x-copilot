// @vitest-environment node
import { createServer } from "node:net";

import { describe, expect, it } from "vitest";

import { allocateFreePorts } from "./ports";

describe("allocateFreePorts", () => {
  it("allocates the requested number of distinct ports", async () => {
    const ports = await allocateFreePorts(4);
    expect(ports).toHaveLength(4);
    expect(new Set(ports).size).toBe(4);
    for (const port of ports) {
      expect(port).toBeGreaterThan(0);
      expect(port).toBeLessThanOrEqual(65_535);
    }
  });

  it("returns ports that are actually bindable afterwards", async () => {
    const [port] = await allocateFreePorts(1);
    await new Promise<void>((resolve, reject) => {
      const server = createServer();
      server.once("error", reject);
      server.listen(port, "127.0.0.1", () => {
        server.close(() => {
          resolve();
        });
      });
    });
  });

  it("rejects a non-positive count", async () => {
    await expect(allocateFreePorts(0)).rejects.toThrow(/positive integer/u);
  });
});
