// <DownloadLocalModelModal /> — the download flow (DESIGN-SPEC §5, FR-5.15):
// pick → streamed progress → ready + "use as default" → Finish; interrupt →
// ember error + retry. The runtime pull is the injected `startPull` seam; the
// test captures its handlers and drives `LocalModelPullEvent`s by hand.

import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { LocalModelPullEvent } from "@0x-copilot/api-types";

import {
  DownloadLocalModelModal,
  type AvailableLocalModel,
  type LocalModelPullHandlers,
} from "./DownloadLocalModelModal";

const AVAILABLE: readonly AvailableLocalModel[] = [
  {
    repo: "bartowski/Llama-3.2-1B-Instruct-GGUF",
    quant: "Q4_K_M",
    name: "Llama 3.2",
    parameterSize: "1.2B",
    sizeBytes: 838_860_800, // 800 MB
    note: "fast · good for chat",
  },
  {
    repo: "bartowski/Qwen2.5-3B-GGUF",
    quant: "Q4_K_M",
    name: "Qwen 2.5",
    parameterSize: "3B",
    sizeBytes: 2_000_000_000,
  },
];

let captured: LocalModelPullHandlers | null = null;
const close = vi.fn();
const startPull = vi.fn(
  (_req: { repo: string; quant: string }, handlers: LocalModelPullHandlers) => {
    captured = handlers;
    return { close };
  },
);

function renderModal(
  overrides: Partial<Parameters<typeof DownloadLocalModelModal>[0]> = {},
) {
  const onFinish = vi.fn();
  const onClose = vi.fn();
  render(
    <DownloadLocalModelModal
      open
      onClose={onClose}
      availableModels={AVAILABLE}
      startPull={startPull}
      onFinish={onFinish}
      {...overrides}
    />,
  );
  return { onFinish, onClose };
}

function pickFirst(): void {
  fireEvent.click(screen.getAllByTestId("download-pick-option")[0]);
}

beforeEach(() => {
  captured = null;
  close.mockReset();
  startPull.mockReset();
  startPull.mockImplementation((_req, handlers) => {
    captured = handlers;
    return { close };
  });
});

describe("<DownloadLocalModelModal>", () => {
  it("does not render when closed", () => {
    render(
      <DownloadLocalModelModal
        open={false}
        onClose={() => undefined}
        availableModels={AVAILABLE}
        startPull={startPull}
        onFinish={() => undefined}
      />,
    );
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("lists available models with name·param·size·note", () => {
    renderModal();
    const list = screen.getByTestId("download-pick-list");
    expect(within(list).getByText("Llama 3.2")).toBeInTheDocument();
    expect(within(list).getByText(/1\.2B · 800 MB · fast/)).toBeInTheDocument();
    expect(within(list).getByText("Qwen 2.5")).toBeInTheDocument();
  });

  it("picking a model starts the pull and shows the progress bar", () => {
    renderModal();
    pickFirst();
    expect(startPull).toHaveBeenCalledWith(
      { repo: AVAILABLE[0].repo, quant: AVAILABLE[0].quant },
      expect.anything(),
    );
    expect(screen.getByTestId("step-dots")).toHaveAttribute(
      "aria-label",
      "Step 2 of 3",
    );
    expect(screen.getByRole("progressbar")).toBeInTheDocument();
  });

  it("streamed byte frames drive the progress bar percentage", () => {
    renderModal();
    pickFirst();
    act(() => captured!.onEvent(progressFrame(404_000_000)));
    expect(screen.getByRole("progressbar")).toHaveAttribute(
      "aria-valuenow",
      "50",
    );
    expect(screen.getByText(/Downloading/)).toBeInTheDocument();
  });

  it("a done frame advances to the ready step with the default toggle on", () => {
    renderModal();
    pickFirst();
    act(() => captured!.onEvent(doneFrame()));
    expect(close).toHaveBeenCalled();
    expect(screen.getByTestId("download-ready")).toBeInTheDocument();
    expect(screen.getByText(/Ready to run locally/)).toBeInTheDocument();
    const toggle = screen.getByTestId(
      "download-default-toggle",
    ) as HTMLInputElement;
    expect(toggle.checked).toBe(true);
    expect(screen.getByTestId("step-dots")).toHaveAttribute(
      "aria-label",
      "Step 3 of 3",
    );
  });

  it("Finish reports the chosen model and default preference, then closes", () => {
    const { onFinish, onClose } = renderModal();
    pickFirst();
    act(() => captured!.onEvent(doneFrame()));
    // Turn the default toggle off before finishing.
    fireEvent.click(screen.getByTestId("download-default-toggle"));
    fireEvent.click(screen.getByTestId("download-finish"));
    expect(onFinish).toHaveBeenCalledWith({
      model: AVAILABLE[0],
      setAsDefault: false,
    });
    expect(onClose).toHaveBeenCalled();
  });

  it("an error frame surfaces an ember alert and a retry that re-pulls", () => {
    renderModal();
    pickFirst();
    act(() =>
      captured!.onEvent({
        ...doneFrame(),
        status: "error",
        done: false,
        error: "not found",
      }),
    );
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent(/not found/);
    const fill = screen.getByTestId("progress-bar-fill");
    expect(fill).toHaveAttribute("data-tone", "danger");

    // Retry lives in the footer action slot (DESIGN-SPEC §5) and re-pulls.
    fireEvent.click(screen.getByTestId("download-retry"));
    expect(startPull).toHaveBeenCalledTimes(2);
  });

  it("a transport onError marks the download interrupted", () => {
    renderModal();
    pickFirst();
    act(() => captured!.onError(new Error("socket closed")));
    expect(screen.getByRole("alert")).toHaveTextContent(/interrupted/i);
  });
});

function progressFrame(bytesCompleted: number): LocalModelPullEvent {
  return {
    sequence_no: 1,
    status: "downloading",
    bytes_total: 808_000_000,
    bytes_completed: bytesCompleted,
    speed_bps: 10_000_000,
    eta_seconds: 40,
    done: false,
    error: null,
  };
}

function doneFrame(): LocalModelPullEvent {
  return {
    sequence_no: 2,
    status: "success",
    bytes_total: 808_000_000,
    bytes_completed: 808_000_000,
    speed_bps: null,
    eta_seconds: null,
    done: true,
    error: null,
  };
}
