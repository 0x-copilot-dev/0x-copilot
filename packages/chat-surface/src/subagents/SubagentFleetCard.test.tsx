import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { SubagentFleetCard } from "./SubagentFleetCard";

describe("SubagentFleetCard", () => {
  it("starts terminal fleets compact, removes redundant copy, and supports pointer and keyboard disclosure", async () => {
    const user = userEvent.setup();
    render(
      <SubagentFleetCard
        fleetId="terminal"
        title="Parallel research"
        total={2}
        running={0}
        done={2}
        elapsed="0:05"
      >
        <p>Completed child details</p>
      </SubagentFleetCard>,
    );

    const toggle = screen.getByTestId("subagent-fleet-toggle-terminal");
    const details = toggle.parentElement?.querySelector(
      ".aui-fleet-card__details",
    ) as HTMLElement;
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(details.hidden).toBe(true);
    expect(screen.queryByText(/keep working while we draft/i)).toBeNull();
    expect(
      screen.queryByText(/keep chatting and they'll report back/i),
    ).toBeNull();

    await user.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(details.hidden).toBe(false);
    expect(screen.getByText("Completed child details")).toBeVisible();

    await user.keyboard(" ");
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    await user.keyboard("{Enter}");
    expect(toggle).toHaveAttribute("aria-expanded", "true");
  });

  it("folds a live fleet when it becomes terminal without overriding later user choices", async () => {
    const { rerender } = render(
      <SubagentFleetCard
        fleetId="live"
        title="Parallel research"
        total={2}
        running={2}
        done={0}
      >
        <p>Live child details</p>
      </SubagentFleetCard>,
    );
    const toggle = screen.getByTestId("subagent-fleet-toggle-live");
    expect(toggle).toHaveAttribute("aria-expanded", "true");

    rerender(
      <SubagentFleetCard
        fleetId="live"
        title="Parallel research"
        total={2}
        running={0}
        done={2}
      >
        <p>Live child details</p>
      </SubagentFleetCard>,
    );
    await waitFor(() =>
      expect(toggle).toHaveAttribute("aria-expanded", "false"),
    );

    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    rerender(
      <SubagentFleetCard
        fleetId="live"
        title="Parallel research"
        total={2}
        running={0}
        done={2}
      >
        <p>Live child details</p>
      </SubagentFleetCard>,
    );
    expect(toggle).toHaveAttribute("aria-expanded", "true");
  });
});
