// ProjectEditor tests (P6.5-B1).
//
// Covers: tab switching, name/description editing, connector allowlist
// tri-mode semantics (null vs []), allowlist chip toggling, save payload
// shape, members slot, owner-only gating, dirty state, error handling.

import type { ProjectId } from "@0x-copilot/api-types";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import {
  ProjectEditor,
  type ProjectEditorConnectorOption,
  type ProjectEditorConnectorSlug,
  type ProjectEditorValue,
} from "./ProjectEditor";

import type { ProjectColorHue, ProjectIconEmoji } from "@0x-copilot/api-types";

const asProjectId = (s: string): ProjectId => s as unknown as ProjectId;
const asSlug = (s: string): ProjectEditorConnectorSlug =>
  s as unknown as ProjectEditorConnectorSlug;

function makeValue(over: Partial<ProjectEditorValue> = {}): ProjectEditorValue {
  return {
    id: asProjectId("proj_1"),
    name: "Acme renewal",
    description: "Push the Q4 renewal across the line.",
    iconEmoji: "📁" as ProjectIconEmoji,
    colorHue: 180 as ProjectColorHue,
    defaultConnectorAllowlist: null,
    ...over,
  };
}

const CONNECTORS: ReadonlyArray<ProjectEditorConnectorOption> = [
  { slug: asSlug("salesforce"), label: "Salesforce" },
  { slug: asSlug("gmail"), label: "Gmail" },
  { slug: asSlug("slack"), label: "Slack" },
];

