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
  reasoningDepth: null,
  webAccess: false,
  toolCallsPerRun: null,
  connectorSuggestions: "unblock_only",
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
    // Auto maps to the empty DOM value (null sentinel); the rest are the
    // canonical runtime depths under the design's Quick/Standard/Deep labels.
    expect(
      Array.from(select.querySelectorAll("option")).map((o) => o.value),
    ).toEqual(["", "fast", "balanced", "deep"]);
    expect(
      Array.from(select.querySelectorAll("option")).map((o) => o.textContent),
    ).toEqual(["Auto", "Quick", "Standard", "Deep"]);

    fireEvent.change(select, { target: { value: "deep" } });
    expect(onChange).toHaveBeenCalledWith({ reasoningDepth: "deep" });

    // Selecting Auto (empty value) reports null.
    fireEvent.change(select, { target: { value: "" } });
    expect(onChange).toHaveBeenCalledWith({ reasoningDepth: null });
  });

  it("reports tool-call-cap edits, and blank means the deployment default", () => {
    const onChange = vi.fn();
    const { rerender } = render(
      <ModelBehaviorPage
        value={BASE_VALUE}
        onChange={onChange}
        controller={makeController()}
      />,
    );
    const input = screen.getByTestId("tool-calls-per-run-input");

    // Unset renders blank, with the default surfaced as the placeholder so
    // "blank" reads as a concrete number rather than an unknown.
    expect((input as HTMLInputElement).value).toBe("");
    expect((input as HTMLInputElement).placeholder).toBe("10");

    fireEvent.change(input, { target: { value: "25" } });
    expect(onChange).toHaveBeenCalledWith({ toolCallsPerRun: 25 });

    // Clearing the field returns to the deployment default.
    rerender(
      <ModelBehaviorPage
        value={{ ...BASE_VALUE, toolCallsPerRun: 25 }}
        onChange={onChange}
        controller={makeController()}
      />,
    );
    fireEvent.change(screen.getByTestId("tool-calls-per-run-input"), {
      target: { value: "  " },
    });
    expect(onChange).toHaveBeenCalledWith({ toolCallsPerRun: null });
  });

  it("keeps the tool-call cap inside the range the server accepts", () => {
    // Clamping here (rather than letting the save 400) means the user sees the
    // corrected number as they type instead of an error after pressing Save.
    const onChange = vi.fn();
    render(
      <ModelBehaviorPage
        value={BASE_VALUE}
        onChange={onChange}
        controller={makeController()}
      />,
    );
    const input = screen.getByTestId("tool-calls-per-run-input");

    for (const [typed, expected] of [
      ["0", 1],
      ["-5", 1],
      ["101", 100],
      ["7.9", 7],
    ] as const) {
      fireEvent.change(input, { target: { value: typed } });
      expect(onChange).toHaveBeenCalledWith({ toolCallsPerRun: expected });
    }

    // Non-numeric input is "unset", never NaN on the wire.
    fireEvent.change(input, { target: { value: "abc" } });
    expect(onChange).toHaveBeenCalledWith({ toolCallsPerRun: null });
  });

  it("reports connector-suggestion appetite changes", () => {
    const onChange = vi.fn();
    render(
      <ModelBehaviorPage
        value={BASE_VALUE}
        onChange={onChange}
        controller={makeController()}
      />,
    );
    const select = getSelect("connector-suggestions-select");

    // The shipped default: a suggestion is the one connector surface the user
    // did not go looking for, so it interrupts only when it would unblock.
    expect(select.value).toBe("unblock_only");
    expect(
      Array.from(select.querySelectorAll("option")).map((o) => o.value),
    ).toEqual(["off", "unblock_only", "always"]);

    fireEvent.change(select, { target: { value: "off" } });
    expect(onChange).toHaveBeenCalledWith({ connectorSuggestions: "off" });
  });

  it("says where a single connector is muted, since that is not this control", () => {
    // The per-connector mute lives on the suggestion card (that is where the
    // intent forms) and is undone in the list below. Without the pointer, a
    // user looking to silence one vendor sets the global control to Never.
    render(
      <ModelBehaviorPage
        value={BASE_VALUE}
        onChange={vi.fn()}
        controller={makeController()}
      />,
    );
    const card = screen.getByTestId("connector-suggestions");
    expect(card.textContent).toMatch(/from its suggestion card/i);
    expect(card.textContent).toMatch(/listed below/i);
  });

  // The reversal. A card's Deny persists, which is only defensible because the
  // decision is visible and undoable — otherwise one misclick removes a
  // connector from every future run's suggestions with no way to notice.
  describe("muted connectors", () => {
    it("lists them under the appetite control", () => {
      render(
        <ModelBehaviorPage
          value={BASE_VALUE}
          onChange={vi.fn()}
          controller={makeController()}
          mutedConnectors={[
            { slug: "linear", displayName: "Linear" },
            { slug: "google-drive", displayName: "Google Drive" },
          ]}
        />,
      );
      const list = screen.getByTestId("muted-connectors");
      expect(list.textContent).toMatch(/Linear/);
      expect(list.textContent).toMatch(/Google Drive/);
    });

    it("unmutes by slug", () => {
      const onUnmute = vi.fn();
      render(
        <ModelBehaviorPage
          value={BASE_VALUE}
          onChange={vi.fn()}
          controller={makeController()}
          mutedConnectors={[
            { slug: "google-drive", displayName: "Google Drive" },
          ]}
          onUnmuteConnector={onUnmute}
        />,
      );
      fireEvent.click(screen.getByTestId("unmute-google-drive"));
      expect(onUnmute).toHaveBeenCalledWith("google-drive");
    });

    it("renders nothing when there is nothing to undo", () => {
      // The card stays a single row for the many users who never mute.
      render(
        <ModelBehaviorPage
          value={BASE_VALUE}
          onChange={vi.fn()}
          controller={makeController()}
        />,
      );
      expect(screen.queryByTestId("muted-connectors")).toBeNull();
    });
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

// PRD-shell-execution §7.3 / §14.4 — the "Run commands" list is MOUNTED under
// the approval policy, and only when the host supplies it.
//
// The section's own behaviour is pinned in `WorkspaceShellAccess.test.tsx`.
// What these two assert is the thing an export alone does not: that the surface
// is reachable from the page a user actually opens, and that it disappears
// cleanly on a host that has no shell. A component that is exported, tested and
// never rendered is the "landed, not wired" defect — and here it would mean the
// only control that can turn command execution on does not exist in the product.
describe("<ModelBehaviorPage> — Run commands (§14.4)", () => {
  it("omits the section entirely when the host supplies nothing", () => {
    render(
      <ModelBehaviorPage
        value={BASE_VALUE}
        onChange={vi.fn()}
        controller={makeController()}
      />,
    );
    expect(
      screen.queryByTestId("workspace-shell-access"),
    ).not.toBeInTheDocument();
  });

  it("omits it again when the host supplies a list but no setter", () => {
    // Two independent gates. The host may know the grants (it renders the
    // composer's folder bar from the same hook) and still have no way to change
    // this flag — web is exactly that shape.
    render(
      <ModelBehaviorPage
        value={BASE_VALUE}
        onChange={vi.fn()}
        controller={makeController()}
        workspaceShellAccess={{
          grants: [
            {
              grantId: "g_atlas",
              mount: "m_atlas",
              label: "atlas",
              mode: "read_write",
              shellEnabled: false,
            },
          ],
          onSetShellEnabled: null,
        }}
      />,
    );
    expect(
      screen.queryByTestId("workspace-shell-access"),
    ).not.toBeInTheDocument();
  });

  it("renders it after the approval policy when the host wires it", () => {
    render(
      <ModelBehaviorPage
        value={BASE_VALUE}
        onChange={vi.fn()}
        controller={makeController()}
        workspaceShellAccess={{
          grants: [
            {
              grantId: "g_atlas",
              mount: "m_atlas",
              label: "atlas",
              mode: "read_write",
              shellEnabled: false,
            },
          ],
          onSetShellEnabled: vi.fn(),
        }}
      />,
    );
    const section = screen.getByTestId("workspace-shell-access");
    expect(section).toBeInTheDocument();
    expect(
      screen.getByTestId("workspace-shell-toggle-g_atlas"),
    ).not.toBeChecked();

    // ORDER, not just presence. §14.4 places it under the tool-policy surface;
    // a control that grants command authority must not sit above the policy
    // that governs how each command is approved.
    const policy = screen.getByTestId("approval-policy");
    expect(
      policy.compareDocumentPosition(section) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });
});
