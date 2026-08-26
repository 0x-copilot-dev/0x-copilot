import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { ToolCallEntry } from "./eventProjector";
import { ToolCallCard } from "./ToolCallCard";

const detailedToolCall: ToolCallEntry = {
  args: { issue: "ENG-142" },
  createdAtMs: 0,
  id: "call-linear-1",
  result: { identifier: "ENG-142", state: "In progress" },
  sequenceNo: 1,
  runId: null,
  status: "complete",
  title: "Get Linear issue",
  toolName: "get_issue",
};

describe("ToolCallCard", () => {
  it("uses the native disclosure as the compact visual tool header", async () => {
    const { container } = render(<ToolCallCard toolCall={detailedToolCall} />);

    const summary = container.querySelector("summary");
    expect(summary).not.toBeNull();
    if (summary === null) {
      throw new Error("Expected a native details summary");
    }
    expect(summary).toHaveStyle({
      display: "flex",
      gap: "9px",
      padding: "9px 11px",
    });
    expect(summary.firstElementChild).not.toBeNull();
    expect(summary.firstElementChild).toHaveStyle({ display: "contents" });
    expect(summary).toHaveAttribute(
      "aria-label",
      "Show details for Get Linear issue",
    );
    expect(within(summary).getByText("Get Linear issue")).toBeInTheDocument();
    expect(within(summary).queryByText("get_issue")).not.toBeInTheDocument();
    expect(
      within(summary).getByTestId("tc-chat-tool-call-linear-1-status"),
    ).toHaveTextContent("✓Done");
    expect(summary.parentElement).toHaveProperty("open", false);

    fireEvent.click(summary);
    expect(summary.parentElement).toHaveProperty("open", true);
    await waitFor(() =>
      expect(summary).toHaveAttribute(
        "aria-label",
        "Hide details for Get Linear issue",
      ),
    );
  });

  it("shows a failed plain-language status and safe error while collapsed", () => {
    const { container } = render(
      <ToolCallCard
        toolCall={{
          createdAtMs: 0,
          errorMessage: "The Linear server could not be reached.",
          id: "call-linear-failed",
          result: {
            error: {
              code: "connection_failed",
              safe_message: "The Linear server could not be reached.",
            },
          },
          sequenceNo: 1,
          runId: null,
          status: "error",
          title: "Load Linear tools",
          toolName: "load_mcp_server",
        }}
      />,
    );

    const details = container.querySelector("details");
    const summary = container.querySelector("summary");
    if (details === null || summary === null) {
      throw new Error("Expected a failed tool disclosure");
    }
    expect(details).toHaveProperty("open", false);
    expect(details).toHaveAttribute("data-tool-status", "error");
    expect(
      screen.getByTestId("tc-chat-tool-call-linear-failed-status"),
    ).toHaveTextContent("!Failed");
    expect(
      within(summary).getByText("The Linear server could not be reached."),
    ).toBeInTheDocument();
    expect(within(summary).queryByText("✓")).toBeNull();
  });
});

describe("ToolCallCard — parked on an approval", () => {
  const running: ToolCallEntry = {
    createdAtMs: 0,
    id: "call-ls-1",
    sequenceNo: 1,
    runId: null,
    status: "running",
    title: "Calling ls",
    toolName: "ls",
  };

  it("reads Waiting with a still glyph instead of Running with a spinner", () => {
    // The lie this fixes: a parked run has no motion, but the card asserted it.
    const { container } = render(<ToolCallCard toolCall={running} parked />);

    expect(screen.getByTestId("tc-tool-card-waiting")).toBeInTheDocument();
    expect(container.querySelector(".tc-tool-card__spinner")).toBeNull();
    expect(
      screen.getByTestId("tc-chat-tool-call-ls-1-status"),
    ).toHaveTextContent("Waiting");
  });

  it("spins again once the approval resolves", () => {
    const { container, rerender } = render(
      <ToolCallCard toolCall={running} parked />,
    );
    expect(screen.getByTestId("tc-tool-card-waiting")).toBeInTheDocument();

    rerender(<ToolCallCard toolCall={running} />);
    expect(screen.queryByTestId("tc-tool-card-waiting")).toBeNull();
    expect(container.querySelector(".tc-tool-card__spinner")).not.toBeNull();
    expect(
      screen.getByTestId("tc-chat-tool-call-ls-1-status"),
    ).toHaveTextContent("Running");
  });

  it("leaves a settled call alone — only a running one can be parked", () => {
    // A completed call is history; the run being parked says nothing about it.
    render(<ToolCallCard toolCall={detailedToolCall} parked />);

    expect(screen.queryByTestId("tc-tool-card-waiting")).toBeNull();
    expect(
      screen.getByTestId("tc-chat-tool-call-linear-1-status"),
    ).toHaveTextContent("Done");
  });
});

