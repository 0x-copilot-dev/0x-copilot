// @vitest-environment jsdom
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { AvailableLocalModel } from "../settings/DownloadLocalModelModal";
import { FirstRunLocalCard } from "./FirstRunLocalCard";
import { FIRST_RUN_COPY } from "./firstRun";
import type { UseFirstRunLocalModelResult } from "./useFirstRunLocalModel";

const PRESET: AvailableLocalModel = {
  repo: "Qwen/Qwen3-4B-GGUF",
  quant: "Q8_0",
  name: "Qwen 3 4B",
  sizeBytes: 4_280_404_704,
};

function state(
  over: Partial<UseFirstRunLocalModelResult> = {},
): UseFirstRunLocalModelResult {
  return {
    localModelPct: null,
    status: "idle",
    enabled: true,
    ollamaRunning: true,
    disabled: false,
    modelName: null,
    error: null,
    start: vi.fn(),
    retry: vi.fn(),
    recheck: vi.fn(),
    ...over,
  };
}

function renderCard(
  over: Partial<UseFirstRunLocalModelResult> = {},
  onStartDownload = vi.fn(),
) {
  render(
    <FirstRunLocalCard
      state={state(over)}
      preset={PRESET}
      onStartDownload={onStartDownload}
    />,
  );
  return { onStartDownload };
}

describe("<FirstRunLocalCard>", () => {
  it("renders byte-verbatim SPEC copy in every state", () => {
    renderCard();
    const card = screen.getByTestId("first-run-local-card");
    expect(card.textContent).toContain(FIRST_RUN_COPY.local.title);
    expect(card.textContent).toContain(FIRST_RUN_COPY.local.meta);
    expect(card.textContent).toContain(FIRST_RUN_COPY.local.body);
    expect(card.textContent).toContain(FIRST_RUN_COPY.local.btn);
    expect(card.textContent).toContain(FIRST_RUN_COPY.local.note);
    // exact mock copy strings
    expect(FIRST_RUN_COPY.local.meta).toBe("Qwen 3 4B · 4.3 GB · free forever");
    expect(FIRST_RUN_COPY.local.body).toBe(
      "Runs on this machine. Nothing you send ever leaves it.",
    );
    expect(FIRST_RUN_COPY.local.note).toBe(
      "type your first prompt while it downloads",
    );
  });

  it("fires onStartDownload from the enabled idle Start button", () => {
    const { onStartDownload } = renderCard();
    fireEvent.click(screen.getByTestId("first-run-start-download"));
    expect(onStartDownload).toHaveBeenCalledTimes(1);
  });

  it("shows a disabled inert CTA while probing", () => {
    renderCard({ status: "probing", disabled: true });
    const btn = screen.getByRole("button", { name: FIRST_RUN_COPY.local.btn });
    expect(btn).toBeDisabled();
    expect(screen.queryByTestId("first-run-start-download")).toBeNull();
  });

  it("degrades to a no-Start note when the feature is disabled (web/cloud)", () => {
    const { onStartDownload } = renderCard({ enabled: false, disabled: true });
    expect(screen.getByTestId("first-run-local-unavailable")).not.toBeNull();
    expect(screen.queryByTestId("first-run-start-download")).toBeNull();
    expect(onStartDownload).not.toHaveBeenCalled();
  });

  it("shows honest Ollama setup steps + Re-check when Ollama is not running", () => {
    const recheck = vi.fn();
    renderCard({ ollamaRunning: false, disabled: true, recheck });
    const setup = screen.getByTestId("first-run-local-setup");
    expect(setup.textContent).toContain("Ollama");
    const link = setup.querySelector("a");
    expect(link?.getAttribute("href")).toBe("https://ollama.com/download");
    expect(screen.queryByTestId("first-run-start-download")).toBeNull();
    fireEvent.click(screen.getByTestId("first-run-local-recheck"));
    expect(recheck).toHaveBeenCalledTimes(1);
  });

  it("renders a progress bar + 'Qwen 3 4B · N%' while downloading", () => {
    renderCard({ status: "downloading", localModelPct: 41 });
    const progress = screen.getByTestId("first-run-local-progress");
    expect(progress).not.toBeNull();
    expect(screen.getByTestId("progress-bar")).not.toBeNull();
    expect(progress.textContent).toContain("Qwen 3 4B · 41%");
    expect(progress.textContent).toContain(FIRST_RUN_COPY.local.note);
    expect(screen.queryByTestId("first-run-start-download")).toBeNull();
  });

  it("shows 'on-device · ready' when ready", () => {
    renderCard({
      status: "ready",
      localModelPct: 100,
      modelName: "hf.co/x:Q8_0",
    });
    expect(screen.getByTestId("first-run-local-ready").textContent).toContain(
      "on-device · ready",
    );
  });

  it("shows an alert + Retry on error", () => {
    const retry = vi.fn();
    renderCard({ status: "error", error: "disk full", retry });
    const alert = screen.getByTestId("first-run-local-error");
    expect(alert.getAttribute("role")).toBe("alert");
    expect(alert.textContent).toContain("disk full");
    fireEvent.click(screen.getByTestId("first-run-local-retry"));
    expect(retry).toHaveBeenCalledTimes(1);
  });
});
