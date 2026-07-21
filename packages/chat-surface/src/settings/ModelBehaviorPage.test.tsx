// FR-5.16 / FR-5.18 — Model & behavior. Default-model select carries Cloud +
// Local optgroups sourced from props (never hardcoded); reasoning depth has the
// four spec options; web access, monthly cap, and pause-at-cap report edits; a
// dirty section docks its SaveBar through the injected surface controller; and
// loading / load-error / empty-models states never render a bare blank.

import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  ModelBehaviorPage,
  type ModelBehaviorModelOption,
  type ModelBehaviorValue,
} from "./ModelBehaviorPage";
import type { SettingsSurfaceController } from "./SettingsSurface";

const BASE_VALUE: ModelBehaviorValue = {
  defaultModel: null,
  reasoningDepth: "auto",
  webAccess: false,
  approvalPolicy: { readOnly: "auto", write: "require", danger: "require" },
  spend: { monthlyCapUsd: null, pauseAtCap: false },
};

const CLOUD: readonly ModelBehaviorModelOption[] = [
  { value: "gpt-4o", label: "GPT-4o", sub: "OpenAI" },
  { value: "claude-opus-4", label: "Claude Opus 4", sub: "Anthropic" },
];

const LOCAL: readonly ModelBehaviorModelOption[] = [
  { value: "llama3:8b", label: "Llama 3", sub: "8B" },
];

function makeController(): SettingsSurfaceController {
  return {
    setDirty: vi.fn(),
    showToast: vi.fn(),
    navigate: vi.fn(),
  };
}

