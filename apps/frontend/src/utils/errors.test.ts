import { describe, expect, it } from "vitest";
import { errorMessage } from "./errors";

describe("errorMessage", () => {
  it("returns the Error message when non-empty", () => {
    expect(errorMessage(new Error("boom"), "fallback")).toBe("boom");
  });

  it("trims whitespace", () => {
    expect(errorMessage(new Error("  boom  "), "fallback")).toBe("boom");
  });

  it("falls back when the value is not an Error", () => {
    expect(errorMessage("string thrown", "fallback")).toBe("fallback");
    expect(errorMessage({ message: "ducktyped" }, "fallback")).toBe("fallback");
    expect(errorMessage(null, "fallback")).toBe("fallback");
    expect(errorMessage(undefined, "fallback")).toBe("fallback");
  });

  it("falls back when the Error message is empty or whitespace-only", () => {
    expect(errorMessage(new Error(""), "fallback")).toBe("fallback");
    expect(errorMessage(new Error("   "), "fallback")).toBe("fallback");
  });

  it("handles Error subclasses", () => {
    class CustomError extends Error {}
    expect(errorMessage(new CustomError("custom"), "fallback")).toBe("custom");
  });
});
