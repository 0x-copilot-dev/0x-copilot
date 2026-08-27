import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { TOOL_OUTPUT_PREVIEW_CAP, type ToolCallEntry } from "./eventProjector";
import { ToolCallCard } from "./ToolCallCard";
import {
  basename,
  COMMAND_MAX_LINES,
  parsePathList,
  toolViewFor,
} from "./toolViews";

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
    file_path: "/Users/dev/projects/kscope-benchmarks/random.csv",
    old_string: "5,orange,lynx,29,false",
    new_string: "5,orange,lynx,30,false",
  },
  result: {
    content:
      "Successfully replaced 1 instance(s) of the string in '/Users/dev/projects/kscope-benchmarks/random.csv'",
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

  it("opens itself, so the diff is visible without a click", () => {
    // A live run showed the diff sitting inside a COLLAPSED <details>: present
    // in the DOM, found by querySelectorAll, and invisible to the reader. DOM
    // presence is not visibility, so this asserts the disclosure's open state.
    const { container } = render(<ToolCallCard toolCall={EDIT_CALL} />);
    const details = container.querySelector("details");
    expect(details).not.toBeNull();
    expect((details as HTMLDetailsElement).open).toBe(true);
  });

  it("leaves an unregistered tool's card closed", () => {
    const { container } = render(
      <ToolCallCard
        toolCall={call({ toolName: "get_issue", args: { issue: "ENG-1" } })}
      />,
    );
    expect(
      (container.querySelector("details") as HTMLDetailsElement).open,
    ).toBe(false);
  });

  it("marks a FAILED edit as not applied and drops the change colour", () => {
    // The diff is built from the call's ARGUMENTS, so a refused edit still has
    // a well-formed hunk. Rendering it unqualified would claim a change that
    // never happened — which is exactly what a live run produced when both
    // file tools were refused for want of a workspace grant.
    render(
      <ToolCallCard
        toolCall={{ ...EDIT_CALL, status: "error", errorMessage: "refused" }}
      />,
    );
    const diff = screen.getByTestId("tc-tool-edit-diff");
    expect(diff.getAttribute("data-applied")).toBe("false");
    expect(
      within(diff).getByTestId("tc-tool-edit-diff-not-applied").textContent,
    ).toBe("not applied");
  });

  it("marks a completed edit as applied", () => {
    render(<ToolCallCard toolCall={EDIT_CALL} />);
    const diff = screen.getByTestId("tc-tool-edit-diff");
    expect(diff.getAttribute("data-applied")).toBe("true");
    expect(screen.queryByTestId("tc-tool-edit-diff-not-applied")).toBeNull();
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

  it("renders two unregistered tools byte-identically, name aside", () => {
    // The registry is an EXACT-name lookup, and this is the property that says
    // so: `run_commandx` is not `run_command`, so adding the command entry must
    // leave it rendering exactly as an unrelated tool does. A prefix or fuzzy
    // match would fail here and nowhere else.
    //
    // Both names start with `r` on purpose — the generic tile is the tool's
    // initial, so a different letter would fail this for a reason that has
    // nothing to do with the registry.
    const payload = {
      args: { issue: "ENG-142" },
      result: { state: "In progress" },
    };
    const first = render(
      <ToolCallCard toolCall={call({ toolName: "run_report", ...payload })} />,
    ).container.innerHTML.replaceAll("run_report", "TOOL");
    const second = render(
      <ToolCallCard
        toolCall={call({ toolName: "run_commandx", ...payload })}
      />,
    ).container.innerHTML.replaceAll("run_commandx", "TOOL");
    expect(second).toBe(first);
  });

  it("does not sniff arguments — only the tool name selects a view", () => {
    // A future "improvement" that reached for `args.command` instead of the
    // name would hand an arbitrary connector tool the command block.
    render(
      <ToolCallCard
        toolCall={call({
          toolName: "get_issue",
          args: { command: "rm -rf /" },
          outputPreview: "deleting…",
        })}
      />,
    );
    expect(screen.queryByTestId("tc-tool-command")).toBeNull();
  });
});

/* ── run_command (PRD-shell-execution §14.2 / §14.3) ────────────────────────
 *
 * SHIPS DARK. No `run_command` tool is registered in the runtime, so nothing
 * reaches this view in a real run today; these tests drive the projection
 * shape the contract specifies (§4.2 args, §4.3 result) so the seam is pinned
 * before the producer exists.
 */

/** The §4.3 result, as the runtime delivers a tool return value: a JSON
 *  document inside `output.content`. */
function commandResult(over: Record<string, unknown>) {
  return {
    content: JSON.stringify({
      status: "completed",
      output: "",
      duration_ms: 12,
      workspace: "my-project",
      ...over,
    }),
  };
}

describe("run_command view", () => {
  it("is registered, and its card stays closed by default", () => {
    const view = toolViewFor("run_command");
    expect(view.kind).toBe("command");
    // §14.2: command output is the case the `defaultOpen` argument runs
    // hardest against. OQ-6 (open on a non-zero exit) is deliberately not
    // taken here — it is not expressible on a constant.
    expect(view.defaultOpen).toBe(false);
  });

  it("renders the command, its output and the exit code", () => {
    render(
      <ToolCallCard
        toolCall={call({
          toolName: "run_command",
          args: { command: "pytest -q", workspace: "my-project" },
          result: commandResult({ output: "12 passed\n", exit_code: 0 }),
        })}
      />,
    );
    expect(screen.getByTestId("tc-tool-command-text").textContent).toBe(
      "pytest -q",
    );
    expect(screen.getByTestId("tc-tool-command-output").textContent).toContain(
      "12 passed",
    );
    expect(screen.getByTestId("tc-tool-command-exit").textContent).toBe(
      "exit 0",
    );
  });

  it("reads the result fields when they arrive unwrapped", () => {
    // The other arm: a runtime that lands `RunCommandResult` on `output`
    // itself rather than as JSON inside `output.content`. Both are plausible
    // and neither is captured yet, so both are read rather than assumed.
    render(
      <ToolCallCard
        toolCall={call({
          toolName: "run_command",
          args: { command: "ls" },
          result: {
            status: "completed",
            output: "README.md\n",
            exit_code: 0,
            workspace: "my-project",
          },
        })}
      />,
    );
    expect(screen.getByTestId("tc-tool-command-output").textContent).toContain(
      "README.md",
    );
    expect(
      screen.getByTestId("tc-tool-command-exit").getAttribute("data-exit"),
    ).toBe("ok");
  });

  it("shows no exit chip for a status that carries no exit code", () => {
    // §4.3: `exit_code` is `None` for every status other than `completed`. A
    // timeout has no code, and inventing 0 or -1 would be a claim we cannot
    // make about a process nobody reaped.
    render(
      <ToolCallCard
        toolCall={call({
          toolName: "run_command",
          status: "error",
          args: { command: "sleep 900" },
          result: commandResult({ status: "timeout", output: "" }),
        })}
      />,
    );
    expect(screen.getByTestId("tc-tool-command-text").textContent).toBe(
      "sleep 900",
    );
    expect(screen.queryByTestId("tc-tool-command-exit")).toBeNull();
  });

  it("colours a non-zero exit as a warning, not as a failure", () => {
    // `grep` exits 1 on no matches. Painting that in the destructive hue would
    // make the transcript's one "something is wrong" colour mean "a command
    // ran normally" (§14.3).
    render(
      <ToolCallCard
        toolCall={call({
          toolName: "run_command",
          args: { command: "grep -r TODO ." },
          result: commandResult({ output: "", exit_code: 1 }),
        })}
      />,
    );
    const chip = screen.getByTestId("tc-tool-command-exit");
    expect(chip.textContent).toBe("exit 1");
    // The stamp, not the pixel: jsdom performs no layout and resolves no custom
    // property, so the colour itself is unassertable here. What IS assertable
    // is that the two states are distinguishable without reading copy.
    expect(chip.getAttribute("data-exit")).toBe("nonzero");
    expect(chip.getAttribute("style")).toContain("--color-warning");
    expect(chip.getAttribute("style")).not.toContain("--color-danger");
  });

  it("shows the live tail while the call is still running", () => {
    render(
      <ToolCallCard
        toolCall={call({
          toolName: "run_command",
          status: "running",
          args: { command: "npm test" },
          outputPreview: "> vitest run\n",
        })}
      />,
    );
    expect(screen.getByTestId("tc-tool-command-output").textContent).toContain(
      "> vitest run",
    );
    // Nothing has settled, so there is no exit code to claim.
    expect(screen.queryByTestId("tc-tool-command-exit")).toBeNull();
  });

  it("prefers the settled result over the stale live tail", () => {
    render(
      <ToolCallCard
        toolCall={call({
          toolName: "run_command",
          args: { command: "npm test" },
          outputPreview: "running 4 of 9…",
          result: commandResult({ output: "9 passed\n", exit_code: 0 }),
        })}
      />,
    );
    const output = screen.getByTestId("tc-tool-command-output").textContent;
    expect(output).toContain("9 passed");
    expect(output).not.toContain("running 4 of 9");
  });

  it("falls back to the live tail when the result carried no output", () => {
    // A run cancelled mid-command. The tail is the only record of what ran.
    render(
      <ToolCallCard
        toolCall={call({
          toolName: "run_command",
          status: "error",
          args: { command: "npm test" },
          outputPreview: "running 4 of 9…",
        })}
      />,
    );
    expect(screen.getByTestId("tc-tool-command-output").textContent).toContain(
      "running 4 of 9",
    );
  });

  it("keeps only the last COMMAND_MAX_LINES and says how many it dropped", () => {
    // The transcript is not a terminal: a command that prints 5000 lines must
    // not mount 5000 line boxes.
    const lines = Array.from({ length: 5000 }, (_, i) => `line ${i}`);
    render(
      <ToolCallCard
        toolCall={call({
          toolName: "run_command",
          args: { command: "yes | head -5000" },
          result: commandResult({
            output: lines.join("\n"),
            exit_code: 0,
          }),
        })}
      />,
    );
    const output = screen.getByTestId("tc-tool-command-output")
      .textContent as string;
    expect(output.split("\n")).toHaveLength(COMMAND_MAX_LINES);
    // TAIL-kept, matching §13's rule for the bytes: the error is at the end.
    expect(output).toContain("line 4999");
    expect(output).not.toContain("line 4000");
    expect(screen.getByTestId("tc-tool-command-clipped").textContent).toBe(
      `${5000 - COMMAND_MAX_LINES} earlier lines not shown`,
    );
  });

  it("mounts one node for the output, whatever the line count", () => {
    // The line cap bounds LAYOUT; this bounds the DOM. One <pre> holding text,
    // never one element per line — `TcChat`'s render budget folds whole cards
    // and cannot help inside the newest one.
    render(
      <ToolCallCard
        toolCall={call({
          toolName: "run_command",
          args: { command: "cat big.log" },
          result: commandResult({ output: "a\n".repeat(400), exit_code: 0 }),
        })}
      />,
    );
    const pre = screen.getByTestId("tc-tool-command-output");
    expect(pre.tagName.toLowerCase()).toBe("pre");
    expect(pre.children).toHaveLength(0);
  });

  it("does not clip a preview that already arrived at the projector's cap", () => {
    // The two caps compose rather than fight: the projector bounds BYTES, the
    // view bounds LINES, and one long line is under both.
    const oneLongLine = "x".repeat(TOOL_OUTPUT_PREVIEW_CAP);
    render(
      <ToolCallCard
        toolCall={call({
          toolName: "run_command",
          status: "running",
          args: { command: "cat one-line.log" },
          outputPreview: oneLongLine,
        })}
      />,
    );
    expect(
      screen.getByTestId("tc-tool-command-output").textContent,
    ).toHaveLength(TOOL_OUTPUT_PREVIEW_CAP);
    expect(screen.queryByTestId("tc-tool-command-clipped")).toBeNull();
  });

  it("renders no output frame for a command that printed nothing", () => {
    render(
      <ToolCallCard
        toolCall={call({
          toolName: "run_command",
          args: { command: "touch out.txt" },
          result: commandResult({ output: "", exit_code: 0 }),
        })}
      />,
    );
    expect(screen.getByTestId("tc-tool-command-text").textContent).toBe(
      "touch out.txt",
    );
    // An empty bordered frame claims there is something to look at.
    expect(screen.queryByTestId("tc-tool-command-output")).toBeNull();
  });

  it("renders nothing at all while the arguments are still streaming", () => {
    // Same discipline as the edit view's half-diff: with no command and no
    // output there is nothing to say, so the card falls back to the generic
    // rows rather than drawing an empty block.
    render(
      <ToolCallCard toolCall={call({ toolName: "run_command", args: {} })} />,
    );
    expect(screen.queryByTestId("tc-tool-command")).toBeNull();
    expect(screen.getByTestId("tc-chat-tool-call-1-args")).toBeTruthy();
  });

  it("shows a result shape it does not recognise verbatim", () => {
    // A refusal sentence, an older event, a prose-answering tool. Showing the
    // string is honest about an unknown shape; showing nothing is not.
    render(
      <ToolCallCard
        toolCall={call({
          toolName: "run_command",
          status: "error",
          args: { command: "deploy.sh" },
          result: { content: "Command refused: not on the allowlist" },
        })}
      />,
    );
    expect(screen.getByTestId("tc-tool-command-output").textContent).toContain(
      "not on the allowlist",
    );
    expect(screen.queryByTestId("tc-tool-command-exit")).toBeNull();
  });

  it("does not read a command's own JSON stdout as the result envelope", () => {
    // `curl … | jq` returns JSON. Requiring BOTH `status` and `output` — the
    // two fields `RunCommandResult` always carries — is what keeps that from
    // being rendered as its own metadata.
    render(
      <ToolCallCard
        toolCall={call({
          toolName: "run_command",
          args: { command: "cat package.json" },
          result: { content: '{"name": "app", "output": "not ours"}' },
        })}
      />,
    );
    const output = screen.getByTestId("tc-tool-command-output").textContent;
    expect(output).toContain('"name": "app"');
    expect(output).not.toBe("not ours");
  });

  it("collapses a multi-line command on the one-line row only", () => {
    const multi = 'printf "a\\n" \\\n  && ls -la';
    render(
      <ToolCallCard
        toolCall={call({ toolName: "run_command", args: { command: multi } })}
      />,
    );
    // The row is one line, and the collapse happens in the DATA — a newline
    // surviving here reaches copy and the accessible name even though the
    // pixels look right.
    expect(screen.getByTestId("tc-tool-card-subtitle").textContent).toBe(
      'printf "a\\n" \\ && ls -la',
    );
    // The block below keeps it exactly as it was written.
    expect(screen.getByTestId("tc-tool-command-text").textContent).toBe(multi);
  });
});
