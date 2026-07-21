// PR-3D — the Settings → Models page: grouped catalog, toggles, custom-add,
// reset-to-default, and load/error states.

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ModelCatalogModel } from "@0x-copilot/api-types";

import { ModelsPage, MODELS_PAGE_NOTE } from "./ModelsPage";
import type { CatalogModel, ModelsPort } from "./data/models";

function model(
  id: string,
  provider: string,
  extra: Partial<ModelCatalogModel> = {},
): CatalogModel {
  return {
    id,
    provider,
    model_name: id,
    name: id,
    configured: true,
    enabled: true,
    ...extra,
  };
}

const CATALOG: CatalogModel[] = [
  model("gpt-4o", "openai", {
    enabled: true,
    context_window: 128_000,
    input_cost_per_mtok: 2.5,
    supports_reasoning: true,
  }),
  model("gpt-4o-mini", "openai", { enabled: false, input_cost_per_mtok: 0.15 }),
  model("llama-3.3", "ollama", { enabled: true }),
];

function makePort(overrides: Partial<ModelsPort> = {}): ModelsPort {
  return {
    list: vi.fn<ModelsPort["list"]>().mockResolvedValue(CATALOG),
    setEnabled: vi.fn<ModelsPort["setEnabled"]>().mockResolvedValue(CATALOG),
    ...overrides,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("<ModelsPage>", () => {
  it("renders grouped models with metadata badges and the note", async () => {
    render(<ModelsPage port={makePort()} />);
    await screen.findByTestId("models-row-gpt-4o");
    expect(screen.getByText(MODELS_PAGE_NOTE)).toBeInTheDocument();
    expect(screen.getByTestId("models-group-openai")).toBeInTheDocument();
    expect(screen.getByTestId("models-group-ollama")).toBeInTheDocument();
    // gpt-4o shows ctx + price + reasoning badge.
    expect(screen.getByText("128K ctx")).toBeInTheDocument();
    expect(screen.getByText("$2.50/M in")).toBeInTheDocument();
    expect(screen.getByText("reasoning")).toBeInTheDocument();
  });

  it("local models are always-on and their toggle is disabled", async () => {
    render(<ModelsPage port={makePort()} />);
    const toggle = await screen.findByTestId("models-toggle-llama-3.3");
    expect(toggle).toBeDisabled();
    expect(toggle).toHaveTextContent("Always on");
  });

  it("disabling an enabled model persists the reduced set", async () => {
    const port = makePort();
    render(<ModelsPage port={port} />);
    fireEvent.click(await screen.findByTestId("models-toggle-gpt-4o"));
    await waitFor(() =>
      expect(port.setEnabled).toHaveBeenCalledWith(
        // gpt-4o removed; gpt-4o-mini was already off; llama stays.
        expect.arrayContaining(["llama-3.3"]),
      ),
    );
    const arg = (port.setEnabled as ReturnType<typeof vi.fn>).mock
      .calls[0][0] as string[];
    expect(arg).not.toContain("gpt-4o");
  });

  it("enabling a disabled model adds it to the set", async () => {
    const port = makePort();
    render(<ModelsPage port={port} />);
    fireEvent.click(await screen.findByTestId("models-toggle-gpt-4o-mini"));
    await waitFor(() =>
      expect(port.setEnabled).toHaveBeenCalledWith(
        expect.arrayContaining(["gpt-4o", "gpt-4o-mini", "llama-3.3"]),
      ),
    );
  });

  it("adds a custom model id", async () => {
    const port = makePort();
    render(<ModelsPage port={port} />);
    fireEvent.change(await screen.findByTestId("models-custom-input"), {
      target: { value: "vendor/custom-model" },
    });
    fireEvent.click(screen.getByTestId("models-custom-add"));
    await waitFor(() =>
      expect(port.setEnabled).toHaveBeenCalledWith(
        expect.arrayContaining(["vendor/custom-model"]),
      ),
    );
  });

  it("resets to recommended defaults with null", async () => {
    const port = makePort();
    render(<ModelsPage port={port} />);
    fireEvent.click(await screen.findByTestId("models-reset"));
    await waitFor(() => expect(port.setEnabled).toHaveBeenCalledWith(null));
  });

  it("filters by search query", async () => {
    render(<ModelsPage port={makePort()} />);
    fireEvent.change(await screen.findByTestId("models-search"), {
      target: { value: "mini" },
    });
    await waitFor(() =>
      expect(screen.queryByTestId("models-row-gpt-4o")).toBeNull(),
    );
    expect(screen.getByTestId("models-row-gpt-4o-mini")).toBeInTheDocument();
  });

  it("surfaces a load error with a Retry", async () => {
    const list = vi
      .fn<ModelsPort["list"]>()
      .mockRejectedValueOnce(new Error("catalog unavailable"))
      .mockResolvedValue(CATALOG);
    render(<ModelsPage port={makePort({ list })} />);
    const alert = await screen.findByTestId("models-error");
    expect(alert).toHaveTextContent("catalog unavailable");
    fireEvent.click(screen.getByTestId("models-retry"));
    await screen.findByTestId("models-row-gpt-4o");
    expect(list).toHaveBeenCalledTimes(2);
  });
});
