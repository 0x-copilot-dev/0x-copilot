// <LocalModelsPage /> — the four states (FR-5.13/5.14): loading / load-error /
// Ollama-not-running setup / running installed list, plus the jade default-local
// chip, placement label, Run / Delete / Set-default host callbacks, and opening
// the download flow. Runtime is the injected callback seam (no fetch here).

import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type {
  LocalModelSummary,
  LocalModelsStatus,
} from "@0x-copilot/api-types";

import { LocalModelsPage, type LocalModelsPageProps } from "./LocalModelsPage";

const RUNNING: LocalModelsStatus = {
  enabled: true,
  ollama_running: true,
  ollama_version: "0.5.1",
};

const MODELS: readonly LocalModelSummary[] = [
  {
    name: "hf.co/acme/Tiny-GGUF:Q4_K_M",
    size_bytes: 838_860_800, // 800 MB
    quantization: "Q4_K_M",
    parameter_size: "1.2B",
    run_placement: "gpu",
  },
  {
    name: "hf.co/acme/Small-GGUF:Q4_K_M",
    size_bytes: 2_000_000_000,
    quantization: "Q4_K_M",
    parameter_size: "3B",
    run_placement: "cpu",
  },
];

function renderPage(overrides: Partial<LocalModelsPageProps> = {}) {
  const props: LocalModelsPageProps = {
    status: RUNNING,
    models: MODELS,
    availableModels: [],
    defaultLocalModelName: MODELS[0].name,
    loadError: null,
    onRecheck: vi.fn(),
    onDownloaded: vi.fn(),
    startPull: vi.fn(() => ({ close: vi.fn() })),
    onDelete: vi.fn(),
    ...overrides,
  };
  render(<LocalModelsPage {...props} />);
  return props;
}

describe("<LocalModelsPage>", () => {
  it("uses the design IA: a 17px section heading over an Installed card", () => {
    renderPage();
    // The section title is the top-of-hierarchy <h1> (SecTitle); the installed
    // group is its own card with an <h3> title + "{n} models" meta.
    expect(
      screen.getByRole("heading", { level: 1, name: "Local models" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { level: 3, name: "Installed" }),
    ).toBeInTheDocument();
    expect(screen.getByText("2 models")).toBeInTheDocument();
  });

  it("shows a probing state while status is null", () => {
    renderPage({ status: null });
    expect(screen.getByTestId("local-models-loading")).toBeInTheDocument();
  });

  it("shows a role=alert + Retry on load error, and Retry re-probes", () => {
    const onRecheck = vi.fn();
    renderPage({ loadError: "Could not reach the local runtime.", onRecheck });
    expect(screen.getByRole("alert")).toHaveTextContent(/local runtime/i);
    fireEvent.click(screen.getByTestId("local-models-retry"));
    expect(onRecheck).toHaveBeenCalled();
  });

  it("shows Ollama setup steps when the runtime is not running", () => {
    const onRecheck = vi.fn();
    renderPage({
      status: { enabled: true, ollama_running: false, ollama_version: null },
      onRecheck,
    });
    expect(screen.getByTestId("local-models-setup")).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: /ollama\.com\/download/i }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("local-models-recheck"));
    expect(onRecheck).toHaveBeenCalled();
  });

  it("shows the empty state when running with no installed models", () => {
    renderPage({ models: [], defaultLocalModelName: null });
    expect(screen.getByTestId("local-models-empty")).toHaveTextContent(
      /No local models yet/i,
    );
  });

  it("lists installed models with name·param·size·placement and the default chip", () => {
    renderPage();
    const rows = screen.getAllByTestId("local-models-row");
    expect(rows).toHaveLength(2);

    const first = rows[0];
    expect(
      within(first).getByText("hf.co/acme/Tiny-GGUF:Q4_K_M"),
    ).toBeInTheDocument();
    expect(
      within(first).getByText(/1\.2B · 800 MB · Q4_K_M · GPU/),
    ).toBeInTheDocument();
    // Only the default model carries the jade "default local" chip.
    expect(
      within(first).getByTestId("local-models-default-chip"),
    ).toBeInTheDocument();
    expect(
      within(rows[1]).queryByTestId("local-models-default-chip"),
    ).toBeNull();
    // Non-GPU placement warns "slower".
    expect(within(rows[1]).getByText(/CPU — slower/)).toBeInTheDocument();
  });

  it("Delete and Run invoke the host callbacks with the model name", () => {
    const onDelete = vi.fn();
    const onRun = vi.fn();
    renderPage({ onDelete, onRun });
    fireEvent.click(
      screen.getByRole("button", {
        name: "Delete hf.co/acme/Tiny-GGUF:Q4_K_M",
      }),
    );
    expect(onDelete).toHaveBeenCalledWith("hf.co/acme/Tiny-GGUF:Q4_K_M");
    fireEvent.click(
      screen.getByRole("button", { name: "Run hf.co/acme/Tiny-GGUF:Q4_K_M" }),
    );
    expect(onRun).toHaveBeenCalledWith("hf.co/acme/Tiny-GGUF:Q4_K_M");
  });

  it("offers Set default only on non-default rows when onSetDefault is given", () => {
    const onSetDefault = vi.fn();
    renderPage({ onSetDefault });
    // Default row has no "Set default"; the other one does.
    expect(
      screen.queryByRole("button", {
        name: /Set hf\.co\/acme\/Tiny-GGUF.* as default/,
      }),
    ).toBeNull();
    fireEvent.click(
      screen.getByRole("button", {
        name: /Set hf\.co\/acme\/Small-GGUF.* as default/,
      }),
    );
    expect(onSetDefault).toHaveBeenCalledWith("hf.co/acme/Small-GGUF:Q4_K_M");
  });

  it("renders the Ollama privacy note", () => {
    renderPage();
    expect(screen.getByTestId("local-models-privacy-note")).toHaveTextContent(
      /private and offline/i,
    );
  });

  it("opens the download flow from Get another model", () => {
    renderPage();
    expect(screen.queryByRole("dialog")).toBeNull();
    fireEvent.click(screen.getByTestId("local-models-get-another"));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getByText("Download a local model")).toBeInTheDocument();
  });
});
