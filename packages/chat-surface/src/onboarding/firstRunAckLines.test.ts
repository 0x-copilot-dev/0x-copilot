// firstRunAckLines — verbatim line derivation per engine/tools combo (PRD-P3 §6.5).

import { describe, expect, it } from "vitest";

import { firstRunAckLines } from "./firstRunAckLines";

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