describe("ToolCallCard — refused vs waiting on a decision", () => {
  // Two `write_file` cards from two live runs, one path apart. Before this they
  // were the same stopped card and their remedies are opposite: run A wants a
  // folder attached, run B wants one click.
  const refused: ToolCallEntry = {
    createdAtMs: 0,
    id: "call-w-1",
    sequenceNo: 1,
    runId: null,
    status: "error",
    title: "Write file",
    toolName: "write_file",
    errorMessage: "permission denied for write on /random.csv",
    blockedBy: { kind: "permission", lane: "filesystem" },
  };

  const gated: ToolCallEntry = {
    createdAtMs: 0,
    id: "call-w-2",
    sequenceNo: 2,
    runId: null,
    status: "running",
    title: "Write file",
    toolName: "write_file",
    blockedBy: {
      kind: "decision",
      approvalId: "int-7:0",
      ask: "Allow writing to /drafts/random.csv?",
    },
  };

  function summaryOf(container: HTMLElement): HTMLElement {
    const summary = container.querySelector("summary");
    if (summary === null) {
      throw new Error("Expected a disclosure summary");
    }
    return summary;
  }

  it("keeps a refusal's REAL reason and adds the authority that was withheld", () => {
    // The regression guard for the error-copy work: the coarse sentence is the
    // fact and must survive verbatim. What is added is the door, not a
    // replacement for the wall.
    const { container } = render(<ToolCallCard toolCall={refused} />);
    const summary = summaryOf(container);

    expect(
      within(summary).getByText("permission denied for write on /random.csv"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("tc-tool-card-remedy-call-w-1"),
    ).toHaveTextContent("Attach that folder to this chat to allow it.");
    expect(
      screen.getByTestId("tc-chat-tool-call-w-1-status"),
    ).toHaveTextContent("Failed");
  });

  it("tells a gated call it is waiting on the READER, not on the tool", () => {
    const { container } = render(<ToolCallCard toolCall={gated} />);
    const summary = summaryOf(container);

    expect(
      screen.getByTestId("tc-chat-tool-call-w-2-status"),
    ).toHaveTextContent("Needs you");
    expect(
      within(summary).getByText("Allow writing to /drafts/random.csv?"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("tc-tool-card-remedy-call-w-2"),
    ).toHaveTextContent(
      "Paused — the run continues once you approve or decline it.",
    );
    // Still, not spinning: nothing is executing while the graph is interrupted.
    expect(screen.getByTestId("tc-tool-card-waiting")).toBeInTheDocument();
    expect(container.querySelector(".tc-tool-card__spinner")).toBeNull();
  });

  it("stamps which stopped state it is, so a journey need not read copy", () => {
    const { container: a } = render(<ToolCallCard toolCall={refused} />);
    expect(a.querySelector("[data-tool-blocked]")).toHaveAttribute(
      "data-tool-blocked",
      "permission",
    );

    const { container: b } = render(<ToolCallCard toolCall={gated} />);
    expect(b.querySelector("[data-tool-blocked]")).toHaveAttribute(
      "data-tool-blocked",
      "decision",
    );
  });

  it("announces the decision in full where the rail can only fit two words", () => {
    render(<ToolCallCard toolCall={gated} />);

    expect(screen.getByTestId("tc-chat-tool-call-w-2-status")).toHaveAttribute(
      "aria-label",
      "Waiting for your approval",
    );
  });

  it("says the run needs the reader WITHOUT the host threading `parked` down", () => {
    // `parked` is run-wide by construction — it says a decision is pending
    // SOMEWHERE. The gated call knows on its own account, which is what makes
    // the two distinguishable at all.
    render(<ToolCallCard toolCall={gated} />);

    expect(screen.getByTestId("tc-tool-card-waiting")).toBeInTheDocument();
  });

  it("does not promote an ordinary parked card to `Needs you`", () => {
    // The other half of that: a call merely stalled behind someone else's
    // decision reads `Waiting`. Collapsing the two would send the reader
    // hunting for an approval that is not about this call.
    render(
      <ToolCallCard
        toolCall={{ ...gated, id: "call-w-3", blockedBy: undefined }}
        parked
      />,
    );

    expect(
      screen.getByTestId("tc-chat-tool-call-w-3-status"),
    ).toHaveTextContent("Waiting");
    expect(screen.queryByTestId("tc-tool-card-remedy-call-w-3")).toBeNull();
  });

  it("puts the remedy in the COLLAPSED header, not behind the disclosure", () => {
    // A reader who must open a disclosure to learn the run is waiting on them
    // has already concluded it is broken. Proven on a `read_file` gate because
    // that card really does start closed — `write_file`'s view sets
    // `defaultOpen`, so asserting it there would prove nothing about the
    // collapsed state. `read_file` is not a contrived case: rule 4 in
    // `host_filesystem.py` interrupts every read outside a granted root.
    const { container } = render(
      <ToolCallCard
        toolCall={{
          ...gated,
          id: "call-r-1",
          title: "Read file",
          toolName: "read_file",
          blockedBy: {
            kind: "decision",
            approvalId: "int-8:0",
            ask: "Allow reading /Users/me/notes?",
          },
        }}
      />,
    );

    expect(container.querySelector("details")).toHaveProperty("open", false);
    expect(
      within(summaryOf(container)).getByTestId("tc-tool-card-remedy-call-r-1"),
    ).toBeInTheDocument();
  });

  it("adds NO approve control — this package has exactly one ask card", () => {
    // The safety property, asserted as the absence of a SURFACE and not of a
    // name: `tc-write-gate` is `TcWriteGateRow`'s own root, stamped for every
    // ask and every id, so a rename of the id-scoped decision testids cannot
    // silence this. A second approve here would put an irreversible write one
    // click from a surface the safety journeys do not police.
    const { container } = render(<ToolCallCard toolCall={gated} />);

    expect(container.querySelectorAll("button")).toHaveLength(0);
    expect(
      container.querySelector("[data-testid^='tc-write-gate']"),
    ).toBeNull();
    expect(
      container.querySelector("[data-testid^='tc-chat-approval-approve-']"),
    ).toBeNull();
    expect(
      container.querySelector(
        "[data-testid^='tc-chat-approval-body-approve-']",
      ),
    ).toBeNull();
  });

  it("names the pending approval in the body, so eye-joining a card to an ask works", () => {
    const { container } = render(<ToolCallCard toolCall={gated} />);
    fireEvent.click(summaryOf(container));

    expect(
      screen.getByTestId("tc-tool-card-decision-call-w-2"),
    ).toHaveTextContent("int-7:0");
  });

  it("sends a refused CONNECTOR call to Tools, not to the folder picker", () => {
    render(
      <ToolCallCard
        toolCall={{
          ...refused,
          id: "call-mcp-1",
          toolName: "call_mcp_tool",
          errorMessage: "permission denied",
          provenance: { source: "mcp", serverName: "Linear" },
          blockedBy: { kind: "permission", lane: "connector" },
        }}
      />,
    );

    expect(
      screen.getByTestId("tc-tool-card-remedy-call-mcp-1"),
    ).toHaveTextContent("Check this connector's access under Tools.");
  });

  it("stays silent when the wire gave the gate no question of its own", () => {
    render(
      <ToolCallCard
        toolCall={{
          ...gated,
          id: "call-w-4",
          blockedBy: { kind: "decision", approvalId: "int-7:0", ask: null },
        }}
      />,
    );

    expect(
      screen.getByText("This step is waiting for your decision."),
    ).toBeInTheDocument();
  });
});
