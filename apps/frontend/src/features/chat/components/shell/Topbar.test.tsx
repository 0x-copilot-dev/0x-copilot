import type { ModelCatalogModel } from "@enterprise-search/api-types";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { RunUiState } from "../../chatRunState";
import { Topbar } from "./Topbar";

const models: Array<ModelCatalogModel & { disabled?: boolean }> = [
  {
    id: "openai/gpt-5.4",
    provider: "openai",
    model_name: "gpt-5.4",
    name: "GPT-5.4",
    configured: true,
    supports_reasoning: true,
  },
];

const baseRunUi: RunUiState = {
  phase: "idle",
  headerStatus: "Ready",
  showPlanningIndicator: false,
  planningLabel: "Planning next step...",
};

const baseProps = {
  workspace: "Acme",
  folder: "Launches",
  title: "Q1 launch",
  runUiState: baseRunUi,
  sidebarCollapsed: false,
  onToggleSidebar: () => undefined,
  panelOpen: true,
  onTogglePanel: () => undefined,
  connectors: [],
  connectorsOpen: false,
  onOpenConnectors: () => undefined,
  usagePct: 64,
  onOpenUsage: () => undefined,
  models,
  selectedModel: "openai/gpt-5.4",
  onModelChange: () => undefined,
  depth: "balanced" as const,
  onDepthChange: () => undefined,
  depthVisible: true,
  onShare: () => undefined,
  onOpenSettings: () => undefined,
};

describe("Topbar", () => {
  it("renders identity row + status + per-run controls", () => {
    render(<Topbar {...baseProps} />);
    expect(screen.getByText("Acme")).toBeInTheDocument();
    expect(screen.getByText("Launches")).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(
      "Q1 launch",
    );
    expect(screen.getByRole("status")).toHaveTextContent("Ready");
    expect(screen.getByRole("radiogroup")).toBeInTheDocument();
  });

  it("hides depth control when not visible", () => {
    render(<Topbar {...baseProps} depthVisible={false} />);
    expect(screen.queryByRole("radiogroup")).toBeNull();
  });

  it("calls share / settings handlers", () => {
    const onShare = vi.fn();
    const onOpenSettings = vi.fn();
    render(
      <Topbar
        {...baseProps}
        onShare={onShare}
        onOpenSettings={onOpenSettings}
      />,
    );
    fireEvent.click(screen.getByLabelText("Share this conversation"));
    fireEvent.click(screen.getByLabelText("Open settings"));
    expect(onShare).toHaveBeenCalledTimes(1);
    expect(onOpenSettings).toHaveBeenCalledTimes(1);
  });

  it("disables interactive controls when chromeDisabled", () => {
    render(<Topbar {...baseProps} chromeDisabled />);
    // Model pill button is the only one with the model name accessible label
    expect(
      screen.getByRole("button", { name: /Model: GPT-5\.4/ }),
    ).toBeDisabled();
  });
});
