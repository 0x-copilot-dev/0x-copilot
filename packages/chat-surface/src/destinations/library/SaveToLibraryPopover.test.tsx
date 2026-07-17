// SaveToLibraryPopover tests (P7-B1).
//
// Covers: default kind override, name autofocus, project picker
// integration, tag parsing, submit + error rendering, cancel, escape.

import type { ProjectId } from "@0x-copilot/api-types";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import {
  SaveToLibraryPopover,
  type SaveToLibrarySubmit,
} from "./SaveToLibraryPopover";
import type { ProjectFilterChipOption } from "../projects/ProjectFilterChip";

const asProjectId = (s: string): ProjectId => s as unknown as ProjectId;

const PROJECT_OPTIONS: ReadonlyArray<ProjectFilterChipOption> = [
  {
    id: asProjectId("proj_acme"),
    name: "Acme renewal",
    icon_emoji: "🚀",
    color_hue: 30,
    status: "active",
    viewer_starred: false,
  },
];

describe("SaveToLibraryPopover", () => {
  it("renders the title, kind row, name field, and tags field", () => {
    render(
      <SaveToLibraryPopover
        fromSource="chat_tool_result"
        defaultKind="dataset"
        defaultName="leads.json"
        onSubmit={vi.fn()}
      />,
    );
    expect(screen.getByTestId("save-to-library-popover")).toBeInTheDocument();
    expect(screen.getByTestId("save-to-library-kind-row")).toBeInTheDocument();
    expect(screen.getByTestId("save-to-library-name")).toBeInTheDocument();
    expect(screen.getByTestId("save-to-library-tags")).toBeInTheDocument();
  });

  it("uses the call-site's default kind", () => {
    render(
      <SaveToLibraryPopover
        fromSource="chat_agent_msg"
        defaultKind="page"
        defaultName="Renewal playbook"
        onSubmit={vi.fn()}
      />,
    );
    expect(screen.getByTestId("save-to-library-popover")).toHaveAttribute(
      "data-kind",
      "page",
    );
  });

  it("flips the kind when the user clicks a different button", () => {
    render(
      <SaveToLibraryPopover
        fromSource="chat_tool_result"
        defaultKind="file"
        defaultName="report.pdf"
        onSubmit={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByTestId("save-to-library-kind-dataset"));
    expect(screen.getByTestId("save-to-library-popover")).toHaveAttribute(
      "data-kind",
      "dataset",
    );
  });

  it("autofocuses the name input on mount", () => {
    render(
      <SaveToLibraryPopover
        fromSource="chat_thread_pin"
        defaultKind="page"
        defaultName="Q3 sync notes"
        onSubmit={vi.fn()}
      />,
    );
    const input = screen.getByTestId("save-to-library-name");
    expect(input).toBe(document.activeElement);
  });

  it("submits the payload with parsed tags + selected kind", async () => {
    const onSubmit = vi
      .fn<(payload: SaveToLibrarySubmit) => Promise<void>>()
      .mockResolvedValue(undefined);
    render(
      <SaveToLibraryPopover
        fromSource="chat_tool_result"
        defaultKind="page"
        defaultName="Renewal playbook"
        defaultTags={["renewal", "q3"]}
        onSubmit={onSubmit}
      />,
    );
    fireEvent.change(screen.getByTestId("save-to-library-tags"), {
      target: { value: "renewal, q3, urgent" },
    });
    fireEvent.click(screen.getByTestId("save-to-library-submit"));
    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    expect(onSubmit).toHaveBeenCalledWith({
      kind: "page",
      name: "Renewal playbook",
      project_id: null,
      tags: ["renewal", "q3", "urgent"],
    });
  });

  it("disables submit when the name is empty", () => {
    render(
      <SaveToLibraryPopover
        fromSource="chat_tool_result"
        defaultKind="page"
        defaultName=""
        onSubmit={vi.fn()}
      />,
    );
    const submit = screen.getByTestId(
      "save-to-library-submit",
    ) as HTMLButtonElement;
    expect(submit.disabled).toBe(true);
  });

  it("renders the project picker when projects are supplied and forwards selection", async () => {
    const onSubmit = vi
      .fn<(payload: SaveToLibrarySubmit) => Promise<void>>()
      .mockResolvedValue(undefined);
    render(
      <SaveToLibraryPopover
        fromSource="run_completion"
        defaultKind="file"
        defaultName="run-output.pdf"
        projects={PROJECT_OPTIONS}
        defaultProjectId={asProjectId("proj_acme")}
        onSubmit={onSubmit}
      />,
    );
    expect(screen.getByTestId("project-filter-chip")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("save-to-library-submit"));
    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    expect(onSubmit.mock.calls[0]![0].project_id).toBe("proj_acme");
  });

  it("renders the error message when onSubmit rejects", async () => {
    const onSubmit = vi
      .fn<(payload: SaveToLibrarySubmit) => Promise<void>>()
      .mockRejectedValue(new Error("quota exceeded"));
    render(
      <SaveToLibraryPopover
        fromSource="routine_output"
        defaultKind="page"
        defaultName="weekly summary"
        onSubmit={onSubmit}
      />,
    );
    fireEvent.click(screen.getByTestId("save-to-library-submit"));
    await waitFor(() =>
      expect(screen.getByTestId("save-to-library-error")).toHaveTextContent(
        "quota exceeded",
      ),
    );
  });

  it("invokes onCancel when the cancel button is clicked", () => {
    const onCancel = vi.fn();
    render(
      <SaveToLibraryPopover
        fromSource="chat_tool_result"
        defaultKind="file"
        defaultName="report.pdf"
        onSubmit={vi.fn()}
        onCancel={onCancel}
      />,
    );
    fireEvent.click(screen.getByTestId("save-to-library-cancel"));
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it("renders the source preview disclosure when preview is supplied", () => {
    render(
      <SaveToLibraryPopover
        fromSource="chat_tool_result"
        defaultKind="dataset"
        defaultName="leads"
        preview={<span>first 200 rows…</span>}
        onSubmit={vi.fn()}
      />,
    );
    expect(screen.getByTestId("save-to-library-preview")).toBeInTheDocument();
    expect(screen.getByText("first 200 rows…")).toBeInTheDocument();
  });

  it("shows the from-source subtitle", () => {
    render(
      <SaveToLibraryPopover
        fromSource="chat_agent_msg"
        defaultKind="page"
        defaultName="msg"
        onSubmit={vi.fn()}
      />,
    );
    expect(screen.getByTestId("save-to-library-from-label")).toHaveTextContent(
      "From an agent message",
    );
  });
});
