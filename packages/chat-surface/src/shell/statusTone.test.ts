import { describe, expect, it } from "vitest";

import { statusTone } from "./statusTone";

describe("statusTone (run-status SSOT)", () => {
  it("maps live states to success + a dot", () => {
    for (const s of ["running", "queued", "streaming"]) {
      expect(statusTone(s)).toMatchObject({ tone: "ok", showDot: true });
    }
  });

  it("maps a finished run to success (jade), NOT grey", () => {
    expect(statusTone("done")).toMatchObject({ tone: "ok", showDot: false });
    expect(statusTone("completed").tone).toBe("ok");
  });

  it("maps a user-stopped run to muted (off), NOT danger-red", () => {
    expect(statusTone("stopped").tone).toBe("muted");
    expect(statusTone("cancelled").tone).toBe("muted");
    expect(statusTone("archived").tone).toBe("muted");
  });

  it("maps paused/approval to warning and the folded-inbox state to accent", () => {
    expect(statusTone("paused").tone).toBe("warning");
    expect(statusTone("waiting_for_approval").tone).toBe("warning");
    expect(statusTone("needs_input").tone).toBe("info");
  });

  it("keeps genuine failures as error (distinct from a user stop)", () => {
    expect(statusTone("failed").tone).toBe("error");
    expect(statusTone("error").tone).toBe("error");
  });

  it("no live/dot on non-live states", () => {
    expect(statusTone("done").showDot).toBe(false);
    expect(statusTone("paused").showDot).toBe(false);
    expect(statusTone("stopped").showDot).toBe(false);
  });

  it("falls back to a muted, title-cased chip for unknown statuses", () => {
    expect(statusTone("some_new_state")).toEqual({
      tone: "muted",
      label: "Some new state",
      showDot: false,
    });
  });

  it("provides a human label per known status", () => {
    expect(statusTone("running").label).toBe("Running");
    expect(statusTone("done").label).toBe("Done");
    expect(statusTone("stopped").label).toBe("Stopped");
  });
});
