import { describe, expect, it } from "vitest";

import { statusTone } from "./statusTone";

// Every status the SSOT knows, plus an unknown one — used by the
// lowercase-invariant sweep below.
const ALL_STATUSES: ReadonlyArray<string> = [
  "running",
  "queued",
  "streaming",
  "cancelling",
  "done",
  "completed",
  "paused",
  "waiting_for_approval",
  "needs_input",
  "stopped",
  "cancelled",
  "canceled",
  "archived",
  "failed",
  "error",
  "some_new_state",
];

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

  it("falls back to a muted, lowercased chip for unknown statuses", () => {
    expect(statusTone("some_new_state")).toEqual({
      tone: "muted",
      label: "some new state",
      showDot: false,
    });
  });

  it("provides a lowercase human label per known status (matches the design)", () => {
    expect(statusTone("running").label).toBe("running");
    expect(statusTone("done").label).toBe("done");
    expect(statusTone("stopped").label).toBe("stopped");
    expect(statusTone("needs_input").label).toBe("needs you");
  });

  it("every label is lowercase — no CSS text-transform is relied on", () => {
    for (const s of ALL_STATUSES) {
      const { label } = statusTone(s);
      expect(label).toBe(label.toLowerCase());
    }
  });
});
