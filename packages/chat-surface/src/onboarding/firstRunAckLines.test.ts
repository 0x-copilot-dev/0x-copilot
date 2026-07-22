// firstRunAckLines — verbatim line derivation per engine/tools combo (PRD-P3
// §6.5) plus PRD-P8 §7's honesty axis: when the awaited local model
// demonstrably is NOT landing, neither the model line nor the ack title may
// keep claiming a download is in flight.

import { describe, expect, it } from "vitest";

import { FIRST_RUN_ACK_TITLES } from "./Acknowledgment";
import { FIRST_RUN_COPY } from "./firstRun";
import {
  FIRST_RUN_ACK_STALLED,
  firstRunAckAction,
  firstRunAckLines,
  firstRunAckNote,
  firstRunAckStateForPhase,
  firstRunAckTitle,
} from "./firstRunAckLines";
import type { FirstRunLaunchPhase } from "./useFirstRunLaunch";

describe("firstRunAckLines", () => {
  it("local · downloading N% + web on → nothing leaves this machine", () => {
    const lines = firstRunAckLines(
      { kind: "local", name: "Qwen 3 4B", pct: 41 },
      { webOn: true, connectors: [] },
    );
    expect(lines.modelLine).toBe("model — Qwen 3 4B · downloading 41%");
    expect(lines.toolsLine).toBe("tools — web search");
    expect(lines.privacyLine).toBe("nothing leaves this machine");
  });

  it("local ready (pct 100) → · on-device", () => {
    const lines = firstRunAckLines(
      { kind: "local", name: "Qwen 3 4B", pct: 100 },
      { webOn: true, connectors: [] },
    );
    expect(lines.modelLine).toBe("model — Qwen 3 4B · on-device");
    expect(lines.privacyLine).toBe("nothing leaves this machine");
  });

  it("local with no pct tolerates undefined → · on-device (never NaN%)", () => {
    const lines = firstRunAckLines(
      { kind: "local", name: "Qwen 3 4B" },
      { webOn: true, connectors: [] },
    );
    expect(lines.modelLine).toBe("model — Qwen 3 4B · on-device");
  });

  it("key engine → plain model name + key in your OS keychain", () => {
    const lines = firstRunAckLines(
      { kind: "key", name: "Claude Sonnet 4.5" },
      { webOn: true, connectors: [] },
    );
    expect(lines.modelLine).toBe("model — Claude Sonnet 4.5");
    expect(lines.privacyLine).toBe("key in your OS keychain");
  });

  it("web off, no connectors → tools — none", () => {
    const lines = firstRunAckLines(
      { kind: "key", name: "GPT-5.2" },
      { webOn: false, connectors: [] },
    );
    expect(lines.toolsLine).toBe("tools — none");
  });

  it("a connector appends the · {connector}… suffix", () => {
    const lines = firstRunAckLines(
      { kind: "key", name: "Claude Sonnet 4.5" },
      { webOn: true, connectors: ["Safe{Wallet}"] },
    );
    expect(lines.toolsLine).toBe("tools — web search · Safe{Wallet}…");
  });
});

// PRD-P8 §7 — the third ack state. The bug it kills: a send accepted while the
// model downloads used to read "Queued — starts when the model lands" forever,
// including after the download demonstrably stopped.
describe("firstRunAckLines — the stalled model line (PRD-P8 §7)", () => {
  it("swaps the downloading claim for an honest paused line, keeping the percent", () => {
    const lines = firstRunAckLines(
      { kind: "local", name: "Qwen 3 4B", pct: 41, blocked: true },
      { webOn: true, connectors: [] },
    );
    expect(lines.modelLine).toBe("model — Qwen 3 4B · download paused at 41%");
    expect(lines.modelLine).not.toContain("downloading");
  });

  it("says paused with no percent when the pull never reported one", () => {
    const lines = firstRunAckLines(
      { kind: "local", name: "Qwen 3 4B", blocked: true },
      { webOn: true, connectors: [] },
    );
    expect(lines.modelLine).toBe("model — Qwen 3 4B · download paused");
  });

  it("a landed model beats a stale block — it is on-device now", () => {
    const lines = firstRunAckLines(
      { kind: "local", name: "Qwen 3 4B", pct: 100, blocked: true },
      { webOn: true, connectors: [] },
    );
    expect(lines.modelLine).toBe("model — Qwen 3 4B · on-device");
  });

  it("a key engine ignores a local block entirely", () => {
    // The block describes the LOCAL download; a user on a key is ready.
    const lines = firstRunAckLines(
      { kind: "key", name: "Claude Sonnet 4.5", blocked: true },
      { webOn: true, connectors: [] },
    );
    expect(lines.modelLine).toBe("model — Claude Sonnet 4.5");
  });
});

describe("firstRunAckLines — the stalled ack state (PRD-P8 §7)", () => {
  it("maps every launch phase to an ack state, and only `blocked` to stalled", () => {
    const cases: readonly [FirstRunLaunchPhase, string][] = [
      ["composing", "starting"],
      ["starting", "starting"],
      ["queued", "queued"],
      ["blocked", "stalled"],
      ["handoff", "starting"],
      ["error", "starting"],
    ];
    for (const [phase, expected] of cases) {
      expect(firstRunAckStateForPhase(phase)).toBe(expected);
    }
  });

  it("titles the stalled state honestly — never the queued promise", () => {
    const title = firstRunAckTitle("stalled");
    expect(title).toBe(FIRST_RUN_ACK_STALLED.title);
    expect(title).not.toBe(FIRST_RUN_ACK_TITLES.queued);
    // The whole defect in one assertion: the user must not be told the model
    // is on its way when it demonstrably is not.
    expect(title).not.toContain("lands");
  });

  it("keeps the two shipped titles byte-identical", () => {
    expect(firstRunAckTitle("starting")).toBe(FIRST_RUN_ACK_TITLES.starting);
    expect(firstRunAckTitle("queued")).toBe(FIRST_RUN_ACK_TITLES.queued);
  });

  it("gives the stalled state a note AND an action — the two things that make it not a dead end", () => {
    expect(firstRunAckNote("stalled")).toBe(FIRST_RUN_ACK_STALLED.note);
    expect(firstRunAckAction("stalled")).toBe(FIRST_RUN_ACK_STALLED.action);
    // Both name a real way out.
    expect(firstRunAckNote("stalled")).toMatch(/Restart Ollama|add a key/);
    expect((firstRunAckAction("stalled") ?? "").trim().length).toBeGreaterThan(
      0,
    );
  });

  it("gives the other two states NO action, so no caller can render a dead button", () => {
    for (const state of ["starting", "queued"] as const) {
      expect(firstRunAckNote(state)).toBeNull();
      expect(firstRunAckAction(state)).toBeNull();
    }
  });

  it("reads its copy from FIRST_RUN_COPY — no second home for the strings", () => {
    expect(FIRST_RUN_ACK_STALLED).toBe(FIRST_RUN_COPY.ack.stalled);
  });
});
