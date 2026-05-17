// @vitest-environment node
import { describe, expect, it } from "vitest";

import { validateAdapterSchema } from "./schema";

const goodAdapter = () => ({
  scheme: "email",
  matches: (uri: string) => uri.startsWith("email://"),
  renderCurrent: (_state: unknown) => ({ type: "div", props: {}, key: null }),
  renderDiff: (_diff: unknown) => ({ type: "div", props: {}, key: null }),
  metadata: { origin: "agent-generated" as const, schemaVersion: 1 },
});

describe("Q1 — validateAdapterSchema", () => {
  it("accepts a well-formed adapter", () => {
    const result = validateAdapterSchema(goodAdapter());
    expect(result.ok).toBe(true);
  });

  it("rejects a non-object", () => {
    const result = validateAdapterSchema(null);
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.errors.length).toBeGreaterThan(0);
    }
  });

  it("rejects an empty object", () => {
    const result = validateAdapterSchema({});
    expect(result.ok).toBe(false);
  });

  it("rejects a missing scheme", () => {
    const a = goodAdapter() as Record<string, unknown>;
    delete a.scheme;
    const result = validateAdapterSchema(a);
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.errors.some((e) => e.path.join(".") === "scheme")).toBe(
        true,
      );
    }
  });

  it("rejects an empty scheme", () => {
    const a = { ...goodAdapter(), scheme: "" };
    const result = validateAdapterSchema(a);
    expect(result.ok).toBe(false);
  });

  it("rejects a missing matches function", () => {
    const a = goodAdapter() as Record<string, unknown>;
    delete a.matches;
    const result = validateAdapterSchema(a);
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.errors.some((e) => e.path.join(".") === "matches")).toBe(
        true,
      );
    }
  });

  it("rejects matches as a string instead of a function", () => {
    const a = { ...goodAdapter(), matches: "not a function" };
    const result = validateAdapterSchema(a);
    expect(result.ok).toBe(false);
  });

  it("rejects a missing renderCurrent function", () => {
    const a = goodAdapter() as Record<string, unknown>;
    delete a.renderCurrent;
    const result = validateAdapterSchema(a);
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(
        result.errors.some((e) => e.path.join(".") === "renderCurrent"),
      ).toBe(true);
    }
  });

  it("rejects a missing renderDiff function", () => {
    const a = goodAdapter() as Record<string, unknown>;
    delete a.renderDiff;
    const result = validateAdapterSchema(a);
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.errors.some((e) => e.path.join(".") === "renderDiff")).toBe(
        true,
      );
    }
  });

  it("rejects metadata of the wrong shape (missing origin)", () => {
    const a = { ...goodAdapter(), metadata: { schemaVersion: 1 } };
    const result = validateAdapterSchema(a);
    expect(result.ok).toBe(false);
  });

  it("rejects metadata.origin not in the allowed literal set", () => {
    const a = {
      ...goodAdapter(),
      metadata: { origin: "evil", schemaVersion: 1 },
    };
    const result = validateAdapterSchema(a);
    expect(result.ok).toBe(false);
  });

  it("rejects metadata.schemaVersion that is a string", () => {
    const a = {
      ...goodAdapter(),
      metadata: { origin: "agent-generated", schemaVersion: "1" },
    };
    const result = validateAdapterSchema(a);
    expect(result.ok).toBe(false);
  });

  it("rejects metadata.schemaVersion that is a negative integer", () => {
    const a = {
      ...goodAdapter(),
      metadata: { origin: "agent-generated", schemaVersion: -1 },
    };
    const result = validateAdapterSchema(a);
    expect(result.ok).toBe(false);
  });

  it("rejects metadata.schemaVersion that is a float", () => {
    const a = {
      ...goodAdapter(),
      metadata: { origin: "agent-generated", schemaVersion: 1.5 },
    };
    const result = validateAdapterSchema(a);
    expect(result.ok).toBe(false);
  });

  it("rejects extra unknown keys on metadata (strict)", () => {
    const a = {
      ...goodAdapter(),
      metadata: {
        origin: "agent-generated" as const,
        schemaVersion: 1,
        sneaky: true,
      },
    };
    const result = validateAdapterSchema(a);
    expect(result.ok).toBe(false);
  });

  it("accepts optional metadata.generatedAt and metadata.generatorModel", () => {
    const a = {
      ...goodAdapter(),
      metadata: {
        origin: "agent-generated" as const,
        schemaVersion: 1,
        generatedAt: "2026-05-17T00:00:00Z",
        generatorModel: "claude-opus-4-7",
      },
    };
    const result = validateAdapterSchema(a);
    expect(result.ok).toBe(true);
  });
});
