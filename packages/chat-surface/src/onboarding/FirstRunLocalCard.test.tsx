// @vitest-environment jsdom
//
// One test per row of the PRD-P8 §5 UI contract, plus the two rules that are
// easier to break than to notice: `Restart Ollama` never renders when the
// server cannot manage the runtime, and D1's red terminal branch is GONE.

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { AvailableLocalModel } from "../settings/DownloadLocalModelModal";
import { FirstRunLocalCard } from "./FirstRunLocalCard";
import { FIRST_RUN_COPY } from "./firstRun";
import type { UseFirstRunLocalModelResult } from "./useFirstRunLocalModel";

const COPY = FIRST_RUN_COPY.local;

const PRESET: AvailableLocalModel = {
  repo: "Qwen/Qwen3-4B-GGUF",
  quant: "Q8_0",
  name: "Qwen 3 4B",
  sizeBytes: 4_280_404_704,
};

/** Default = design state ②: enabled, runtime up, nothing downloaded yet. */
function state(
  over: Partial<UseFirstRunLocalModelResult> = {},
): UseFirstRunLocalModelResult {
  return {
    enabled: true,
    runtime: "running",
    runtimeManaged: true,
    phase: "idle",
    modelInstalled: false,
    localModelPct: null,
    bytesCompleted: null,
    bytesTotal: null,
    blocked: null,
    modelName: null,
    disabled: false,
    start: vi.fn(),
    resume: vi.fn(),
    restartRuntime: vi.fn(),
    recheck: vi.fn(),
    ...over,
  };
}

function renderCard(
  over: Partial<UseFirstRunLocalModelResult> = {},
  props: {
    onStartDownload?: () => void;
    onContinue?: () => void;
    onGetOllama?: () => void;
  } = {},
) {
  const onStartDownload = props.onStartDownload ?? vi.fn();
  const view = render(
    <FirstRunLocalCard
      state={state(over)}
      preset={PRESET}
      onStartDownload={onStartDownload}
      onContinue={props.onContinue}
      onGetOllama={props.onGetOllama}
    />,
  );
  return { ...view, onStartDownload };
}

/** Every testid the pre-P8 red branch owned — none may come back (D1). */
const DELETED_TESTIDS = [
  "first-run-local-error",
  "first-run-local-retry",
  "first-run-local-setup",
  "first-run-local-recheck",
];

describe("<FirstRunLocalCard> header + a11y", () => {
  it("renders byte-verbatim SPEC copy, unchanged in every state", () => {
    renderCard();
    const card = screen.getByTestId("first-run-local-card");
    expect(card.textContent).toContain(COPY.title);
    expect(card.textContent).toContain(COPY.meta);
    expect(card.textContent).toContain(COPY.body);
    // D5 — the frozen header meta. The mock says 5.6 GB; do not "fix" it.
    expect(COPY.meta).toBe("Qwen 3 4B · 4.3 GB · free forever");
    expect(COPY.body).toBe(
      "Runs on this machine. Nothing you send ever leaves it.",
    );
  });

  it("announces the foot politely (①→detected and ③→④ change with no click)", () => {
    renderCard();
    const foot = screen.getByTestId("first-run-local-foot");
    expect(foot.getAttribute("role")).toBe("status");
    expect(foot.getAttribute("aria-live")).toBe("polite");
  });

  // Every other test reads `COPY.*`, so a paraphrase in `firstRun.ts` would
  // slip through all of them together. PRD-P8 §5 fixes these strings — glyphs
  // included (— is U+2014, → is U+2192, ↗ is U+2197, · is U+00B7) — so they are
  // pinned literally here, exactly as D5's header meta already is above.
  it("pins the §5 foot copy byte-for-byte, glyphs included", () => {
    expect(COPY.getOllama).toBe("Get Ollama ↗");
    expect(COPY.watchDetect).toBe("download starts once it's detected");
    expect(COPY.detected).toBe("Ollama detected — starting your download");
    expect(COPY.ready).toBe("on-device · ready");
    expect(COPY.downloading).toBe("Ollama detected — downloading now");
    expect(COPY.stopped).toBe("Ollama stopped responding");
    expect(COPY.restart).toBe("Restart Ollama");
    expect(COPY.stoppedWatch).toBe("download resumes on its own");
    expect(COPY.resume).toBe("Resume download");
    expect(COPY.btn).toBe("Start download");
    expect(COPY.note).toBe("type your first prompt while it downloads");
    // The two D4a deviations — deliberate, and just as much a contract.
    expect(COPY.continueBtn).toBe("Continue →");
    expect(COPY.downloadingNote).toBe("downloading in the background");
  });
});

