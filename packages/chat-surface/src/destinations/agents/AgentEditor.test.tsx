import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import {
  AGENT_EDITOR_DEFAULTS,
  AgentEditor,
  type AgentEditorValue,
} from "./AgentEditor";

function makeInitial(overrides?: Partial<AgentEditorValue>): AgentEditorValue {
  return {
    ...AGENT_EDITOR_DEFAULTS,
    name: "Inbox Triage",
    description: "Triage incoming inbox items.",
    instructions: "Sort and tag items.",
    ...(overrides ?? {}),
  };
}

describe("<AgentEditor />", () => {
  it("renders with defaults in the create flow (no initialValue)", () => {
    render(<AgentEditor onSave={vi.fn()} />);
    const root = screen.getByTestId("agent-editor");
    expect(root.getAttribute("data-status")).toBe("draft");
    expect(root.getAttribute("data-active-tab")).toBe("identity");
    expect(screen.getByTestId("agent-editor-status-pill")).toHaveTextContent(
      "Draft",
    );
  });

  it("renders all 5 tabs with ARIA wiring and arrow-key navigation", () => {
    render(<AgentEditor onSave={vi.fn()} initialValue={makeInitial()} />);
    const tablist = screen.getByRole("tablist", { name: /agent editor/i });
    expect(within(tablist).getAllByRole("tab")).toHaveLength(5);

    const identityTab = screen.getByTestId("agent-editor-tab-identity");
    expect(identityTab.getAttribute("aria-selected")).toBe("true");

    fireEvent.keyDown(tablist, { key: "ArrowRight" });
    expect(
      screen
        .getByTestId("agent-editor-tab-behavior")
        .getAttribute("aria-selected"),
    ).toBe("true");

    fireEvent.keyDown(tablist, { key: "End" });
    expect(
      screen
        .getByTestId("agent-editor-tab-permissions")
        .getAttribute("aria-selected"),
    ).toBe("true");

    fireEvent.keyDown(tablist, { key: "Home" });
    expect(identityTab.getAttribute("aria-selected")).toBe("true");
  });

  it("flips status pill from Draft to Ready once name + instructions are set", () => {
    render(<AgentEditor onSave={vi.fn()} initialValue={makeInitial()} />);
    expect(screen.getByTestId("agent-editor-status-pill")).toHaveTextContent(
      "Ready",
    );
  });

  it("invokes onSave with the assembled draft (identity edits flow through)", () => {
    const onSave = vi.fn();
    render(<AgentEditor onSave={onSave} initialValue={makeInitial()} />);

    const nameInput = screen.getByTestId("agent-editor-name-input");
    fireEvent.change(nameInput, { target: { value: "Inbox Hero" } });

    fireEvent.click(screen.getByTestId("agent-editor-save"));
    expect(onSave).toHaveBeenCalledTimes(1);
    expect(onSave.mock.calls[0]![0].name).toBe("Inbox Hero");
  });

  it("save button reflects saveState with visual feedback", () => {
    const { rerender } = render(
      <AgentEditor
        onSave={vi.fn()}
        initialValue={makeInitial()}
        saveState="idle"
      />,
    );
    expect(screen.getByTestId("agent-editor-save")).toHaveTextContent("Save");

    rerender(
      <AgentEditor
        onSave={vi.fn()}
        initialValue={makeInitial()}
        saveState="saving"
      />,
    );
    const btn = screen.getByTestId("agent-editor-save");
    expect(btn).toHaveTextContent(/saving/i);
    expect(btn).toBeDisabled();

    rerender(
      <AgentEditor
        onSave={vi.fn()}
        initialValue={makeInitial()}
        saveState="saved"
      />,
    );
    expect(screen.getByTestId("agent-editor-save")).toHaveTextContent(/saved/i);
  });

  it("Behavior tab updates model id and reasoning depth", () => {
    const onSave = vi.fn();
    render(
      <AgentEditor
        onSave={onSave}
        initialValue={makeInitial()}
        availableModels={[
          { model_id: "anthropic:claude-opus-4-7", label: "Opus" },
          { model_id: "anthropic:claude-sonnet-4-7", label: "Sonnet" },
        ]}
      />,
    );

    fireEvent.click(screen.getByTestId("agent-editor-tab-behavior"));
    fireEvent.change(screen.getByTestId("agent-editor-model-input"), {
      target: { value: "anthropic:claude-opus-4-7" },
    });
    fireEvent.click(screen.getByTestId("agent-editor-depth-deep"));

    fireEvent.click(screen.getByTestId("agent-editor-save"));
    const value = onSave.mock.calls[0]![0] as AgentEditorValue;
    expect(value.model_default.model_id).toBe("anthropic:claude-opus-4-7");
    expect(value.model_default.reasoning_depth).toBe("deep");
  });

  it("renders the chat-surface Composer in the Behavior tab (SP-1 reuse)", () => {
    render(<AgentEditor onSave={vi.fn()} initialValue={makeInitial()} />);
    fireEvent.click(screen.getByTestId("agent-editor-tab-behavior"));
    // Composer's stable test id is exposed in chat-surface composer.tsx.
    expect(screen.getByTestId("composer")).toBeInTheDocument();
  });

  it("Connectors tab toggles connector membership", () => {
    const onSave = vi.fn();
    render(
      <AgentEditor
        onSave={onSave}
        initialValue={makeInitial()}
        availableConnectors={[
          { connector_id: "slack", label: "Slack" },
          { connector_id: "drive", label: "Drive" },
        ]}
      />,
    );
    fireEvent.click(screen.getByTestId("agent-editor-tab-connectors"));
    fireEvent.click(screen.getByTestId("agent-editor-connector-slack-toggle"));
    fireEvent.click(screen.getByTestId("agent-editor-save"));
    const value = onSave.mock.calls[0]![0] as AgentEditorValue;
    expect(value.connectors_default).toEqual(["slack"]);
  });

  it("Skills tab toggles skill membership", () => {
    const onSave = vi.fn();
    render(
      <AgentEditor
        onSave={onSave}
        initialValue={makeInitial()}
        availableSkills={[
          { skill_id: "summarize", label: "Summarize" },
          { skill_id: "extract", label: "Extract" },
        ]}
      />,
    );
    fireEvent.click(screen.getByTestId("agent-editor-tab-skills"));
    fireEvent.click(screen.getByTestId("agent-editor-skill-extract-toggle"));
    fireEvent.click(screen.getByTestId("agent-editor-save"));
    const value = onSave.mock.calls[0]![0] as AgentEditorValue;
    expect(value.skills).toEqual(["extract"]);
  });

  it("Permissions tab edits autonomy, read-only, and limits", () => {
    const onSave = vi.fn();
    render(<AgentEditor onSave={onSave} initialValue={makeInitial()} />);
    fireEvent.click(screen.getByTestId("agent-editor-tab-permissions"));
    fireEvent.click(screen.getByTestId("agent-editor-autonomy-auto_apply"));
    fireEvent.click(
      within(screen.getByTestId("agent-editor-read-only-toggle")).getByRole(
        "checkbox",
      ),
    );
    fireEvent.change(screen.getByTestId("agent-editor-max-tool-calls"), {
      target: { value: "5" },
    });
    fireEvent.change(screen.getByTestId("agent-editor-blocked-tool-families"), {
      target: { value: "filesystem, network" },
    });
    fireEvent.click(screen.getByTestId("agent-editor-save"));
    const value = onSave.mock.calls[0]![0] as AgentEditorValue;
    expect(value.permissions.autonomy).toBe("auto_apply");
    expect(value.permissions.read_only).toBe(true);
    expect(value.permissions.max_tool_calls_per_run).toBe(5);
    expect(value.permissions.blocked_tool_families).toEqual([
      "filesystem",
      "network",
    ]);
  });

  it("renders Save-as-version CTA only when onSnapshot is provided (edit flow)", () => {
    const { rerender } = render(
      <AgentEditor onSave={vi.fn()} initialValue={makeInitial()} />,
    );
    expect(
      screen.queryByTestId("agent-editor-save-as-version"),
    ).not.toBeInTheDocument();

    const onSnapshot = vi.fn();
    rerender(
      <AgentEditor
        onSave={vi.fn()}
        onSnapshot={onSnapshot}
        initialValue={makeInitial()}
      />,
    );
    fireEvent.click(screen.getByTestId("agent-editor-save-as-version"));
    expect(onSnapshot).toHaveBeenCalledTimes(1);
  });

  it("invokes onCancel from header cancel button", () => {
    const onCancel = vi.fn();
    render(
      <AgentEditor
        onSave={vi.fn()}
        onCancel={onCancel}
        initialValue={makeInitial()}
      />,
    );
    fireEvent.click(screen.getByTestId("agent-editor-cancel"));
    expect(onCancel).toHaveBeenCalledTimes(1);
  });
});
