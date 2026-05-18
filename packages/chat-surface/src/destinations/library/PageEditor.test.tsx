// Tests for <PageEditor /> (P7-B2).

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { PageEditor, type PageEditorSaveStatus } from "./PageEditor";

function makeProps(
  overrides: {
    title?: string;
    markdown?: string;
    saveStatus?: PageEditorSaveStatus;
    onChange?: (next: { title: string; markdown: string }) => void;
    onSave?: () => void;
    now?: () => number;
  } = {},
): {
  title: string;
  markdown: string;
  saveStatus: PageEditorSaveStatus;
  onChange: (next: { title: string; markdown: string }) => void;
  onSave: () => void;
  now?: () => number;
} {
  return {
    title: overrides.title ?? "Untitled",
    markdown: overrides.markdown ?? "# Hi",
    onChange:
      overrides.onChange ??
      vi.fn<(next: { title: string; markdown: string }) => void>(),
    onSave: overrides.onSave ?? vi.fn<() => void>(),
    saveStatus:
      overrides.saveStatus ?? ({ kind: "idle" } as PageEditorSaveStatus),
    now: overrides.now,
  };
}

describe("<PageEditor>", () => {
  it("renders title input + textarea + Streamdown preview by default (split view)", () => {
    render(<PageEditor {...makeProps()} />);
    expect(screen.getByTestId("library-page-editor-title")).toBeTruthy();
    expect(screen.getByTestId("library-page-editor-textarea")).toBeTruthy();
    expect(screen.getByTestId("library-page-editor-preview")).toBeTruthy();
    expect(
      screen.getByTestId("library-page-editor").getAttribute("data-view"),
    ).toBe("split");
  });

  it("calls onChange when title or markdown is edited", () => {
    const onChange =
      vi.fn<(next: { title: string; markdown: string }) => void>();
    render(<PageEditor {...makeProps({ onChange })} />);
    fireEvent.change(screen.getByTestId("library-page-editor-title"), {
      target: { value: "New title" },
    });
    expect(onChange).toHaveBeenCalledWith({
      title: "New title",
      markdown: "# Hi",
    });
    fireEvent.change(screen.getByTestId("library-page-editor-textarea"), {
      target: { value: "# Hi\n\nmore" },
    });
    expect(onChange).toHaveBeenLastCalledWith({
      title: "Untitled",
      markdown: "# Hi\n\nmore",
    });
  });

  it("calls onSave when Save is clicked", () => {
    const onSave = vi.fn<() => void>();
    render(<PageEditor {...makeProps({ onSave })} />);
    fireEvent.click(screen.getByTestId("library-page-editor-save"));
    expect(onSave).toHaveBeenCalledOnce();
  });

  it("shows 'Saving…' and disables save button when saveStatus=saving", () => {
    render(<PageEditor {...makeProps({ saveStatus: { kind: "saving" } })} />);
    const btn = screen.getByTestId(
      "library-page-editor-save",
    ) as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
    expect(btn.textContent).toContain("Saving");
  });

  it("renders 'Saved Ns ago' chip when saveStatus=saved (sign-of-life)", () => {
    // saved 3 seconds ago, with a frozen `now`.
    const at = "2026-05-17T10:00:00Z";
    const nowMs = Date.parse(at) + 3_000;
    render(
      <PageEditor
        {...makeProps({
          saveStatus: { kind: "saved", at },
          now: () => nowMs,
        })}
      />,
    );
    const chip = screen.getByTestId("library-page-editor-save-chip");
    expect(chip.textContent).toContain("3s ago");
    expect(chip.getAttribute("data-save-status")).toBe("saved");
  });

  it("renders 'just now' for saves under 5 seconds", () => {
    const at = "2026-05-17T10:00:00Z";
    const nowMs = Date.parse(at) + 1_000;
    render(
      <PageEditor
        {...makeProps({
          saveStatus: { kind: "saved", at },
          now: () => nowMs,
        })}
      />,
    );
    expect(
      screen.getByTestId("library-page-editor-save-chip").textContent,
    ).toContain("just now");
  });

  it("toggles between split/edit/preview views", () => {
    render(<PageEditor {...makeProps()} />);
    fireEvent.click(screen.getByTestId("library-page-editor-view-preview"));
    expect(
      screen.getByTestId("library-page-editor").getAttribute("data-view"),
    ).toBe("preview");
    expect(screen.queryByTestId("library-page-editor-textarea")).toBeNull();
    fireEvent.click(screen.getByTestId("library-page-editor-view-edit"));
    expect(screen.queryByTestId("library-page-editor-preview")).toBeNull();
    expect(screen.getByTestId("library-page-editor-textarea")).toBeTruthy();
  });

  it("renders conflict banner with View their version / Overwrite", () => {
    const onViewRemote = vi.fn<() => void>();
    const onOverwrite = vi.fn<() => void>();
    render(
      <PageEditor
        {...makeProps({
          saveStatus: {
            kind: "conflict",
            remoteVersion: 7,
            message: "Page updated in another tab",
          },
        })}
        onViewRemote={onViewRemote}
        onOverwrite={onOverwrite}
      />,
    );
    expect(screen.getByText("Page updated in another tab")).toBeTruthy();
    fireEvent.click(screen.getByTestId("library-page-editor-conflict-view"));
    expect(onViewRemote).toHaveBeenCalledOnce();
    fireEvent.click(
      screen.getByTestId("library-page-editor-conflict-overwrite"),
    );
    expect(onOverwrite).toHaveBeenCalledOnce();
  });

  it("respects controlled activeView prop", () => {
    const onViewChange = vi.fn<(view: "edit" | "preview" | "split") => void>();
    const { rerender } = render(
      <PageEditor
        {...makeProps()}
        activeView="edit"
        onViewChange={onViewChange}
      />,
    );
    expect(
      screen.getByTestId("library-page-editor").getAttribute("data-view"),
    ).toBe("edit");
    fireEvent.click(screen.getByTestId("library-page-editor-view-split"));
    expect(onViewChange).toHaveBeenCalledWith("split");
    // host hasn't updated activeView yet — view should still read "edit".
    expect(
      screen.getByTestId("library-page-editor").getAttribute("data-view"),
    ).toBe("edit");
    rerender(
      <PageEditor
        {...makeProps()}
        activeView="split"
        onViewChange={onViewChange}
      />,
    );
    expect(
      screen.getByTestId("library-page-editor").getAttribute("data-view"),
    ).toBe("split");
  });
});