describe("<FirstRunLocalCard> §5 — feature off / probing", () => {
  it("feature off (web/cloud): the note only, no Start", () => {
    const { onStartDownload } = renderCard({ enabled: false, disabled: true });
    expect(screen.getByTestId("first-run-local-unavailable").textContent).toBe(
      COPY.unavailable,
    );
    expect(screen.queryByTestId("first-run-start-download")).toBeNull();
    expect(onStartDownload).not.toHaveBeenCalled();
  });

  it("probing: the CTA stays visible but inert", () => {
    renderCard({ phase: "probing", runtime: "unknown", disabled: true });
    const btn = screen.getByRole("button", { name: COPY.btn });
    expect(btn).toBeDisabled();
    expect(screen.queryByTestId("first-run-start-download")).toBeNull();
    expect(screen.getByTestId("first-run-local-foot").textContent).toContain(
      COPY.note,
    );
  });
});

describe("<FirstRunLocalCard> §5 ① — Ollama not installed", () => {
  it.each(["not_installed", "unknown"] as const)(
    "runtime=%s: Get Ollama ↗ + the watch line",
    (runtime) => {
      const onGetOllama = vi.fn();
      renderCard({ runtime, disabled: true }, { onGetOllama });
      const watch = screen.getByTestId("first-run-local-watch");
      expect(watch.textContent).toContain(COPY.watchDetect);
      fireEvent.click(screen.getByTestId("first-run-local-get-ollama"));
      expect(onGetOllama).toHaveBeenCalledTimes(1);
      expect(screen.queryByTestId("first-run-start-download")).toBeNull();
    },
  );

  it("omits Get Ollama when the host wired no external-open seam", () => {
    renderCard({ runtime: "not_installed", disabled: true });
    expect(screen.queryByTestId("first-run-local-get-ollama")).toBeNull();
    expect(screen.getByTestId("first-run-local-watch").textContent).toContain(
      COPY.watchDetect,
    );
  });

  it("① → detected: the watched runtime coming up reads as auto-starting", () => {
    const { rerender } = renderCard({ runtime: "not_installed" });
    expect(screen.getByTestId("first-run-local-watch")).not.toBeNull();

    rerender(
      <FirstRunLocalCard
        state={state({ runtime: "running" })}
        preset={PRESET}
        onStartDownload={vi.fn()}
      />,
    );

    expect(
      screen.getByTestId("first-run-local-detected").textContent,
    ).toContain(COPY.detected);
    // The pull begins with no click — offering Start here would race it.
    expect(screen.queryByTestId("first-run-start-download")).toBeNull();
  });
});

