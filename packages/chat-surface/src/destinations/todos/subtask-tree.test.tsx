import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { TodoId } from "@0x-copilot/api-types";

import { SubtaskTree, type SubtaskTreeTodo } from "./subtask-tree";

const PARENT: SubtaskTreeTodo = {
  id: "todo_parent" as TodoId,
  text: "Ship Phase 3",
  done: false,
  parent_id: null,
  project_id: "proj_atlas",
};

function mkSubtask(
  id: string,
  text: string,
  done: boolean,
  parentId: string = "todo_parent",
): SubtaskTreeTodo {
  return {
    id: id as TodoId,
    text,
    done,
    parent_id: parentId as TodoId,
    project_id: "proj_atlas",
  };
}

describe("<SubtaskTree>", () => {
  it("renders parent + children count and exposes the collapse affordance", () => {
    render(
      <SubtaskTree
        parent={PARENT}
        subtasks={[
          mkSubtask("s1", "draft PRD", true),
          mkSubtask("s2", "code recurrence editor", false),
          mkSubtask("s3", "code subtask tree", false),
        ]}
        onAddSubtask={vi.fn()}
        onCompleteSubtask={vi.fn()}
      />,
    );
    const tree = screen.getByTestId("subtask-tree");
    expect(tree).toHaveAttribute("data-parent-id", "todo_parent");
    expect(tree).toHaveAttribute("data-subtask-count", "3");

    expect(screen.getByTestId("subtask-tree-collapse")).toHaveAttribute(
      "aria-expanded",
      "true",
    );
    expect(screen.getAllByTestId("subtask-row")).toHaveLength(3);
  });

  it("collapse button hides the body but keeps the header", () => {
    render(
      <SubtaskTree
        parent={PARENT}
        subtasks={[mkSubtask("s1", "draft PRD", false)]}
        onAddSubtask={vi.fn()}
        onCompleteSubtask={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByTestId("subtask-tree-collapse"));
    const tree = screen.getByTestId("subtask-tree");
    expect(tree).toHaveAttribute("data-collapsed", "true");
    expect(screen.queryByTestId("subtask-row")).not.toBeInTheDocument();
    expect(screen.getByTestId("subtask-tree-collapse")).toHaveAttribute(
      "aria-expanded",
      "false",
    );
  });

  it("toggling a subtask calls onCompleteSubtask with the right shape", () => {
    const onCompleteSubtask = vi.fn();
    render(
      <SubtaskTree
        parent={PARENT}
        subtasks={[mkSubtask("s1", "draft PRD", false)]}
        onAddSubtask={vi.fn()}
        onCompleteSubtask={onCompleteSubtask}
      />,
    );
    fireEvent.click(screen.getByTestId("subtask-row-toggle"));
    expect(onCompleteSubtask).toHaveBeenCalledWith({
      subtaskId: "s1",
      nextDone: true,
    });
  });

  it("submitting the inline add calls onAddSubtask with parent id + inherited project", () => {
    const onAddSubtask = vi.fn();
    render(
      <SubtaskTree
        parent={PARENT}
        subtasks={[]}
        onAddSubtask={onAddSubtask}
        onCompleteSubtask={vi.fn()}
      />,
    );
    const input = screen.getByTestId(
      "subtask-tree-add-input",
    ) as HTMLInputElement;
    fireEvent.change(input, { target: { value: "  write tests  " } });
    fireEvent.click(screen.getByTestId("subtask-tree-add-submit"));
    expect(onAddSubtask).toHaveBeenCalledWith({
      parentId: "todo_parent",
      text: "write tests",
      inheritedProjectId: "proj_atlas",
    });
    // Input cleared after submit.
    expect(input.value).toBe("");
  });

  it("Enter in the add input submits; Escape clears", () => {
    const onAddSubtask = vi.fn();
    render(
      <SubtaskTree
        parent={PARENT}
        subtasks={[]}
        onAddSubtask={onAddSubtask}
        onCompleteSubtask={vi.fn()}
      />,
    );
    const input = screen.getByTestId(
      "subtask-tree-add-input",
    ) as HTMLInputElement;
    fireEvent.change(input, { target: { value: "land it" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onAddSubtask).toHaveBeenCalledTimes(1);

    fireEvent.change(input, { target: { value: "scratch" } });
    fireEvent.keyDown(input, { key: "Escape" });
    expect(input.value).toBe("");
  });

  it("does not submit empty / whitespace-only drafts", () => {
    const onAddSubtask = vi.fn();
    render(
      <SubtaskTree
        parent={PARENT}
        subtasks={[]}
        onAddSubtask={onAddSubtask}
        onCompleteSubtask={vi.fn()}
      />,
    );
    const submit = screen.getByTestId(
      "subtask-tree-add-submit",
    ) as HTMLButtonElement;
    expect(submit.disabled).toBe(true);

    const input = screen.getByTestId(
      "subtask-tree-add-input",
    ) as HTMLInputElement;
    fireEvent.change(input, { target: { value: "   " } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onAddSubtask).not.toHaveBeenCalled();
  });

  it("shows the 'all subtasks done · mark parent done?' CTA when applicable", () => {
    const onCompleteParent = vi.fn();
    render(
      <SubtaskTree
        parent={PARENT}
        subtasks={[mkSubtask("s1", "a", true), mkSubtask("s2", "b", true)]}
        onAddSubtask={vi.fn()}
        onCompleteSubtask={vi.fn()}
        onCompleteParent={onCompleteParent}
      />,
    );
    const hint = screen.getByTestId("subtask-tree-mark-parent-hint");
    expect(hint).toHaveTextContent(/all subtasks done/i);
    fireEvent.click(screen.getByTestId("subtask-tree-mark-parent-cta"));
    expect(onCompleteParent).toHaveBeenCalledWith({ parentId: "todo_parent" });
  });

  it("hides the 'mark parent done' CTA when the parent is already done", () => {
    render(
      <SubtaskTree
        parent={{ ...PARENT, done: true }}
        subtasks={[mkSubtask("s1", "a", true)]}
        onAddSubtask={vi.fn()}
        onCompleteSubtask={vi.fn()}
      />,
    );
    expect(
      screen.queryByTestId("subtask-tree-mark-parent-hint"),
    ).not.toBeInTheDocument();
  });

  it("filters out stale non-children (one-level invariant from §11.2)", () => {
    // A subtask whose `parent_id` does not match `parent.id` MUST NOT
    // render, even if the server (or a stale cache) hands it to us.
    render(
      <SubtaskTree
        parent={PARENT}
        subtasks={[
          mkSubtask("s1", "legit child", false, "todo_parent"),
          mkSubtask("s_orphan", "wrong parent", false, "todo_other"),
        ]}
        onAddSubtask={vi.fn()}
        onCompleteSubtask={vi.fn()}
      />,
    );
    const rows = screen.getAllByTestId("subtask-row");
    expect(rows).toHaveLength(1);
    expect(rows[0]).toHaveAttribute("data-subtask-id", "s1");
    expect(screen.getByTestId("subtask-tree")).toHaveAttribute(
      "data-subtask-count",
      "1",
    );
  });

  it("renders the inherited-project hint only when parent has a project_id", () => {
    const { rerender } = render(
      <SubtaskTree
        parent={PARENT}
        subtasks={[]}
        onAddSubtask={vi.fn()}
        onCompleteSubtask={vi.fn()}
      />,
    );
    expect(screen.getByTestId("subtask-inherited-project")).toBeInTheDocument();

    rerender(
      <SubtaskTree
        parent={{ ...PARENT, project_id: null }}
        subtasks={[]}
        onAddSubtask={vi.fn()}
        onCompleteSubtask={vi.fn()}
      />,
    );
    expect(
      screen.queryByTestId("subtask-inherited-project"),
    ).not.toBeInTheDocument();
  });

  it("done subtask text is prefixed 'Completed:' for screen readers", () => {
    render(
      <SubtaskTree
        parent={PARENT}
        subtasks={[mkSubtask("s1", "ship", true)]}
        onAddSubtask={vi.fn()}
        onCompleteSubtask={vi.fn()}
      />,
    );
    const row = screen.getByTestId("subtask-row");
    expect(row).toHaveAttribute("data-done", "true");
    expect(row).toHaveTextContent(/Completed: ship/);
  });
});
