import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { RuntimeEventEnvelope } from "@0x-copilot/api-types";

import { FocusPlan, projectFocusPlan } from "./FocusPlan";

function event(
  eventType: string,
  sequenceNo: number,
  payload: Record<string, unknown> = {},
  displayTitle?: string,
): RuntimeEventEnvelope {
  return {
    event_id: `event-${sequenceNo}`,
    run_id: "run-focus-plan",
    conversation_id: "conversation-focus-plan",
    sequence_no: sequenceNo,
    event_type: eventType,
    activity_kind: "event",
    payload,
    created_at: "2026-07-26T00:00:00.000Z",
    display_title: displayTitle,
  } as RuntimeEventEnvelope;
}

describe("projectFocusPlan", () => {
  it("shows an active understanding step while only reasoning is streaming", () => {
    expect(
      projectFocusPlan([
        event("reasoning_summary_delta", 1, { delta: "Checking the request…" }),
      ]),
    ).toEqual({
      steps: [
        {
          id: "understanding-request",
          label: "Understanding your request",
          state: "active",
        },
      ],
    });
  });

  it("uses the actual scheduled tools and advances their lifecycle", () => {
    expect(
      projectFocusPlan([
        event(
          "tool_call_started",
          1,
          { call_id: "call-linear", tool_name: "linear.issues.get" },
          "Read ENG-142",
        ),
        event(
          "tool_result",
          2,
          { call_id: "call-linear", tool_name: "linear.issues.get" },
          "Read ENG-142",
        ),
        event(
          "tool_call_started",
          3,
          { call_id: "call-web", tool_name: "web.search" },
          "Search the postmortem",
        ),
      ]),
    ).toEqual({
      steps: [
        { id: "call-linear", label: "Read ENG-142", state: "complete" },
        {
          id: "call-web",
          label: "Search the postmortem",
          state: "active",
        },
      ],
    });
  });

  it("prefers the agent-authored tool purpose over a lifecycle label", () => {
    expect(
      projectFocusPlan([
        event(
          "tool_call_started",
          1,
          { call_id: "call-web", tool_name: "web_search" },
          "Calling web_search",
        ),
        event(
          "tool_call_delta",
          2,
          {
            call_id: "call-web",
            tool_name: "web_search",
            args: {
              display_title: "PEP 8 docs",
              display_summary: "Find the official Python style guide",
            },
          },
          "web_search running",
        ),
        event(
          "tool_result",
          3,
          { call_id: "call-web", tool_name: "web_search" },
          "web_search completed",
        ),
      ]),
    ).toEqual({
      steps: [{ id: "call-web", label: "PEP 8 docs", state: "complete" }],
    });
  });
});

describe("FocusPlan", () => {
  it("renders a labelled plan with semantic step state", () => {
    render(
      <FocusPlan
        projection={{
          steps: [
            { id: "done", label: "Read the issue", state: "complete" },
            { id: "active", label: "Search the web", state: "active" },
          ],
        }}
      />,
    );

    expect(screen.getByTestId("focus-plan")).toHaveTextContent("Plan");
    expect(screen.getByText("Read the issue").parentElement).toHaveAttribute(
      "data-state",
      "complete",
    );
    expect(screen.getByText("Search the web").parentElement).toHaveAttribute(
      "data-state",
      "active",
    );
  });
});
