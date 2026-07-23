import { describe, expect, it } from "vitest";

import type { LocalModelSummary } from "@0x-copilot/api-types";

import type { FirstRunEngine } from "./firstRun";
import {
  backoffDelayMs,
  classifyPullError,
  findInstalledTag,
  firstRunModelPillLabel,
  INITIAL_PULL_PROGRESS,
  pullPercent,
  reducePullProgress,
  resolveInstalledTag,
} from "./localModelEngine";

const NAME = "Qwen 3 4B";

function summary(name: string): LocalModelSummary {
  return {
    name,
    size_bytes: 1,
    quantization: null,
    parameter_size: null,
    run_placement: null,
  };
}

describe("firstRunModelPillLabel", () => {
  it("shows the rounded percent while a local model downloads", () => {
    const engine: FirstRunEngine = { kind: "local", modelId: null };
    expect(firstRunModelPillLabel(engine, NAME, 41)).toBe("Qwen 3 4B · 41%");
    expect(firstRunModelPillLabel(engine, NAME, 40.6)).toBe("Qwen 3 4B · 41%");
  });

  it("drops the percent once the local model is ready (100 or null)", () => {
    const engine: FirstRunEngine = { kind: "local", modelId: "hf.co/x:Q8_0" };
    expect(firstRunModelPillLabel(engine, NAME, 100)).toBe("Qwen 3 4B");
    expect(firstRunModelPillLabel(engine, NAME, null)).toBe("Qwen 3 4B");
  });

  it("returns the provider label for a key engine and '' for none", () => {
    const key: FirstRunEngine = {
      kind: "key",
      provider: "anthropic",
      label: "Anthropic",
      dotColor: "#d97757",
      modelId: null,
    };
    expect(firstRunModelPillLabel(key, NAME, null)).toBe("Anthropic");
    expect(firstRunModelPillLabel(null, NAME, null)).toBe("");
  });
});

describe("pullPercent", () => {
  it("uses live bytes_total as the denominator", () => {
    expect(pullPercent(25, 100, 999, false)).toBe(25);
  });

  it("falls back to the size hint until bytes_total arrives", () => {
    expect(pullPercent(50, null, 200, false)).toBe(25);
  });

  it("clamps at 100 and returns 100 on done with no totals", () => {
    expect(pullPercent(300, 100, null, false)).toBe(100);
    expect(pullPercent(null, null, null, true)).toBe(100);
    expect(pullPercent(null, null, null, false)).toBe(0);
  });
});

describe("resolveInstalledTag", () => {
  it("matches an installed tag case-insensitively by repo substring", () => {
    const models = [summary("HF.CO/Qwen/Qwen3-4B-GGUF:Q8_0")];
    expect(resolveInstalledTag(models, "Qwen/Qwen3-4B-GGUF", "Q8_0")).toBe(
      "HF.CO/Qwen/Qwen3-4B-GGUF:Q8_0",
    );
  });

  it("falls back to the literal hf.co tag when nothing matches", () => {
    expect(
      resolveInstalledTag([summary("llama3")], "Qwen/Qwen3-4B-GGUF", "Q8_0"),
    ).toBe("hf.co/Qwen/Qwen3-4B-GGUF:Q8_0");
  });
});

describe("findInstalledTag", () => {
  it("returns the matched tag when the preset is installed", () => {
    expect(
      findInstalledTag(
        [summary("llama3"), summary("HF.CO/Qwen/Qwen3-4B-GGUF:Q8_0")],
        "Qwen/Qwen3-4B-GGUF",
      ),
    ).toBe("HF.CO/Qwen/Qwen3-4B-GGUF:Q8_0");
  });

  it("returns null on a miss — unlike resolveInstalledTag's fallback", () => {
    expect(
      findInstalledTag([summary("llama3")], "Qwen/Qwen3-4B-GGUF"),
    ).toBeNull();
    expect(findInstalledTag([], "Qwen/Qwen3-4B-GGUF")).toBeNull();
  });
});

describe("reducePullProgress", () => {
  const f = (over: {
    bytes_completed?: number | null;
    bytes_total?: number | null;
    done?: boolean;
  }) => ({
    bytes_completed: over.bytes_completed ?? null,
    bytes_total: over.bytes_total ?? null,
    done: over.done ?? false,
  });

  it("exposes the live byte counts alongside the percent", () => {
    expect(
      reducePullProgress(
        f({ bytes_completed: 50, bytes_total: 200 }),
        999,
        INITIAL_PULL_PROGRESS,
      ),
    ).toEqual({ pct: 25, bytesCompleted: 50, bytesTotal: 200 });
  });

  it("carries the last known bytes through a status-only frame", () => {
    const at25 = reducePullProgress(
      f({ bytes_completed: 50, bytes_total: 200 }),
      null,
      null,
    );
    // "verifying sha256" — no byte counts. The bar must not snap back to 0.
    const verifying = reducePullProgress(f({}), null, at25);
    expect(verifying.pct).toBe(25);
    expect(verifying.bytesTotal).toBe(200);
  });

  it("keeps the seeded percent when the first frame carries no bytes", () => {
    expect(reducePullProgress(f({}), 200, INITIAL_PULL_PROGRESS).pct).toBe(2);
    expect(reducePullProgress(f({}), null, null).pct).toBe(0);
  });

  it("lands at 100 on done and reports completed = total", () => {
    const at25 = reducePullProgress(
      f({ bytes_completed: 50, bytes_total: 200 }),
      null,
      null,
    );
    expect(reducePullProgress(f({ done: true }), null, at25)).toEqual({
      pct: 100,
      bytesCompleted: 200,
      bytesTotal: 200,
    });
    expect(reducePullProgress(f({ done: true }), null, null)).toEqual({
      pct: 100,
      bytesCompleted: null,
      bytesTotal: null,
    });
  });

  it("uses the size hint as the denominator until bytes_total arrives", () => {
    expect(reducePullProgress(f({ bytes_completed: 50 }), 200, null)).toEqual({
      pct: 25,
      bytesCompleted: 50,
      bytesTotal: null,
    });
  });
});

describe("classifyPullError", () => {
  it("passes each known kind through", () => {
    expect(classifyPullError("runtime_unreachable")).toBe(
      "runtime_unreachable",
    );
    expect(classifyPullError("transient")).toBe("transient");
    expect(classifyPullError("terminal")).toBe("terminal");
  });

  it("degrades an absent or unrecognised kind to terminal, never to a retry", () => {
    expect(classifyPullError(undefined)).toBe("terminal");
    expect(classifyPullError(null)).toBe("terminal");
    expect(classifyPullError("retryable" as never)).toBe("terminal");
  });
});

describe("backoffDelayMs", () => {
  it("doubles from 1s and saturates at 30s", () => {
    expect(backoffDelayMs(0)).toBe(1_000);
    expect(backoffDelayMs(1)).toBe(2_000);
    expect(backoffDelayMs(2)).toBe(4_000);
    expect(backoffDelayMs(3)).toBe(8_000);
    expect(backoffDelayMs(4)).toBe(16_000);
    expect(backoffDelayMs(5)).toBe(30_000);
    expect(backoffDelayMs(50)).toBe(30_000);
  });

  it("clamps nonsense attempts to the first delay", () => {
    expect(backoffDelayMs(-3)).toBe(1_000);
    expect(backoffDelayMs(Number.NaN)).toBe(1_000);
  });
});