describe("ProjectEditor", () => {
  it("renders all four tab options and starts on metadata by default", () => {
    render(
      <ProjectEditor
        value={makeValue()}
        availableConnectors={CONNECTORS}
        onSave={vi.fn()}
      />,
    );
    expect(screen.getByTestId("project-editor")).toHaveAttribute(
      "data-active-tab",
      "metadata",
    );
    for (const slug of ["metadata", "appearance", "connectors", "members"]) {
      expect(screen.getByTestId(`filter-tab-${slug}`)).toBeInTheDocument();
    }
    expect(
      screen.getByTestId("project-editor-tab-metadata"),
    ).toBeInTheDocument();
  });

  it("switches tabs when the host does not provide controlled state", () => {
    render(
      <ProjectEditor
        value={makeValue()}
        availableConnectors={CONNECTORS}
        onSave={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByTestId("filter-tab-connectors"));
    expect(screen.getByTestId("project-editor")).toHaveAttribute(
      "data-active-tab",
      "connectors",
    );
    expect(
      screen.getByTestId("project-editor-tab-connectors"),
    ).toBeInTheDocument();
  });

  it("calls onTabChange when controlled and does not flip internal state", () => {
    const onTabChange = vi.fn();
    render(
      <ProjectEditor
        value={makeValue()}
        availableConnectors={CONNECTORS}
        activeTab="metadata"
        onTabChange={onTabChange}
        onSave={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByTestId("filter-tab-appearance"));
    expect(onTabChange).toHaveBeenCalledWith("appearance");
    // Stayed on the controlled value:
    expect(screen.getByTestId("project-editor")).toHaveAttribute(
      "data-active-tab",
      "metadata",
    );
  });

  it("is not dirty initially and dirty after editing name", () => {
    render(
      <ProjectEditor
        value={makeValue()}
        availableConnectors={CONNECTORS}
        onSave={vi.fn()}
      />,
    );
    expect(screen.getByTestId("project-editor")).toHaveAttribute(
      "data-dirty",
      "false",
    );
    fireEvent.change(screen.getByTestId("project-editor-name-input"), {
      target: { value: "Renamed" },
    });
    expect(screen.getByTestId("project-editor")).toHaveAttribute(
      "data-dirty",
      "true",
    );
  });

  it("connector tab defaults to `inherit` when allowlist is null", () => {
    render(
      <ProjectEditor
        value={makeValue({ defaultConnectorAllowlist: null })}
        availableConnectors={CONNECTORS}
        onSave={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByTestId("filter-tab-connectors"));
    expect(screen.getByTestId("project-editor-tab-connectors")).toHaveAttribute(
      "data-allowlist-mode",
      "inherit",
    );
  });

  it("connector tab shows `none` when allowlist is []", () => {
    render(
      <ProjectEditor
        value={makeValue({ defaultConnectorAllowlist: [] })}
        availableConnectors={CONNECTORS}
        onSave={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByTestId("filter-tab-connectors"));
    expect(screen.getByTestId("project-editor-tab-connectors")).toHaveAttribute(
      "data-allowlist-mode",
      "none",
    );
  });

  it("connector tab shows `allowlist` and renders chips when allowlist is non-empty", () => {
    render(
      <ProjectEditor
        value={makeValue({
          defaultConnectorAllowlist: [asSlug("salesforce")],
        })}
        availableConnectors={CONNECTORS}
        onSave={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByTestId("filter-tab-connectors"));
    expect(screen.getByTestId("project-editor-tab-connectors")).toHaveAttribute(
      "data-allowlist-mode",
      "allowlist",
    );
    expect(
      screen.getByTestId("project-editor-allowlist-chip-salesforce"),
    ).toHaveAttribute("data-selected", "true");
    expect(
      screen.getByTestId("project-editor-allowlist-chip-gmail"),
    ).toHaveAttribute("data-selected", "false");
  });

  it("switching to `allowlist` mode preserves the empty-array distinction from null", () => {
    // Starting from inherit (null), switching to allowlist enters with [].
    render(
      <ProjectEditor
        value={makeValue({ defaultConnectorAllowlist: null })}
        availableConnectors={CONNECTORS}
        onSave={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByTestId("filter-tab-connectors"));
    fireEvent.click(screen.getByTestId("project-editor-mode-allowlist"));
    expect(screen.getByTestId("project-editor-tab-connectors")).toHaveAttribute(
      "data-allowlist-mode",
      "allowlist",
    );
    expect(screen.getByTestId("project-editor")).toHaveAttribute(
      "data-dirty",
      "true",
    );
  });

  it("renders EmptyState when allowlist mode is on but no connectors are available", () => {
    render(
      <ProjectEditor
        value={makeValue({ defaultConnectorAllowlist: [] })}
        availableConnectors={[]}
        onSave={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByTestId("filter-tab-connectors"));
    fireEvent.click(screen.getByTestId("project-editor-mode-allowlist"));
    expect(screen.getByTestId("empty-state")).toBeInTheDocument();
    expect(screen.getByText("No connectors available")).toBeInTheDocument();
  });

  it("toggling a chip adds the slug; toggling again removes it", () => {
    render(
      <ProjectEditor
        value={makeValue({
          defaultConnectorAllowlist: [asSlug("salesforce")],
        })}
        availableConnectors={CONNECTORS}
        onSave={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByTestId("filter-tab-connectors"));
    const gmailChip = screen.getByTestId("project-editor-allowlist-chip-gmail");
    expect(gmailChip).toHaveAttribute("data-selected", "false");
    fireEvent.click(gmailChip);
    expect(gmailChip).toHaveAttribute("data-selected", "true");
    fireEvent.click(gmailChip);
    expect(gmailChip).toHaveAttribute("data-selected", "false");
  });

  it("calls onSave with the trimmed payload preserving allowlist semantics", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(
      <ProjectEditor
        value={makeValue({
          defaultConnectorAllowlist: [asSlug("gmail")],
        })}
        availableConnectors={CONNECTORS}
        onSave={onSave}
      />,
    );
    fireEvent.change(screen.getByTestId("project-editor-name-input"), {
      target: { value: "  Acme NEW  " },
    });
    fireEvent.click(screen.getByTestId("filter-tab-connectors"));
    fireEvent.click(
      screen.getByTestId("project-editor-allowlist-chip-salesforce"),
    );
    fireEvent.click(screen.getByTestId("project-editor-save"));

    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    const payload = onSave.mock.calls[0]![0]!;
    expect(payload.name).toBe("Acme NEW");
    expect(payload.defaultConnectorAllowlist).toEqual([
      asSlug("gmail"),
      asSlug("salesforce"),
    ]);
  });

  it("blocks save when name is empty and shows an inline error", () => {
    const onSave = vi.fn();
    render(
      <ProjectEditor
        value={makeValue()}
        availableConnectors={CONNECTORS}
        onSave={onSave}
      />,
    );
    fireEvent.change(screen.getByTestId("project-editor-name-input"), {
      target: { value: "   " },
    });
    fireEvent.click(screen.getByTestId("project-editor-save"));
    expect(onSave).not.toHaveBeenCalled();
    expect(screen.getByTestId("project-editor-error")).toHaveTextContent(
      "Name is required.",
    );
  });

  it("hides the Save button and disables inputs when canEdit=false", () => {
    render(
      <ProjectEditor
        value={makeValue()}
        availableConnectors={CONNECTORS}
        canEdit={false}
        onSave={vi.fn()}
      />,
    );
    expect(screen.queryByTestId("project-editor-save")).not.toBeInTheDocument();
    expect(screen.getByTestId("project-editor-name-input")).toBeDisabled();
  });

  it("invokes the renderMembersTab slot when the Members tab is active", () => {
    const renderMembersTab = vi.fn(() => (
      <div data-testid="my-members">members go here</div>
    ));
    render(
      <ProjectEditor
        value={makeValue()}
        availableConnectors={CONNECTORS}
        renderMembersTab={renderMembersTab}
        onSave={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByTestId("filter-tab-members"));
    expect(screen.getByTestId("my-members")).toBeInTheDocument();
    expect(renderMembersTab).toHaveBeenCalled();
  });

  it("renders a fallback EmptyState on the Members tab when no slot is supplied", () => {
    render(
      <ProjectEditor
        value={makeValue()}
        availableConnectors={CONNECTORS}
        onSave={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByTestId("filter-tab-members"));
    expect(screen.getByText("Members tab not wired")).toBeInTheDocument();
  });

  it("renders the optional onDelete button with the supplied label", () => {
    const onDelete = vi.fn();
    render(
      <ProjectEditor
        value={makeValue()}
        availableConnectors={CONNECTORS}
        onSave={vi.fn()}
        onDelete={onDelete}
        deleteLabel="Archive"
      />,
    );
    const btn = screen.getByTestId("project-editor-delete");
    expect(btn).toHaveTextContent("Archive");
    fireEvent.click(btn);
    expect(onDelete).toHaveBeenCalledTimes(1);
  });

  it("surfaces save errors inline and clears submitting state", async () => {
    const onSave = vi.fn().mockRejectedValue(new Error("boom"));
    render(
      <ProjectEditor
        value={makeValue()}
        availableConnectors={CONNECTORS}
        onSave={onSave}
      />,
    );
    fireEvent.change(screen.getByTestId("project-editor-name-input"), {
      target: { value: "Updated name" },
    });
    fireEvent.click(screen.getByTestId("project-editor-save"));
    const error = await screen.findByTestId("project-editor-error");
    expect(error).toHaveTextContent("boom");
  });
});
