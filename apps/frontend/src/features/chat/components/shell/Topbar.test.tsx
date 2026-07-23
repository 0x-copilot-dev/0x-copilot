import type { ModelCatalogModel } from "@0x-copilot/api-types";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
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
  it("renders identity row + status (single row, PR 8.0.2)", () => {
    // PR 8.0.2 — model + thinking-depth controls moved into the
    // composer. The topbar collapses to a single row (identity +
    // status pills only).
    render(<Topbar {...baseProps} />);
    expect(screen.getByText("Acme")).toBeInTheDocument();
    expect(screen.getByText("Launches")).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(
      "Q1 launch",
    );
    expect(
      screen
        .getAllByRole("status")
        .find((node) => node.classList.contains("ui-badge")),
    ).toHaveTextContent("Ready");
    // No model pill, no depth control in topbar — they live in the composer.
    expect(screen.queryByRole("radiogroup")).toBeNull();
    expect(screen.queryByRole("button", { name: /Model:/ })).toBeNull();
  });

  it("does not render the depth control regardless of `depthVisible` (moved to composer)", () => {
    render(<Topbar {...baseProps} depthVisible={false} />);
    expect(screen.queryByRole("radiogroup")).toBeNull();
    render(<Topbar {...baseProps} depthVisible={true} />);
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

  it("propagates chromeDisabled to the topbar root", () => {
    // The connectors pill moved to the composer, so the topbar carries
    // identity + status + share/settings only. ``chromeDisabled`` now
    // surfaces via the ``data-chrome-disabled`` attribute and the
    // disabled rename affordance on `<ConversationTitle>`.
    const { container } = render(<Topbar {...baseProps} chromeDisabled />);
    expect(container.querySelector(".atlas-topbar")).toHaveAttribute(
      "data-chrome-disabled",
      "true",
    );
  });

  describe("depth announcement", () => {
    beforeEach(() => {
      vi.useFakeTimers({ shouldAdvanceTime: true });
    });
    afterEach(() => {
      vi.runOnlyPendingTimers();
      vi.useRealTimers();
    });

    it("announces a polite message when depth changes", async () => {
      const { rerender } = render(<Topbar {...baseProps} depth="balanced" />);
      // Mount renders an empty live region — no announcement on first paint.
      expect(
        screen
          .getAllByRole("status")
          .map((node) => node.textContent ?? "")
          .filter((text) => text.includes("Depth:")),
      ).toHaveLength(0);
      rerender(<Topbar {...baseProps} depth="deep" />);
      await waitFor(() => {
        expect(
          screen
            .getAllByRole("status")
            .some((node) => node.textContent?.includes("Depth: Deep")),
        ).toBe(true);
      });
      // Region clears after ~2s so screen readers don't replay stale text.
      act(() => {
        vi.advanceTimersByTime(2100);
      });
      await waitFor(() => {
        expect(
          screen
            .getAllByRole("status")
            .some((node) => (node.textContent ?? "").includes("Depth:")),
        ).toBe(false);
      });
    });
  });

  describe("depth announcement", () => {
    beforeEach(() => {
      vi.useFakeTimers({ shouldAdvanceTime: true });
    });
    afterEach(() => {
      vi.runOnlyPendingTimers();
      vi.useRealTimers();
    });

    it("announces a polite message when depth changes", async () => {
      const { rerender } = render(<Topbar {...baseProps} depth="balanced" />);
      // Mount renders an empty live region — no announcement on first paint.
      expect(
        screen
          .getAllByRole("status")
          .map((node) => node.textContent ?? "")
          .filter((text) => text.includes("Depth:")),
      ).toHaveLength(0);
      rerender(<Topbar {...baseProps} depth="deep" />);
      await waitFor(() => {
        expect(
          screen
            .getAllByRole("status")
            .some((node) => node.textContent?.includes("Depth: Deep")),
        ).toBe(true);
      });
      // Region clears after ~2s so screen readers don't replay stale text.
      act(() => {
        vi.advanceTimersByTime(2100);
      });
      await waitFor(() => {
        expect(
          screen
            .getAllByRole("status")
            .some((node) => (node.textContent ?? "").includes("Depth:")),
        ).toBe(false);
      });
    });
  });
});
