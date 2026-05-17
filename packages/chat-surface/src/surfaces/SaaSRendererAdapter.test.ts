import { createElement, type ReactElement } from "react";
import { describe, expect, it } from "vitest";

import {
  TIER3_SCHEME,
  type SaaSRendererAdapter,
  type SaaSRendererAdapterMetadata,
  type SaaSRendererAdapterOrigin,
} from "./SaaSRendererAdapter";

describe("SaaSRendererAdapter", () => {
  it("TIER3_SCHEME is the wildcard '*'", () => {
    expect(TIER3_SCHEME).toBe("*");
  });

  it("accepts concrete TResource / TDiff generics", () => {
    interface EmailResource {
      readonly id: string;
    }
    interface EmailDiff {
      readonly diffId: string;
    }
    const adapter: SaaSRendererAdapter<EmailResource, EmailDiff> = {
      scheme: "email",
      matches: (uri) => uri.startsWith("email://"),
      renderCurrent: (state): ReactElement =>
        createElement("div", { "data-id": state.id }),
      renderDiff: (diff): ReactElement =>
        createElement("div", { "data-diff": diff.diffId }),
      metadata: {
        origin: "first-party",
        schemaVersion: 1,
      },
    };
    expect(adapter.scheme).toBe("email");
    expect(adapter.matches("email://draft-1")).toBe(true);
    expect(adapter.matches("sf-opp://o1")).toBe(false);
  });

  it("metadata origin is restricted to the three documented values", () => {
    const origins: SaaSRendererAdapterOrigin[] = [
      "first-party",
      "agent-generated",
      "community",
    ];
    for (const origin of origins) {
      const meta: SaaSRendererAdapterMetadata = {
        origin,
        schemaVersion: 1,
      };
      expect(meta.origin).toBe(origin);
    }
  });

  it("metadata supports optional generatedAt and generatorModel", () => {
    const meta: SaaSRendererAdapterMetadata = {
      origin: "agent-generated",
      schemaVersion: 7,
      generatedAt: "2026-05-17T00:00:00Z",
      generatorModel: "claude-opus-4-7",
    };
    expect(meta.generatedAt).toBe("2026-05-17T00:00:00Z");
    expect(meta.generatorModel).toBe("claude-opus-4-7");
  });
});
