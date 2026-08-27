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

  it("says WHY there is no exit code, rather than drawing nothing", () => {
    // The other half of the assertion above, and the reason it is not enough on
    // its own: with no chip at all, a settled timeout renders exactly like a
    // command that is still running — same command row, same absent chip. "It
    // could not run" and "it ran and returned 1" are different facts and the
    // card has to be able to say both.
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
    const chip = screen.getByTestId("tc-tool-command-status");
    expect(chip.textContent).toBe("timed out");
    expect(chip.getAttribute("data-command-status")).toBe("timeout");
    expect(chip.getAttribute("data-exit")).toBe("none");
    // Neither of the two outcome hues: the process reported nothing, so there
    // is no result to colour. Amber here would put a policy refusal and a
    // user's own Stop in the same register as a failing test suite.
    expect(chip.getAttribute("style")).not.toContain("--color-warning");
    expect(chip.getAttribute("style")).not.toContain("--color-success");
    expect(chip.getAttribute("style")).not.toContain("--color-danger");
  });

  it.each([
    ["timeout", "timed out"],
    ["cancelled", "cancelled"],
    ["refused", "refused"],
    ["unavailable", "unavailable"],
  ])("labels the %s status", (status, label) => {
    render(
      <ToolCallCard
        toolCall={call({
          toolName: "run_command",
          args: { command: "deploy.sh" },
          result: commandResult({ status, output: "" }),
        })}
      />,
    );
    expect(screen.getByTestId("tc-tool-command-status").textContent).toBe(
      label,
    );
  });

  it("draws no status chip for a completed command, whatever its exit", () => {
    // "completed" is not an outcome the chip slot can carry — the exit code is.
    // A completed result that lost its `exit_code` in transit has nothing to
    // report, and "completed" printed where the code should be would read as a
    // success claim nobody made.
    render(
      <ToolCallCard
        toolCall={call({
          toolName: "run_command",
          args: { command: "true" },
          result: commandResult({ status: "completed", output: "" }),
        })}
      />,
    );
    expect(screen.queryByTestId("tc-tool-command-status")).toBeNull();
    expect(screen.queryByTestId("tc-tool-command-exit")).toBeNull();
  });

  it("shows an unrecognised status word rather than hiding it", () => {
    // `status` is a closed `Literal` server-side, so a word this build does not
    // know means the contract grew. A chip reading an unfamiliar word is a
    // smaller failure than a settled command that looks like it is still
    // running — and the record still parses, so the output survives too.
    render(
      <ToolCallCard
        toolCall={call({
          toolName: "run_command",
          args: { command: "deploy.sh" },
          result: commandResult({ status: "quarantined", output: "halted\n" }),
        })}
      />,
    );
    const chip = screen.getByTestId("tc-tool-command-status");
    expect(chip.textContent).toBe("quarantined");
    expect(chip.getAttribute("data-command-status")).toBe("quarantined");
    expect(screen.getByTestId("tc-tool-command-output").textContent).toContain(
      "halted",
    );
  });

  it("clips a status token so a long one cannot become the row", () => {
    render(
      <ToolCallCard
        toolCall={call({
          toolName: "run_command",
          args: { command: "deploy.sh" },
          result: commandResult({ status: "x".repeat(400), output: "" }),
        })}
      />,
    );
    expect(
      (screen.getByTestId("tc-tool-command-status").textContent as string)
        .length,
    ).toBeLessThanOrEqual(24);
  });

  it("keeps a running command chip-free", () => {
    // Nothing has settled, so neither chip may claim anything. This is the
    // state the timeout chip above exists to stop being confused with.
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
    expect(screen.queryByTestId("tc-tool-command-exit")).toBeNull();
    expect(screen.queryByTestId("tc-tool-command-status")).toBeNull();
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

  it("says the runtime truncated the bytes, and how many there were", () => {
    // §13/§14.3. This is read from the STRUCTURED fields, not from the notice
    // §13 has the runtime put inside `output` — see the next test for why that
    // distinction is the whole point.
    render(
      <ToolCallCard
        toolCall={call({
          toolName: "run_command",
          args: { command: "cat huge.log" },
          result: commandResult({
            output: "tail\n",
            exit_code: 0,
            truncated: true,
            output_total_bytes: 4_404_019,
            output_ref: "/scratch/run-1/cmd-2.out",
          }),
        })}
      />,
    );
    expect(screen.getByTestId("tc-tool-command-truncated").textContent).toBe(
      "Output truncated — this is the tail of 4.2 MB. The full output was saved.",
    );
  });

  it("survives the line clip that throws the runtime's own notice away", () => {
    // The reason the card re-states it. §13 has the runtime PREPEND its notice
    // to the output; this view keeps the LAST COMMAND_MAX_LINES — so on any
    // output long enough to be truncated, the in-string notice is the first
    // thing dropped. Read from the fields, the fact is not part of the clipped
    // text and cannot be clipped with it.
    const runtimeNotice =
      "...output truncated (kept the last 64 KiB of 4.2 MB)";
    render(
      <ToolCallCard
        toolCall={call({
          toolName: "run_command",
          args: { command: "cat huge.log" },
          result: commandResult({
            output: [
              runtimeNotice,
              "",
              ...Array.from({ length: 5000 }, (_, i) => `line ${i}`),
            ].join("\n"),
            exit_code: 0,
            truncated: true,
            output_total_bytes: 4_404_019,
          }),
        })}
      />,
    );
    // Gone from the text, exactly as described…
    expect(
      screen.getByTestId("tc-tool-command-output").textContent,
    ).not.toContain("output truncated (kept the last");
    // …and still on the card.
    expect(screen.getByTestId("tc-tool-command-truncated").textContent).toBe(
      "Output truncated — this is the tail of 4.2 MB.",
    );
    // Two cuts, two sentences: the runtime's bytes and the card's lines are
    // different facts and neither number describes the other.
    expect(screen.getByTestId("tc-tool-command-clipped").textContent).toBe(
      `${5002 - COMMAND_MAX_LINES} earlier lines not shown`,
    );
  });

  it("promises a saved file only when one was reported", () => {
    render(
      <ToolCallCard
        toolCall={call({
          toolName: "run_command",
          args: { command: "cat huge.log" },
          result: commandResult({
            output: "tail\n",
            exit_code: 0,
            truncated: true,
          }),
        })}
      />,
    );
    // No `output_total_bytes` and no `output_ref`: the card says the one thing
    // it knows and invents neither the size nor the file.
    expect(screen.getByTestId("tc-tool-command-truncated").textContent).toBe(
      "Output truncated — this is the tail.",
    );
  });

  it("says nothing about truncation when nothing was truncated", () => {
    render(
      <ToolCallCard
        toolCall={call({
          toolName: "run_command",
          args: { command: "ls" },
          result: commandResult({ output: "README.md\n", exit_code: 0 }),
        })}
      />,
    );
    expect(screen.queryByTestId("tc-tool-command-truncated")).toBeNull();
  });

  it("does not attach the settled truncation claim to the live tail", () => {
    // The frame is showing `outputPreview` because the result is unreadable, so
    // any volumetric sentence would be describing a string that is not on
    // screen.
    render(
      <ToolCallCard
        toolCall={call({
          toolName: "run_command",
          status: "running",
          args: { command: "cat huge.log" },
          outputPreview: "…",
        })}
      />,
    );
    expect(screen.queryByTestId("tc-tool-command-truncated")).toBeNull();
  });

  it("renders the runtime's exit note beside a non-zero exit", () => {
    // §4.3's `exit_note`. It is the sentence that stops "exit 1" reading as a
    // crash, and it is the runtime's own words rather than a table this client
    // would have to keep in sync with every CLI in the world.
    render(
      <ToolCallCard
        toolCall={call({
          toolName: "run_command",
          args: { command: "grep -r TODO ." },
          result: commandResult({
            output: "",
            exit_code: 1,
            exit_note: "grep exits 1 when there are no matches.",
          }),
        })}
      />,
    );
    // The command printed NOTHING, which is exactly the case the note explains
    // — and exactly the case that has no output frame for it to live in. So it
    // sits above, on the panel, not inside the output block.
    expect(screen.queryByTestId("tc-tool-command-output")).toBeNull();
    expect(screen.getByTestId("tc-tool-command-exit-note").textContent).toBe(
      "grep exits 1 when there are no matches.",
    );
    expect(screen.getByTestId("tc-tool-command-exit").textContent).toBe(
      "exit 1",
    );
  });

  it("omits the exit note when the runtime sent none", () => {
    render(
      <ToolCallCard
        toolCall={call({
          toolName: "run_command",
          args: { command: "pytest -q" },
          result: commandResult({ output: "1 failed\n", exit_code: 1 }),
        })}
      />,
    );
    expect(screen.queryByTestId("tc-tool-command-exit-note")).toBeNull();
  });

  it("makes no truncation or exit-note claim from an unrecognised result", () => {
    // The raw-text fallback carries the text and nothing else. A default of
    // `truncated: true` or a scavenged note would be this card asserting facts
    // about a payload whose shape it just admitted it does not know.
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
    expect(screen.queryByTestId("tc-tool-command-truncated")).toBeNull();
    expect(screen.queryByTestId("tc-tool-command-exit-note")).toBeNull();
    expect(screen.queryByTestId("tc-tool-command-status")).toBeNull();
  });

  it("bounds the output block's height and scrolls it in its own box", () => {
    // §16.5 asks for LAYOUT, not DOM presence — this repo has shipped a
    // disclosure clipped to 6% of its ink under a green suite. jsdom performs
    // no layout, so what is assertable is the resolved cascade: the block has a
    // ceiling and owns its own scroll, which is what keeps a 200-line command
    // from pushing the transcript around and its long lines from making the
    // page body scroll sideways (AC8.4).
    render(
      <ToolCallCard
        toolCall={call({
          toolName: "run_command",
          args: { command: "cat wide.log" },
          result: commandResult({
            output: `${"x".repeat(4000)}\n`.repeat(40),
            exit_code: 0,
          }),
        })}
      />,
    );
    const style = getComputedStyle(
      screen.getByTestId("tc-tool-command-output"),
    );
    expect(style.maxHeight).toBe("320px");
    expect(style.overflow).toBe("auto");
  });

  it("has no class names for a host stylesheet to re-declare", () => {
    // §14.3 wants the command view's CSS shipped inside this package with a
    // gate asserting no host sheet re-declares its class names. The view
    // answers that by construction rather than by a guard: it is styled
    // entirely with inline `CSSProperties`, and an inline style cannot be
    // shadowed by a host sheet at all. This is the assertion that keeps that
    // property true — the moment someone reaches for a `className` here, the
    // PR #459 failure mode (rules stranded in `apps/frontend/src/styles.css`,
    // surface unstyled on desktop) becomes reachable again and this fails.
    render(
      <ToolCallCard
        toolCall={call({
          toolName: "run_command",
          args: { command: "pytest -q" },
          result: commandResult({
            output: "F\n".repeat(400),
            exit_code: 1,
            truncated: true,
            output_total_bytes: 90_000,
            exit_note: "pytest exits 1 when a test fails.",
          }),
        })}
      />,
    );
    const block = screen.getByTestId("tc-tool-command");
    for (const node of [block, ...block.querySelectorAll("*")]) {
      expect(node.getAttribute("class")).toBeNull();
    }
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