describe("<FirstRunLocalCard> §5 ② — Ollama running", () => {
  it("model absent: the explicit Start button + note", () => {
    const onStartDownload = vi.fn();
    renderCard({}, { onStartDownload });
    expect(screen.getByTestId("first-run-local-foot").textContent).toContain(
      COPY.note,
    );
    fireEvent.click(screen.getByTestId("first-run-start-download"));
    expect(onStartDownload).toHaveBeenCalledTimes(1);
  });

  it("model already installed: 'on-device · ready', no redundant pull", () => {
    renderCard({ modelInstalled: true, modelName: "hf.co/x:Q8_0" });
    expect(screen.getByTestId("first-run-local-ready").textContent).toContain(
      COPY.ready,
    );
    expect(screen.queryByTestId("first-run-start-download")).toBeNull();
    expect(screen.queryByTestId("first-run-local-progress")).toBeNull();
    // §5's ready row is the `.ok` line ALONE. Carrying the ② note here would
    // promise a download ("…while it downloads") that is already finished.
    expect(
      screen.getByTestId("first-run-local-foot").textContent,
    ).not.toContain(COPY.note);
  });

  it("a finished pull lands on the same ready line", () => {
    renderCard({
      phase: "ready",
      localModelPct: 100,
      modelName: "hf.co/x:Q8_0",
    });
    expect(screen.getByTestId("first-run-local-ready").textContent).toContain(
      COPY.ready,
    );
  });
});

describe("<FirstRunLocalCard> §5 ③ — downloading", () => {
  it("spinner + bar + byte line + D4a's Continue →", () => {
    const onContinue = vi.fn();
    renderCard(
      {
        phase: "downloading",
        localModelPct: 56,
        bytesCompleted: 2_400_000_000,
        bytesTotal: 4_300_000_000,
      },
      { onContinue },
    );

    const block = screen.getByTestId("first-run-local-progress");
    expect(block.textContent).toContain(COPY.downloading);
    expect(block.querySelector(".spin")).not.toBeNull();

    const bar = screen.getByTestId("first-run-local-bar");
    expect(bar.getAttribute("role")).toBe("progressbar");
    expect(bar.getAttribute("aria-valuenow")).toBe("56");
    expect(bar.getAttribute("aria-label")).toBe(
      `${COPY.progressLabel} ${PRESET.name}`,
    );

    // The design's byte line: ONE unit, taken from the total.
    expect(screen.getByTestId("first-run-local-note").textContent).toBe(
      `${PRESET.name} · 2.4 / 4.3 GB · ${COPY.downloadingNote}`,
    );

    fireEvent.click(screen.getByTestId("first-run-local-continue"));
    expect(onContinue).toHaveBeenCalledTimes(1);
    expect(screen.queryByTestId("first-run-start-download")).toBeNull();
  });

  it("drops the byte segment while the totals are unknown", () => {
    renderCard({ phase: "downloading", localModelPct: 2 });
    expect(screen.getByTestId("first-run-local-note").textContent).toBe(
      `${PRESET.name} · ${COPY.downloadingNote}`,
    );
  });

  it("omits Continue when the host wired no advance seam", () => {
    renderCard({ phase: "downloading", localModelPct: 12 });
    expect(screen.queryByTestId("first-run-local-continue")).toBeNull();
  });

  it("reconnecting: same shell, swapped note, the bar keeps its value", () => {
    renderCard({
      phase: "reconnecting",
      localModelPct: 41,
      bytesCompleted: 2_400_000_000,
      bytesTotal: 4_300_000_000,
    });
    expect(screen.getByTestId("first-run-local-note").textContent).toBe(
      `${PRESET.name} · 2.4 / 4.3 GB · ${COPY.reconnecting}`,
    );
    expect(
      screen.getByTestId("first-run-local-bar").getAttribute("aria-valuenow"),
    ).toBe("41");
  });
});

