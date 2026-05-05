import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ConversationTitle } from "./ConversationTitle";

describe("ConversationTitle", () => {
  it("renders the title", () => {
    render(<ConversationTitle title="Q1 launch" />);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(
      "Q1 launch",
    );
  });

  it("falls back when title is null", () => {
    render(<ConversationTitle title={null} />);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(
      "Untitled chat",
    );
  });

  it("does not enter edit mode without onRename", () => {
    render(<ConversationTitle title="Q1 launch" />);
    fireEvent.doubleClick(screen.getByRole("heading", { level: 1 }));
    expect(screen.queryByLabelText("Edit conversation title")).toBeNull();
  });

  it("enters edit mode on double-click and commits on Enter", () => {
    const onRename = vi.fn().mockResolvedValue(undefined);
    render(<ConversationTitle title="Old" onRename={onRename} />);
    fireEvent.doubleClick(screen.getByRole("heading", { level: 1 }));
    const input = screen.getByLabelText("Edit conversation title");
    fireEvent.change(input, { target: { value: "New title" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onRename).toHaveBeenCalledWith("New title");
  });

  it("cancels on Escape without firing onRename", () => {
    const onRename = vi.fn();
    render(<ConversationTitle title="Old" onRename={onRename} />);
    fireEvent.doubleClick(screen.getByRole("heading", { level: 1 }));
    const input = screen.getByLabelText("Edit conversation title");
    fireEvent.change(input, { target: { value: "abandoned" } });
    fireEvent.keyDown(input, { key: "Escape" });
    expect(onRename).not.toHaveBeenCalled();
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Old");
  });

  it("does not call onRename when text is unchanged", () => {
    const onRename = vi.fn();
    render(<ConversationTitle title="Same" onRename={onRename} />);
    fireEvent.doubleClick(screen.getByRole("heading", { level: 1 }));
    fireEvent.keyDown(screen.getByLabelText("Edit conversation title"), {
      key: "Enter",
    });
    expect(onRename).not.toHaveBeenCalled();
  });

  it("does not enter edit mode when disabled", () => {
    const onRename = vi.fn();
    render(<ConversationTitle title="Locked" onRename={onRename} disabled />);
    fireEvent.doubleClick(screen.getByRole("heading", { level: 1 }));
    expect(screen.queryByLabelText("Edit conversation title")).toBeNull();
  });
});
