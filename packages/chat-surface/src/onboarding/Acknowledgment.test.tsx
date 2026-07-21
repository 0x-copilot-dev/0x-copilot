// Acknowledgment — variant copy + the three jade-check lines (PRD-P3 §6.3).

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Acknowledgment, FIRST_RUN_ACK_TITLES } from "./Acknowledgment";

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
    expect(screen.getByTestId("first-run-ack").getAttribute("data-variant")).toBe(
      "queued",
    );
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
