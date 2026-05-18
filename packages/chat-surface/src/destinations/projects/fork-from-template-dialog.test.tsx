// ForkFromTemplateDialog tests (P6.5-B1).

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import {
  ForkFromTemplateDialog,
  type ForkFromTemplateSnapshotSummary,
} from "./fork-from-template-dialog";

import type { ProjectTemplateId } from "./TemplateGallery";

const asId = (s: string): ProjectTemplateId =>
  s as unknown as ProjectTemplateId;

function makeSummary(
  over: Partial<ForkFromTemplateSnapshotSummary> = {},
): ForkFromTemplateSnapshotSummary {
  return {
    defaultIconEmoji: "📊",
    defaultColorHue: 210,
    suggestedMemberCount: 4,
    defaultConnectorAllowlist: ["salesforce"],
    seededTodosCount: 3,
    seededRoutinesCount: 1,
    ...over,
  };
}

describe("ForkFromTemplateDialog", () => {
  it("returns null when open=false", () => {
    const { container } = render(
      <ForkFromTemplateDialog
        open={false}
        onClose={vi.fn()}
        templateId={asId("tpl_1")}
        templateName="X"
        snapshot={makeSummary()}
        onFork={vi.fn()}
      />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("renders the dialog with the template name in the title", () => {
    render(
      <ForkFromTemplateDialog
        open
        onClose={vi.fn()}
        templateId={asId("tpl_1")}
        templateName="QBR"
        snapshot={makeSummary()}
        onFork={vi.fn()}
      />,
    );
    expect(screen.getByTestId("fork-from-template-dialog")).toBeInTheDocument();
    expect(screen.getByText(/Fork from .*QBR/)).toBeInTheDocument();
  });

  it("disables Fork until a name is entered", () => {
    render(
      <ForkFromTemplateDialog
        open
        onClose={vi.fn()}
        templateId={asId("tpl_1")}
        templateName="QBR"
        snapshot={makeSummary()}
        onFork={vi.fn()}
      />,
    );
    const submit = screen.getByTestId("fork-from-template-confirm");
    expect(submit).toBeDisabled();
    fireEvent.change(screen.getByTestId("fork-from-template-name-input"), {
      target: { value: "New project" },
    });
    expect(submit).not.toBeDisabled();
  });

  it("calls onFork with the user payload (overrides honored)", async () => {
    const onFork = vi.fn().mockResolvedValue(undefined);
    const onClose = vi.fn();
    render(
      <ForkFromTemplateDialog
        open
        onClose={onClose}
        templateId={asId("tpl_x")}
        templateName="QBR"
        snapshot={makeSummary()}
        onFork={onFork}
      />,
    );
    fireEvent.change(screen.getByTestId("fork-from-template-name-input"), {
      target: { value: "Acme Q4" },
    });
    fireEvent.change(
      screen.getByTestId("fork-from-template-description-input"),
      {
        target: { value: "Acme renewal" },
      },
    );
    fireEvent.click(screen.getByTestId("fork-from-template-icon-🚀"));
    fireEvent.click(screen.getByTestId("fork-from-template-color-30"));
    fireEvent.click(screen.getByTestId("fork-from-template-confirm"));
    await waitFor(() => expect(onFork).toHaveBeenCalledTimes(1));
    expect(onFork).toHaveBeenCalledWith({
      templateId: asId("tpl_x"),
      name: "Acme Q4",
      description: "Acme renewal",
      iconEmoji: "🚀",
      colorHue: 30,
    });
    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
  });

  it("passes null description when the description input is empty", async () => {
    const onFork = vi.fn().mockResolvedValue(undefined);
    render(
      <ForkFromTemplateDialog
        open
        onClose={vi.fn()}
        templateId={asId("tpl_1")}
        templateName="QBR"
        snapshot={makeSummary()}
        onFork={onFork}
      />,
    );
    fireEvent.change(screen.getByTestId("fork-from-template-name-input"), {
      target: { value: "Acme" },
    });
    fireEvent.click(screen.getByTestId("fork-from-template-confirm"));
    await waitFor(() => expect(onFork).toHaveBeenCalledTimes(1));
    expect(onFork.mock.calls[0]![0]!.description).toBeNull();
  });

  it("surfaces fork errors inline and keeps the dialog open", async () => {
    const onFork = vi.fn().mockRejectedValue(new Error("server fail"));
    const onClose = vi.fn();
    render(
      <ForkFromTemplateDialog
        open
        onClose={onClose}
        templateId={asId("tpl_1")}
        templateName="QBR"
        snapshot={makeSummary()}
        onFork={onFork}
      />,
    );
    fireEvent.change(screen.getByTestId("fork-from-template-name-input"), {
      target: { value: "Acme" },
    });
    fireEvent.click(screen.getByTestId("fork-from-template-confirm"));
    const error = await screen.findByTestId("fork-from-template-error");
    expect(error).toHaveTextContent("server fail");
    expect(onClose).not.toHaveBeenCalled();
  });

  it("Cancel fires onClose without calling onFork", () => {
    const onFork = vi.fn();
    const onClose = vi.fn();
    render(
      <ForkFromTemplateDialog
        open
        onClose={onClose}
        templateId={asId("tpl_1")}
        templateName="QBR"
        snapshot={makeSummary()}
        onFork={onFork}
      />,
    );
    fireEvent.click(screen.getByTestId("fork-from-template-cancel"));
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(onFork).not.toHaveBeenCalled();
  });

  it("renders the snapshot summary (connector mode and seeded counts)", () => {
    render(
      <ForkFromTemplateDialog
        open
        onClose={vi.fn()}
        templateId={asId("tpl_1")}
        templateName="QBR"
        snapshot={makeSummary({
          defaultConnectorAllowlist: null,
          seededTodosCount: 2,
          seededRoutinesCount: 0,
        })}
        onFork={vi.fn()}
      />,
    );
    expect(
      screen.getByTestId("fork-from-template-snapshot-summary"),
    ).toBeInTheDocument();
    expect(screen.getByText("Connectors: Inherit")).toBeInTheDocument();
    expect(
      screen.getByText(/Will seed 2 todos and 0 routines/),
    ).toBeInTheDocument();
  });

  it("uses snapshot defaults for icon and color when the dialog opens", () => {
    render(
      <ForkFromTemplateDialog
        open
        onClose={vi.fn()}
        templateId={asId("tpl_1")}
        templateName="QBR"
        snapshot={makeSummary({ defaultIconEmoji: "🎯", defaultColorHue: 120 })}
        onFork={vi.fn()}
      />,
    );
    const preview = screen.getByTestId("fork-from-template-preview");
    expect(preview).toHaveAttribute("data-color-hue", "120");
    expect(preview).toHaveTextContent("🎯");
  });
});
