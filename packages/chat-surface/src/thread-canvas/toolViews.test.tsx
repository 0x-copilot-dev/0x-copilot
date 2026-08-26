import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { ToolCallEntry } from "./eventProjector";
import { ToolCallCard } from "./ToolCallCard";
import { basename, parsePathList, toolViewFor } from "./toolViews";

/** A tool call in the shape the projector produces AFTER the streamed-argument
 *  envelope is unwrapped — i.e. what a view is entitled to assume. */
function call(over: Partial<ToolCallEntry> & Pick<ToolCallEntry, "toolName">) {
  return {
    createdAtMs: 0,
    id: "call-1",
    sequenceNo: 1,
    runId: null,
    status: "complete",
    title: over.toolName,
    ...over,
  } as ToolCallEntry;
}

// Verbatim from a real packaged run's events.jsonl.
const EDIT_CALL = call({
  toolName: "edit_file",
  title: "Edit file",
  args: {
    file_path: "/Users/parthpahwa/Documents/work/kscope-benchmarks/random.csv",
    old_string: "5,orange,lynx,29,false",
    new_string: "5,orange,lynx,30,false",
  },
  result: {
    content:
      "Successfully replaced 1 instance(s) of the string in '/Users/parthpahwa/Documents/work/kscope-benchmarks/random.csv'",
  },
});

describe("toolViewFor", () => {
  it("returns a specialised view for each registered filesystem tool", () => {
    for (const name of [
      "edit_file",
      "write_file",
      "read_file",
      "glob",
      "grep",
      "ls",
    ]) {
      expect(toolViewFor(name).kind).not.toBe("generic");
    }
  });

  it("falls back to the generic view for an unregistered tool", () => {
    const view = toolViewFor("list_issues");
    expect(view.kind).toBe("generic");
    expect(view.Body).toBeNull();
    expect(view.icon).toBeNull();
  });
});

describe("basename", () => {
  it("takes the trailing segment", () => {
    expect(basename("/a/b/data.csv")).toBe("data.csv");
  });
  it("ignores a trailing slash", () => {
    expect(basename("/a/b/")).toBe("b");
  });
});

describe("parsePathList", () => {
  it("parses the Python list repr the runtime returns", () => {
    expect(parsePathList("['/a/one.csv', '/b/two.csv']")).toEqual([
      "/a/one.csv",
      "/b/two.csv",
    ]);
  });
  it("reads the empty list as no matches, not as an unknown shape", () => {
    expect(parsePathList("[]")).toEqual([]);
  });
  it("returns null for text that is not a list", () => {
    expect(parsePathList("Updated file /tmp/x")).toBeNull();
  });
});

describe("edit_file view", () => {
  it("renders a real diff of the replacement", () => {
    render(<ToolCallCard toolCall={EDIT_CALL} />);
    const diff = screen.getByTestId("tc-tool-edit-diff");
    expect(
      within(diff).getByTestId("tc-tool-edit-diff-counts").textContent,
    ).toBe("+1−1");
  });

  it("names the edited file on the row without opening it", () => {
    render(<ToolCallCard toolCall={EDIT_CALL} />);
    expect(screen.getByTestId("tc-tool-card-subtitle").textContent).toBe(
      "random.csv",
    );
  });

  it("demotes the raw payload behind its own disclosure", () => {
    render(<ToolCallCard toolCall={EDIT_CALL} />);
    const raw = screen.getByText("raw payload");
    expect(raw.tagName.toLowerCase()).toBe("summary");
  });

  it("renders no diff while the arguments are still streaming", () => {
    // Mid-stream the projector holds no parsed arguments at all; a half-diff
    // would be worse than none.
    render(
      <ToolCallCard toolCall={call({ toolName: "edit_file", args: {} })} />,
    );
    expect(screen.queryByTestId("tc-tool-edit-diff")).toBeNull();
  });
});

describe("write_file view", () => {
  it("renders the written content as all additions", () => {
    render(
      <ToolCallCard
        toolCall={call({
          toolName: "write_file",
          args: { file_path: "/tmp/new.csv", content: "id,name\n1,Olivia" },
        })}
      />,
    );
    expect(screen.getByTestId("tc-tool-write-diff-counts").textContent).toBe(
      "+2−0",
    );
  });
});

describe("read_file view", () => {
  it("shows the file as the tool returned it", () => {
    render(
      <ToolCallCard
        toolCall={call({
          toolName: "read_file",
          args: { file_path: "/tmp/a.csv" },
          result: { content: "1  id,name\n 2  1,Olivia" },
        })}
      />,
    );
    expect(screen.getByTestId("tc-tool-read-preview").textContent).toContain(
      "1,Olivia",
    );
  });
});

describe("search views", () => {
  it("renders glob matches as a list with a count", () => {
    render(
      <ToolCallCard
        toolCall={call({
          toolName: "glob",
          args: { pattern: "**/*.csv", path: "/repo" },
          result: { content: "['/repo/a.csv', '/repo/b.csv']" },
        })}
      />,
    );
    const panel = screen.getByTestId("tc-tool-search-matches");
    expect(within(panel).getByText("2 matches")).toBeTruthy();
    expect(panel.textContent).toContain("/repo/b.csv");
  });

  it("shows the pattern on the row", () => {
    render(
      <ToolCallCard
        toolCall={call({
          toolName: "glob",
          args: { pattern: "**/*.csv" },
          result: { content: "[]" },
        })}
      />,
    );
    expect(screen.getByTestId("tc-tool-card-subtitle").textContent).toBe(
      "**/*.csv",
    );
  });

  it("falls back to raw text for an output shape it does not recognise", () => {
    render(
      <ToolCallCard
        toolCall={call({
          toolName: "grep",
          args: { pattern: "TODO" },
          result: { content: "no matches found" },
        })}
      />,
    );
    expect(screen.getByTestId("tc-tool-search-raw").textContent).toContain(
      "no matches found",
    );
  });
});

describe("unregistered tools are untouched", () => {
  it("keeps the letter tile and the raw payload as the primary body", () => {
    render(
      <ToolCallCard
        toolCall={call({
          toolName: "get_issue",
          title: "Get Linear issue",
          args: { issue: "ENG-142" },
          result: { state: "In progress" },
        })}
      />,
    );
    expect(screen.queryByText("raw payload")).toBeNull();
    expect(screen.getByTestId("tc-chat-tool-call-1-args")).toBeTruthy();
    expect(screen.queryByTestId("tc-tool-card-subtitle")).toBeNull();
  });
});
