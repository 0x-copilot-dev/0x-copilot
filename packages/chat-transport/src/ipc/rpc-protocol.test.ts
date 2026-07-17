import { describe, expect, it } from "vitest";

import {
  BootPhaseSchema,
  BootStatusPayloadSchema,
  CHANNELS,
  isAllowedChannel,
} from "./rpc-protocol";

describe("boot.status channel", () => {
  it("is present in the channel allowlist", () => {
    expect(CHANNELS.bootStatus).toBe("boot.status");
    expect(isAllowedChannel("boot.status")).toBe(true);
  });

  it("accepts a well-formed progress payload", () => {
    const parsed = BootStatusPayloadSchema.parse({
      phase: "postgres",
      message: "Starting local database…",
      percent: 30,
    });
    expect(parsed.phase).toBe("postgres");
    expect(parsed.fatal).toBeUndefined();
  });

  it("accepts a fatal payload that keeps the failing phase", () => {
    const parsed = BootStatusPayloadSchema.parse({
      phase: "migrations",
      message: "Database migrations failed",
      percent: 40,
      fatal: true,
    });
    expect(parsed.fatal).toBe(true);
  });

  it("rejects an unknown phase", () => {
    const result = BootStatusPayloadSchema.safeParse({
      phase: "warp-drive",
      message: "x",
      percent: 1,
    });
    expect(result.success).toBe(false);
  });

  it("rejects percent outside 0..100 and unknown keys", () => {
    expect(
      BootStatusPayloadSchema.safeParse({
        phase: "ready",
        message: "done",
        percent: 101,
      }).success,
    ).toBe(false);
    expect(
      BootStatusPayloadSchema.safeParse({
        phase: "ready",
        message: "done",
        percent: 100,
        extra: true,
      }).success,
    ).toBe(false);
  });

  it("orders phases boot-first, ready before stopping", () => {
    expect(BootPhaseSchema.options).toEqual([
      "secrets",
      "ports",
      "postgres",
      "migrations",
      "services",
      "health",
      "ready",
      "stopping",
    ]);
  });
});
