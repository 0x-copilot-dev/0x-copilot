// TemplateEditor tests (P6.5-B1).

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import {
  TemplateEditor,
  type TemplateEditorSnapshot,
  type TemplateEditorValue,
} from "./TemplateEditor";

import type { ProjectTemplateId } from "./TemplateGallery";

const asId = (s: string): ProjectTemplateId =>
  s as unknown as ProjectTemplateId;

function makeSnapshot(
  over: Partial<TemplateEditorSnapshot> = {},
): TemplateEditorSnapshot {
  return {
    memberCount: 3,
    defaultConnectorAllowlist: ["salesforce", "gmail"],
    colorHue: 210,
    iconEmoji: "📊",
    seededTodos: [
      { text: "Send kickoff email", priority: "normal", relativeDueDays: 1 },
      { text: "Schedule QBR", priority: "high", relativeDueDays: 7 },
    ],
    seededRoutines: [
      {
        name: "Weekly status",
        description: "Send a weekly status update",
        triggerSummary: "Mondays at 09:00",
      },
    ],
    ...over,
  };
}

function makeValue(
  over: Partial<TemplateEditorValue> = {},
): TemplateEditorValue {
  return {
    id: asId("tpl_1"),
    name: "Quarterly review",
    description: "Recurring QBR.",
    snapshot: makeSnapshot(),
    ...over,
  };
}

describe("TemplateEditor", () => {
  it("renders the editor with name and description prefilled", () => {
    render(<TemplateEditor value={makeValue()} onSave={vi.fn()} />);
    expect(screen.getByTestId("template-editor")).toHaveAttribute(
      "data-dirty",
      "false",
    );
    expect(screen.getByTestId("template-editor-name-input")).toHaveValue(
      "Quarterly review",
    );
    expect(screen.getByTestId("template-editor-description-input")).toHaveValue(
      "Recurring QBR.",
    );
  });

  it("becomes dirty when the name is edited and clean when reverted", () => {
    render(<TemplateEditor value={makeValue()} onSave={vi.fn()} />);
    fireEvent.change(screen.getByTestId("template-editor-name-input"), {
      target: { value: "QBR (renamed)" },
    });
    expect(screen.getByTestId("template-editor")).toHaveAttribute(
      "data-dirty",
      "true",
    );
    fireEvent.change(screen.getByTestId("template-editor-name-input"), {
      target: { value: "Quarterly review" },
    });
    expect(screen.getByTestId("template-editor")).toHaveAttribute(
      "data-dirty",
      "false",
    );
  });

  it("calls onSave with the trimmed metadata payload (snapshot is NOT included)", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(<TemplateEditor value={makeValue()} onSave={onSave} />);
    fireEvent.change(screen.getByTestId("template-editor-name-input"), {
      target: { value: "  New name  " },
    });
    fireEvent.change(screen.getByTestId("template-editor-description-input"), {
      target: { value: " New body " },
    });
    fireEvent.click(screen.getByTestId("template-editor-save"));
    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    expect(onSave).toHaveBeenCalledWith({
      name: "New name",
      description: "New body",
    });
  });

  it("toggles the snapshot preview body when the toggle is clicked", () => {
    render(<TemplateEditor value={makeValue()} onSave={vi.fn()} />);
    expect(
      screen.queryByTestId("template-editor-snapshot-body"),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId("template-editor-snapshot-toggle"));
    expect(
      screen.getByTestId("template-editor-snapshot-body"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("template-editor-snapshot-preview"),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("template-editor-snapshot-toggle"));
    expect(
      screen.queryByTestId("template-editor-snapshot-body"),
    ).not.toBeInTheDocument();
  });

  it("renders snapshot details including seeded todos and routines when expanded", () => {
    render(
      <TemplateEditor
        value={makeValue()}
        onSave={vi.fn()}
        snapshotDefaultOpen
      />,
    );
    expect(
      screen.getByTestId("template-editor-snapshot-body"),
    ).toBeInTheDocument();
    expect(screen.getAllByTestId("template-editor-snapshot-todo")).toHaveLength(
      2,
    );
    expect(
      screen.getAllByTestId("template-editor-snapshot-routine"),
    ).toHaveLength(1);
  });

  it("renders the connector summary with the right tone for inherit / none / allowlist", () => {
    // inherit
    const { rerender } = render(
      <TemplateEditor
        value={makeValue({
          snapshot: makeSnapshot({ defaultConnectorAllowlist: null }),
        })}
        onSave={vi.fn()}
        snapshotDefaultOpen
      />,
    );
    expect(screen.getByText("Inherit defaults")).toBeInTheDocument();
    // none
    rerender(
      <TemplateEditor
        value={makeValue({
          snapshot: makeSnapshot({ defaultConnectorAllowlist: [] }),
        })}
        onSave={vi.fn()}
        snapshotDefaultOpen
      />,
    );
    expect(screen.getByText("No connectors")).toBeInTheDocument();
    // allowlist
    rerender(
      <TemplateEditor
        value={makeValue({
          snapshot: makeSnapshot({
            defaultConnectorAllowlist: ["a", "b", "c"],
          }),
        })}
        onSave={vi.fn()}
        snapshotDefaultOpen
      />,
    );
    expect(screen.getByText("3 connectors")).toBeInTheDocument();
  });

  it("hides the Save button when canEdit is false", () => {
    render(
      <TemplateEditor value={makeValue()} canEdit={false} onSave={vi.fn()} />,
    );
    expect(
      screen.queryByTestId("template-editor-save"),
    ).not.toBeInTheDocument();
    expect(screen.getByTestId("template-editor-name-input")).toBeDisabled();
  });

  it("renders the Delete button and fires onDelete when clicked", () => {
    const onDelete = vi.fn();
    render(
      <TemplateEditor
        value={makeValue()}
        onSave={vi.fn()}
        onDelete={onDelete}
      />,
    );
    fireEvent.click(screen.getByTestId("template-editor-delete"));
    expect(onDelete).toHaveBeenCalledTimes(1);
  });

  it("blocks save with inline error when name is blank", () => {
    const onSave = vi.fn();
    render(<TemplateEditor value={makeValue()} onSave={onSave} />);
    fireEvent.change(screen.getByTestId("template-editor-name-input"), {
      target: { value: "   " },
    });
    fireEvent.click(screen.getByTestId("template-editor-save"));
    expect(onSave).not.toHaveBeenCalled();
    expect(screen.getByTestId("template-editor-error")).toHaveTextContent(
      "Name is required.",
    );
  });
});