function getSelect(testId: string): HTMLSelectElement {
  return screen.getByTestId(testId) as HTMLSelectElement;
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("<ModelBehaviorPage>", () => {
  it("uses the design IA: a 17px section heading over Defaults / Approval policy / Spend guardrail cards", () => {
    render(
      <ModelBehaviorPage
        value={BASE_VALUE}
        onChange={vi.fn()}
        controller={makeController()}
        cloudModels={CLOUD}
      />,
    );
    // The section title is the top-of-hierarchy <h1> (SecTitle); the blocks are
    // separate cards with <h3> titles (no outer "Model & behavior" card).
    expect(
      screen.getByRole("heading", { level: 1, name: "Model & behavior" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { level: 3, name: "Defaults" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { level: 3, name: "Approval policy" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { level: 3, name: "Spend guardrail" }),
    ).toBeInTheDocument();
  });

  it("sources the default-model optgroups from props", () => {
    render(
      <ModelBehaviorPage
        value={BASE_VALUE}
        onChange={vi.fn()}
        controller={makeController()}
        cloudModels={CLOUD}
        localModels={LOCAL}
      />,
    );

    const select = getSelect("default-model-select");
    const optgroups = Array.from(select.querySelectorAll("optgroup"));
    expect(optgroups.map((g) => g.label)).toEqual([
      "Cloud · your keys",
      "Local · your machine",
    ]);

    const optionValues = Array.from(select.querySelectorAll("option")).map(
      (o) => o.value,
    );
    expect(optionValues).toContain("gpt-4o");
    expect(optionValues).toContain("claude-opus-4");
    expect(optionValues).toContain("llama3:8b");
    // Metadata rides along in the label, not a separate hardcoded list.
    expect(
      screen.getByRole("option", { name: "GPT-4o · OpenAI" }),
    ).toBeTruthy();
    expect(screen.getByRole("option", { name: "Llama 3 · 8B" })).toBeTruthy();
  });

  it("omits an optgroup with no models and reports the picked default", () => {
    const onChange = vi.fn();
    render(
      <ModelBehaviorPage
        value={BASE_VALUE}
        onChange={onChange}
        controller={makeController()}
        cloudModels={CLOUD}
      />,
    );
    const select = getSelect("default-model-select");
    expect(select.querySelectorAll("optgroup")).toHaveLength(1);

    fireEvent.change(select, { target: { value: "gpt-4o" } });
    expect(onChange).toHaveBeenCalledWith({ defaultModel: "gpt-4o" });

    // The empty placeholder maps back to null (no explicit default).
    fireEvent.change(select, { target: { value: "" } });
    expect(onChange).toHaveBeenCalledWith({ defaultModel: null });
  });

  it("disables the select with an honest empty state when no models exist", () => {
    render(
      <ModelBehaviorPage
        value={BASE_VALUE}
        onChange={vi.fn()}
        controller={makeController()}
      />,
    );
    const select = getSelect("default-model-select");
    expect(select).toBeDisabled();
    expect(select.querySelectorAll("optgroup")).toHaveLength(0);
    expect(
      screen.getByRole("option", { name: "No models available" }),
    ).toBeTruthy();
  });

  it("offers the four reasoning depths and reports a change", () => {
    const onChange = vi.fn();
    render(
      <ModelBehaviorPage
        value={BASE_VALUE}
        onChange={onChange}
        controller={makeController()}
      />,
    );
    const select = getSelect("reasoning-depth-select");
    expect(
      Array.from(select.querySelectorAll("option")).map((o) => o.value),
    ).toEqual(["auto", "quick", "standard", "deep"]);

    fireEvent.change(select, { target: { value: "deep" } });
    expect(onChange).toHaveBeenCalledWith({ reasoningDepth: "deep" });
  });

  it("reports web-access toggles", () => {
    const onChange = vi.fn();
    render(
      <ModelBehaviorPage
        value={BASE_VALUE}
        onChange={onChange}
        controller={makeController()}
      />,
    );
    fireEvent.click(screen.getByTestId("web-access-toggle"));
    expect(onChange).toHaveBeenCalledWith({ webAccess: true });
  });

  it("reports monthly-cap edits as a whole spend block, clamping negatives and blanks", () => {
    const onChange = vi.fn();
    const { rerender } = render(
      <ModelBehaviorPage
        value={BASE_VALUE}
        onChange={onChange}
        controller={makeController()}
      />,
    );
    const input = screen.getByTestId("monthly-cap-input");

    fireEvent.change(input, { target: { value: "50" } });
    expect(onChange).toHaveBeenCalledWith({
      spend: { monthlyCapUsd: 50, pauseAtCap: false },
    });

    fireEvent.change(input, { target: { value: "-5" } });
    expect(onChange).toHaveBeenCalledWith({
      spend: { monthlyCapUsd: 0, pauseAtCap: false },
    });

    // The field is controlled, so clearing it only emits a change when it
    // currently holds a value — reflect a non-null cap first, then blank it.
    rerender(
      <ModelBehaviorPage
        value={{
          ...BASE_VALUE,
          spend: { monthlyCapUsd: 50, pauseAtCap: false },
        }}
        onChange={onChange}
        controller={makeController()}
      />,
    );
    fireEvent.change(input, { target: { value: "" } });
    expect(onChange).toHaveBeenCalledWith({
      spend: { monthlyCapUsd: null, pauseAtCap: false },
    });
  });

  it("reports pause-at-cap toggles", () => {
    const onChange = vi.fn();
    render(
      <ModelBehaviorPage
        value={BASE_VALUE}
        onChange={onChange}
        controller={makeController()}
      />,
    );
    fireEvent.click(screen.getByTestId("pause-at-cap-toggle"));
    expect(onChange).toHaveBeenCalledWith({
      spend: { monthlyCapUsd: null, pauseAtCap: true },
    });
  });

  it("embeds the approval-policy block", () => {
    render(
      <ModelBehaviorPage
        value={BASE_VALUE}
        onChange={vi.fn()}
        controller={makeController()}
      />,
    );
    expect(screen.getByTestId("approval-policy")).toBeInTheDocument();
    expect(
      screen.getByRole("radiogroup", { name: /write actions approval/i }),
    ).toBeInTheDocument();
  });

  it("docks the SaveBar through the controller when dirty and clears it when clean", () => {
    const controller = makeController();
    const onSave = vi.fn();
    const onDiscard = vi.fn();
    const { rerender } = render(
      <ModelBehaviorPage
        value={BASE_VALUE}
        onChange={vi.fn()}
        controller={controller}
        dirty
        saving
        onSave={onSave}
        onDiscard={onDiscard}
      />,
    );

    expect(controller.setDirty).toHaveBeenCalled();
    const registered = vi.mocked(controller.setDirty).mock.calls[0]![0];
    expect(registered).not.toBeNull();
    expect(registered!.saving).toBe(true);
    // The registered handlers delegate to the host's latest closures.
    registered!.onSave();
    registered!.onDiscard();
    expect(onSave).toHaveBeenCalledTimes(1);
    expect(onDiscard).toHaveBeenCalledTimes(1);

    rerender(
      <ModelBehaviorPage
        value={BASE_VALUE}
        onChange={vi.fn()}
        controller={controller}
        dirty={false}
        onSave={onSave}
        onDiscard={onDiscard}
      />,
    );
    expect(vi.mocked(controller.setDirty).mock.calls.at(-1)![0]).toBeNull();
  });

  it("surfaces a save error inline as a role=alert, distinct from the savebar", () => {
    render(
      <ModelBehaviorPage
        value={BASE_VALUE}
        onChange={vi.fn()}
        controller={makeController()}
        dirty
        saveError="Could not save."
      />,
    );
    const alert = screen.getByTestId("model-behavior-save-error");
    expect(alert).toHaveAttribute("role", "alert");
    expect(alert).toHaveTextContent("Could not save.");
  });

  it("renders a loading skeleton, never a blank", () => {
    render(
      <ModelBehaviorPage
        value={BASE_VALUE}
        onChange={vi.fn()}
        controller={makeController()}
        loading
      />,
    );
    expect(screen.getByTestId("model-behavior-loading")).toBeInTheDocument();
    expect(screen.queryByTestId("default-model-select")).toBeNull();
  });

  it("renders a load error with a Retry affordance", () => {
    const onRetry = vi.fn();
    render(
      <ModelBehaviorPage
        value={BASE_VALUE}
        onChange={vi.fn()}
        controller={makeController()}
        error="Facade unreachable"
        onRetry={onRetry}
      />,
    );
    const alert = screen.getByTestId("model-behavior-error");
    expect(alert).toHaveAttribute("role", "alert");
    fireEvent.click(screen.getByTestId("model-behavior-retry"));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });
});
