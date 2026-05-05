import { describe, expect, it } from "vitest";
import {
  firstNameFromDisplayName,
  greetingForHour,
  welcomeGreeting,
} from "./greeting";

describe("greetingForHour", () => {
  it.each([
    [0, "late"],
    [4, "late"],
    [5, "morning"],
    [11, "morning"],
    [12, "afternoon"],
    [17, "afternoon"],
    [18, "evening"],
    [22, "evening"],
    [23, "late"],
  ] as const)("hour %d → %s", (hour, bucket) => {
    expect(greetingForHour(hour)).toBe(bucket);
  });
});

describe("welcomeGreeting", () => {
  function at(hour: number): Date {
    const d = new Date(2026, 4, 5, hour, 0, 0, 0);
    return d;
  }

  it("morning with name", () => {
    expect(welcomeGreeting(at(8), "Sarah")).toBe("Good morning, Sarah.");
  });
  it("afternoon without name", () => {
    expect(welcomeGreeting(at(14), null)).toBe("Good afternoon.");
  });
  it("evening with empty string treats as no name", () => {
    expect(welcomeGreeting(at(20), "")).toBe("Good evening.");
  });
  it("late night past 23:00", () => {
    expect(welcomeGreeting(at(23), "Sarah")).toBe("Working late, Sarah.");
  });
  it("late night at midnight", () => {
    expect(welcomeGreeting(at(0), null)).toBe("Working late.");
  });
  it("trims whitespace from name", () => {
    expect(welcomeGreeting(at(8), "  Sarah  ")).toBe("Good morning, Sarah.");
  });
});

describe("firstNameFromDisplayName", () => {
  it("extracts the first space-separated token", () => {
    expect(firstNameFromDisplayName("Sarah Chen")).toBe("Sarah");
  });
  it("handles single-token name", () => {
    expect(firstNameFromDisplayName("Sarah")).toBe("Sarah");
  });
  it("returns null on null/undefined/blank", () => {
    expect(firstNameFromDisplayName(null)).toBeNull();
    expect(firstNameFromDisplayName(undefined)).toBeNull();
    expect(firstNameFromDisplayName("   ")).toBeNull();
  });
  it("collapses internal whitespace runs", () => {
    expect(firstNameFromDisplayName("  Sarah   Chen  ")).toBe("Sarah");
  });

  // Wire-receiver assertion: ThreadBody pulls auth.identity?.display_name
  // and routes it through this helper. The optional SessionIdentity field
  // (api/authApi.ts) means today's auth bearer ships display_name=undefined;
  // a future auth-contract PR may populate it. Either input shape must
  // produce a final greeting that matches the spec's "appends when present"
  // contract.
  it.each([
    [{ display_name: "Sarah Chen" }, "Sarah"],
    [{ display_name: null }, null],
    [{ display_name: undefined }, null],
    [{}, null], // pre-contract-widening: identity has no field at all
  ])(
    "consumes a SessionIdentity-shaped payload (%j → %j)",
    (identity: { display_name?: string | null }, expected: string | null) => {
      expect(firstNameFromDisplayName(identity.display_name ?? null)).toBe(
        expected,
      );
    },
  );
});
