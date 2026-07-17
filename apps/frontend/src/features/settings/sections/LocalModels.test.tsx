// Local models settings section — Round 2.
//
// States under test: Ollama not running → setup steps; running → installed
// list with placement badge + delete; the download flow (size heads-up →
// pull stream → refresh on done).

import { describe, expect, it, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import type {
  LocalModelPullEvent,
  LocalModelSize,
  LocalModelsListResponse,
  LocalModelsStatus,
} from "@0x-copilot/api-types";

const mockStatus = vi.fn<() => Promise<LocalModelsStatus>>();
const mockList = vi.fn<() => Promise<LocalModelsListResponse>>();
const mockSize =
  vi.fn<(repo: string, quant: string) => Promise<LocalModelSize>>();
const mockDelete = vi.fn<(name: string) => Promise<void>>();
const mockStream =
  vi.fn<
    (opts: {
      repo: string;
      quant: string;
      onEvent: (e: LocalModelPullEvent) => void;
      onError?: (err: Error) => void;
      onOpen?: () => void;
    }) => { close: () => void }
  >();

vi.mock("../../../api/localModelsApi", () => ({
  getLocalModelsStatus: () => mockStatus(),
  listLocalModels: () => mockList(),
  getLocalModelSize: (repo: string, quant: string) => mockSize(repo, quant),
  deleteLocalModel: (name: string) => mockDelete(name),
  streamLocalModelPull: (opts: never) => mockStream(opts),
}));

import { LocalModels } from "./LocalModels";

const RUNNING: LocalModelsStatus = {
  enabled: true,
  ollama_running: true,
  ollama_version: "0.5.1",
};

beforeEach(() => {
  mockStatus.mockReset();
  mockList.mockReset();
  mockSize.mockReset();
  mockDelete.mockReset();
  mockStream.mockReset();
  mockList.mockResolvedValue({ models: [] });
});

describe("LocalModels", () => {
  it("shows Ollama setup steps when the runtime is not running", async () => {
    mockStatus.mockResolvedValue({
      enabled: true,
      ollama_running: false,
      ollama_version: null,
    });
    render(<LocalModels />);
    await waitFor(() =>
      expect(screen.getByText(/Install Ollama to get started/i)).toBeTruthy(),
    );
    expect(
      screen.getByRole("link", { name: /ollama\.com\/download/i }),
    ).toBeTruthy();
  });

  it("lists installed models with a placement badge and can remove one", async () => {
    mockStatus.mockResolvedValue(RUNNING);
    mockList.mockResolvedValue({
      models: [
        {
          name: "hf.co/acme/Tiny-GGUF:Q4_K_M",
          size_bytes: 808_000_000,
          quantization: "Q4_K_M",
          parameter_size: "1.2B",
          run_placement: "gpu",
        },
      ],
    });
    mockDelete.mockResolvedValue(undefined);
    render(<LocalModels />);

    await waitFor(() =>
      expect(screen.getByText("hf.co/acme/Tiny-GGUF:Q4_K_M")).toBeTruthy(),
    );
    expect(screen.getByText("GPU")).toBeTruthy();

    fireEvent.click(
      screen.getByRole("button", { name: /Remove hf\.co\/acme/i }),
    );
    await waitFor(() =>
      expect(mockDelete).toHaveBeenCalledWith("hf.co/acme/Tiny-GGUF:Q4_K_M"),
    );
  });

  it("downloads a model: size heads-up, pull stream, refresh on done", async () => {
    mockStatus.mockResolvedValue(RUNNING);
    mockSize.mockResolvedValue({
      repo: "acme/Tiny-GGUF",
      quant: "Q4_K_M",
      filename: "Tiny-Q4_K_M.gguf",
      size_bytes: 808_000_000,
    });
    let captured: {
      onEvent: (e: LocalModelPullEvent) => void;
    } | null = null;
    const close = vi.fn();
    mockStream.mockImplementation((opts) => {
      captured = opts;
      return { close };
    });

    render(<LocalModels />);
    const repoInput = await screen.findByPlaceholderText("vendor/repo-GGUF");
    fireEvent.change(repoInput, { target: { value: "acme/Tiny-GGUF" } });
    fireEvent.click(screen.getByRole("button", { name: /^Download$/ }));

    await waitFor(() =>
      expect(mockSize).toHaveBeenCalledWith("acme/Tiny-GGUF", "Q4_K_M"),
    );
    await waitFor(() => expect(mockStream).toHaveBeenCalledTimes(1));

    // Drive a progress frame then a terminal done frame.
    captured!.onEvent({
      sequence_no: 1,
      status: "downloading",
      bytes_total: 808_000_000,
      bytes_completed: 404_000_000,
      speed_bps: 10_000_000,
      eta_seconds: 40,
      done: false,
      error: null,
    });
    await waitFor(() => expect(screen.getByText(/Downloading/)).toBeTruthy());

    const listCallsBefore = mockList.mock.calls.length;
    captured!.onEvent({
      sequence_no: 2,
      status: "success",
      bytes_total: 808_000_000,
      bytes_completed: 808_000_000,
      speed_bps: null,
      eta_seconds: null,
      done: true,
      error: null,
    });
    await waitFor(() => expect(close).toHaveBeenCalled());
    // Completion refreshes the installed list.
    await waitFor(() =>
      expect(mockList.mock.calls.length).toBeGreaterThan(listCallsBefore),
    );
  });

  it("surfaces a size-lookup error without starting the stream", async () => {
    mockStatus.mockResolvedValue(RUNNING);
    mockSize.mockRejectedValue(new Error("No 'Q4_K_M' GGUF found in 'acme/x'"));
    render(<LocalModels />);
    const repoInput = await screen.findByPlaceholderText("vendor/repo-GGUF");
    fireEvent.change(repoInput, { target: { value: "acme/x" } });
    fireEvent.click(screen.getByRole("button", { name: /^Download$/ }));

    await waitFor(() =>
      expect(screen.getByText(/No 'Q4_K_M' GGUF found/)).toBeTruthy(),
    );
    expect(mockStream).not.toHaveBeenCalled();
  });
});
