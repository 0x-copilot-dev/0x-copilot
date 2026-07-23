// Acknowledgment — variant copy + the three jade-check lines (PRD-P3 §6.3).

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { Acknowledgment, FIRST_RUN_ACK_TITLES } from "./Acknowledgment";
import { FIRST_RUN_ACK_STALLED } from "./firstRunAckLines";

describe("<Acknowledgment>", () => {
  it("variant 'starting' renders 'Starting your first run' + the 3 lines", () => {
    render(
      <Acknowledgment
        variant="starting"
        modelLine="model — Claude Sonnet 4.5"
        toolsLine="tools — web search"
        privacyLine="key in your OS keychain"
      />,
    );
    expect(screen.getByTestId("first-run-ack-title").textContent).toBe(
      FIRST_RUN_ACK_TITLES.starting,
    );
    expect(screen.getByTestId("first-run-ack-title").textContent).toBe(
      "Starting your first run",
    );
    const lines = screen.getByTestId("first-run-ack").querySelectorAll(".ln");
    expect(lines).toHaveLength(3);
    expect(lines[0].textContent).toContain("model — Claude Sonnet 4.5");
    expect(lines[1].textContent).toContain("tools — web search");
    expect(lines[2].textContent).toContain("key in your OS keychain");
    // Jade check present on each line.
    expect(
      screen.getByTestId("first-run-ack").querySelectorAll(".ln__check"),
    ).toHaveLength(3);
  });

  it("variant 'queued' renders 'Queued — starts when the model lands'", () => {
    render(
      <Acknowledgment
        variant="queued"
        modelLine="model — Qwen 3 4B · downloading 41%"
        toolsLine="tools — none"
        privacyLine="nothing leaves this machine"
      />,
    );
    expect(screen.getByTestId("first-run-ack-title").textContent).toBe(
      "Queued — starts when the model lands",
    );
    expect(
      screen.getByTestId("first-run-ack").getAttribute("data-variant"),
    ).toBe("queued");
  });

  // PRD-P8 §7 — the third variant. `queued` promises "starts when the model
  // lands"; once it demonstrably is not landing, that title contradicts the
  // model line right beneath it ("· download paused at 40%").
  it("variant 'stalled' renders the honest held title + note + escape action", () => {
    const onAction = vi.fn();
    render(
      <Acknowledgment
        variant="stalled"
        modelLine="model — Qwen 3 4B · download paused at 40%"
        toolsLine="tools — none"
        privacyLine="nothing leaves this machine"
        note={FIRST_RUN_ACK_STALLED.note}
        actionLabel={FIRST_RUN_ACK_STALLED.action}
        onAction={onAction}
      />,
    );
    expect(screen.getByTestId("first-run-ack-title").textContent).toBe(
      "Held — the model isn't downloading",
    );
    // One home for the string: the map entry IS the copy constant.
    expect(FIRST_RUN_ACK_TITLES.stalled).toBe(FIRST_RUN_ACK_STALLED.title);
    expect(
      screen.getByTestId("first-run-ack").getAttribute("data-variant"),
    ).toBe("stalled");
    expect(screen.getByTestId("first-run-ack-note").textContent).toBe(
      "Restart Ollama or add a key — your prompt is saved.",
    );
    fireEvent.click(screen.getByTestId("first-run-ack-back"));
    expect(onAction).toHaveBeenCalledTimes(1);
  });

  // Mirrors `FirstRunLocalCardProps`' omitted-means-no-button rule: a host that
  // wired no escape must not get a control that silently does nothing.
  it("renders no action when the label or the handler is missing", () => {
    const { rerender } = render(
      <Acknowledgment
        variant="stalled"
        modelLine="model — Qwen 3 4B"
        toolsLine="tools — none"
        privacyLine="nothing leaves this machine"
        actionLabel={FIRST_RUN_ACK_STALLED.action}
      />,
    );
    expect(screen.queryByTestId("first-run-ack-back")).toBeNull();
    expect(screen.queryByTestId("first-run-ack-note")).toBeNull();

    rerender(
      <Acknowledgment
        variant="stalled"
        modelLine="model — Qwen 3 4B"
        toolsLine="tools — none"
        privacyLine="nothing leaves this machine"
        onAction={() => undefined}
      />,
    );
    expect(screen.queryByTestId("first-run-ack-back")).toBeNull();
  });

  it("renders an optional error line when supplied", () => {
    render(
      <Acknowledgment
        variant="queued"
        modelLine="model — Qwen 3 4B"
        toolsLine="tools — none"
        privacyLine="nothing leaves this machine"
        error="Couldn't start the run."
      />,
    );
    expect(screen.getByTestId("first-run-ack-error").textContent).toBe(
      "Couldn't start the run.",
    );
  });
});
