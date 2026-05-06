import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Message } from "./Message";
import { MessageParts } from "./MessageParts";
import type { ThreadMessageLike, ToolCallMessagePartProps } from "../types";

function ToolGroup({ children }: { children?: React.ReactNode }) {
  return <section data-testid="tool-group">{children}</section>;
}

// PR 3.2.4 — fleet children flow as raw parts via `nestedChildren`,
// not as React-rendered children. The mock surfaces them as testids so
// the existing assertion contract still holds.
type RawPart = {
  readonly toolCallId?: string;
  readonly args?: Record<string, unknown>;
  readonly argsText?: string;
};

function FleetTool(
  props: ToolCallMessagePartProps & {
    children?: React.ReactNode;
    nestedChildren?: readonly RawPart[];
  },
) {
  return (
    <section data-testid="fleet">
      <span>fleet</span>
      {(props.nestedChildren ?? []).map((child, index) => (
        <div key={child.toolCallId ?? index} data-testid="subagent">
          {child.argsText}
        </div>
      ))}
    </section>
  );
}

function SubagentTool(props: ToolCallMessagePartProps) {
  return <div data-testid="subagent">{props.argsText}</div>;
}

function FallbackTool(props: ToolCallMessagePartProps) {
  return <div data-testid="fallback">{props.toolName}</div>;
}

describe("MessageParts subagent grouping", () => {
  it("nests child subagents inside the fleet even when children arrive before the fleet part", () => {
    const message: ThreadMessageLike = {
      role: "assistant",
      content: [
        {
          type: "tool-call",
          toolCallId: "task_a",
          toolName: "run_subagent",
          args: { parent_fleet_id: "fleet_1" },
          argsText: "A",
        },
        {
          type: "tool-call",
          toolCallId: "task_b",
          toolName: "run_subagent",
          args: { parent_fleet_id: "fleet_1" },
          argsText: "B",
        },
        {
          type: "tool-call",
          toolCallId: "fleet_1",
          toolName: "run_subagent_fleet",
          args: { fleet_id: "fleet_1" },
          argsText: "fleet",
        },
      ],
    };

    render(
      <Message message={message}>
        <MessageParts
          components={{
            ToolGroup,
            tools: {
              Fallback: FallbackTool,
              by_name: {
                run_subagent: SubagentTool,
                run_subagent_fleet: FleetTool,
              },
            },
          }}
        />
      </Message>,
    );

    expect(screen.getByTestId("fleet")).toBeInTheDocument();
    expect(screen.getAllByTestId("subagent")).toHaveLength(2);
    expect(screen.queryByTestId("tool-group")).not.toBeInTheDocument();
  });
});