describe("<FirstRunLocalCard> §5 ④ — runtime stopped / terminal", () => {
  it("stopped + managed: the amber line, Restart Ollama, and the resume note", () => {
    const restartRuntime = vi.fn();
    renderCard({
      runtime: "stopped",
      runtimeManaged: true,
      phase: "idle",
      localModelPct: 41,
      restartRuntime,
    });

    const block = screen.getByTestId("first-run-local-stopped");
    expect(block.textContent).toContain(COPY.stopped);
    expect(block.querySelector(".dling.warn")).not.toBeNull();
    expect(
      screen.getByTestId("first-run-local-stopped-watch").textContent,
    ).toContain(COPY.stoppedWatch);

    fireEvent.click(screen.getByTestId("first-run-local-restart"));
    expect(restartRuntime).toHaveBeenCalledTimes(1);
  });

  it("stopped + UNMANAGED: no Restart button that could not work", () => {
    renderCard({ runtime: "stopped", runtimeManaged: false, phase: "idle" });
    expect(screen.queryByTestId("first-run-local-restart")).toBeNull();
    expect(
      screen.getByTestId("first-run-local-stopped-watch").textContent,
    ).toContain(COPY.stoppedWatchUnmanaged);
  });

  it("terminal error: the server's safe message + Resume download", () => {
    const resume = vi.fn();
    renderCard({
      blocked: { kind: "terminal", message: "No space left on device." },
      localModelPct: 41,
      resume,
    });

    expect(
      screen.getByTestId("first-run-local-stopped-msg").textContent,
    ).toContain("No space left on device.");
    // A terminal failure does NOT auto-resume — never promise that it does.
    expect(screen.queryByTestId("first-run-local-stopped-watch")).toBeNull();

    fireEvent.click(screen.getByTestId("first-run-local-resume"));
    expect(resume).toHaveBeenCalledTimes(1);
  });

  it("blocked AND stopped: restart first, resume second", () => {
    renderCard({
      runtime: "stopped",
      runtimeManaged: true,
      blocked: { kind: "terminal", message: "Repository not found." },
    });
    expect(
      screen.getByTestId("first-run-local-stopped-msg").textContent,
    ).toContain("Repository not found.");
    expect(screen.getByTestId("first-run-local-restart")).not.toBeNull();
    expect(screen.getByTestId("first-run-local-resume")).not.toBeNull();
  });
});

describe("<FirstRunLocalCard> precedence + D1", () => {
  it("a runtime that died mid-pull renders ④, not a download that can never end", () => {
    // The pre-P8 foot asked `status === "downloading"` FIRST, so the runtime
    // flags were never consulted once a pull started. This is that regression.
    renderCard({
      phase: "reconnecting",
      runtime: "stopped",
      runtimeManaged: true,
      localModelPct: 41,
    });
    expect(screen.getByTestId("first-run-local-stopped")).not.toBeNull();
    expect(screen.queryByTestId("first-run-local-progress")).toBeNull();
  });

  it("an installed model outranks a stopped runtime (no download left to resume)", () => {
    renderCard({ modelInstalled: true, runtime: "stopped" });
    expect(screen.getByTestId("first-run-local-ready")).not.toBeNull();
    expect(screen.queryByTestId("first-run-local-stopped")).toBeNull();
  });

  it("the feature gate outranks every runtime fact", () => {
    renderCard({ enabled: false, runtime: "stopped", phase: "downloading" });
    expect(screen.getByTestId("first-run-local-unavailable")).not.toBeNull();
    expect(screen.queryByTestId("first-run-local-stopped")).toBeNull();
    expect(screen.queryByTestId("first-run-local-progress")).toBeNull();
  });

  it.each([
    [
      "downloading" as const,
      { phase: "downloading" as const, localModelPct: 41 },
    ],
    ["stopped" as const, { runtime: "stopped" as const, localModelPct: 41 }],
    [
      "terminal" as const,
      {
        blocked: { kind: "terminal" as const, message: "disk full" },
        localModelPct: 41,
      },
    ],
  ])("D1: no red error branch survives in the %s state", (_label, over) => {
    renderCard(over);
    for (const testId of DELETED_TESTIDS) {
      expect(screen.queryByTestId(testId)).toBeNull();
    }
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
    expect(
      screen.getByTestId("first-run-local-card").textContent,
    ).not.toContain("Couldn");
  });
});
