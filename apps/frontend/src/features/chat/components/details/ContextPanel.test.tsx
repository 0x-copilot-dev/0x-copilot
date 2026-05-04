import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ContextPanel } from "./ContextPanel";

vi.mock("../../../../api/agentApi", () => ({
  getConversationContext: vi.fn(async () => ({
    model: {
      provider: "openai",
      name: "gpt-5.4-mini",
      context_window_tokens: 100_000,
    },
    current: {
      last_run_id: "r-latest",
      input_tokens: 1_000,
      output_tokens: 200,
      cached_input_tokens: 0,
      available_tokens: 99_000,
      headroom_pct: 99,
    },
    breakdown: {
      by_call: [
        {
          event_id: "call-a",
          model_name: "gpt-5.4-mini",
          input: 600,
          output: 100,
          cached_input: 0,
          task_id: null,
        },
      ],
      by_subagent: [
        { subagent_id: "sub-x", name: "sub-x", total: 800, call_count: 2 },
      ],
      compression_events: [],
    },
  })),
}));

const identity = { orgId: "org_a", userId: "user_1" };

describe("ContextPanel", () => {
  it("renders the server-supplied headroom verbatim", async () => {
    render(
      <ContextPanel
        conversationId="conv-1"
        identity={identity}
        onClose={() => undefined}
      />,
    );
    await waitFor(() => expect(screen.getByText(/99%/)).toBeInTheDocument());
    // Window size + gauge legend both render the count — at least one
    // node must contain it. Crucially, no element renders a re-derived
    // percent like "1,000 / 100,000" from the integers.
    expect(screen.getAllByText(/100,000 tok/).length).toBeGreaterThan(0);
  });

  it("invokes onClose when the close button is clicked", async () => {
    const onClose = vi.fn();
    render(
      <ContextPanel
        conversationId="conv-1"
        identity={identity}
        onClose={onClose}
      />,
    );
    await waitFor(() => screen.getByText(/99%/));
    fireEvent.click(
      screen.getByRole("button", { name: /close context panel/i }),
    );
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("renders the per-call and per-subagent breakdown tables", async () => {
    render(
      <ContextPanel
        conversationId="conv-1"
        identity={identity}
        onClose={() => undefined}
      />,
    );
    await waitFor(() =>
      expect(screen.getByText(/By model call/i)).toBeInTheDocument(),
    );
    expect(screen.getByText(/By subagent/i)).toBeInTheDocument();
    expect(screen.getByText(/sub-x/)).toBeInTheDocument();
  });
});
