// @vitest-environment node
import { describe, expect, it } from "vitest";

import { browserFormPayloadDigest } from "./browser-engine";

const FORM = {
  actionUrl: "https://example.com/orders",
  method: "POST",
  enctype: "application/x-www-form-urlencoded",
  target: "_self",
} as const;

describe("browser form-payload digest", () => {
  it("is deterministic while keeping raw protected values out of the result", () => {
    const entries = [
      ["email", "person@example.com"],
      ["csrf", "private-token"],
    ] as const;

    const first = browserFormPayloadDigest({ ...FORM, entries });
    const second = browserFormPayloadDigest({ ...FORM, entries });

    expect(first).toBe(second);
    expect(first).toMatch(/^[a-f0-9]{64}$/u);
    expect(first).not.toContain("person@example.com");
    expect(first).not.toContain("private-token");
  });

  it("changes when any successful form value changes", () => {
    const approved = browserFormPayloadDigest({
      ...FORM,
      entries: [
        ["recipient", "alice"],
        ["amount", "10.00"],
      ],
    });
    const mutated = browserFormPayloadDigest({
      ...FORM,
      entries: [
        ["recipient", "mallory"],
        ["amount", "10.00"],
      ],
    });

    expect(mutated).not.toBe(approved);
  });

  it("binds order, destination, method, encoding, and target semantics", () => {
    const entries = [
      ["item", "one"],
      ["item", "two"],
    ] as const;
    const baseline = browserFormPayloadDigest({ ...FORM, entries });
    const variants = [
      browserFormPayloadDigest({
        ...FORM,
        entries: [...entries].reverse(),
      }),
      browserFormPayloadDigest({
        ...FORM,
        actionUrl: "https://example.com/other",
        entries,
      }),
      browserFormPayloadDigest({ ...FORM, method: "GET", entries }),
      browserFormPayloadDigest({ ...FORM, enctype: "text/plain", entries }),
      browserFormPayloadDigest({ ...FORM, target: "_blank", entries }),
    ];

    expect(new Set([baseline, ...variants])).toHaveLength(variants.length + 1);
  });
});
