// SuggestionChips — the 3 verbatim starter chips (PRD-P3 §6.1).

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import {
  FIRST_RUN_SUGGESTIONS,
  SuggestionChips,
  type FirstRunSuggestion,
} from "./SuggestionChips";

describe("FIRST_RUN_SUGGESTIONS (verbatim data)", () => {
  it("has exactly 3 chips with verbatim titles", () => {
    expect(FIRST_RUN_SUGGESTIONS.map((s) => s.title)).toEqual([
      "Watch a wallet",
      "Draft a launch thread",
      "Explain a CSV",
    ]);
  });

  it("carries the verbatim prompts", () => {
    const byTitle = (t: string): FirstRunSuggestion =>
      FIRST_RUN_SUGGESTIONS.find((s) => s.title === t) as FirstRunSuggestion;
    expect(byTitle("Watch a wallet").prompt).toBe(
      "Watch 0x7f3C…a92C and alert me on any transfer over $500. Keep running in the background.",
    );
    expect(byTitle("Draft a launch thread").prompt).toBe(
      "Draft a 6-post launch thread… Ask me 3 questions first, then write it.",
    );
    expect(byTitle("Explain a CSV").prompt).toBe(
      "Explain this CSV… chart the top movers.",
    );
  });

  it("only the CSV chip carries attachmentId === 'airdrop-claims.csv'", () => {
    const withAttachment = FIRST_RUN_SUGGESTIONS.filter(
      (s) => s.attachmentId !== undefined,
    );
    expect(withAttachment).toHaveLength(1);
    expect(withAttachment[0].title).toBe("Explain a CSV");
    expect(withAttachment[0].attachmentId).toBe("airdrop-claims.csv");
  });
});

describe("<SuggestionChips>", () => {
  it("renders 3 chips and fires onPick with the full suggestion", () => {
    const onPick = vi.fn();
    render(<SuggestionChips onPick={onPick} />);
    const chips = screen.getByTestId("first-run-chips").querySelectorAll(".fr-chip");
    expect(chips).toHaveLength(3);

    fireEvent.click(screen.getByTestId("first-run-chip-explain-csv"));
    expect(onPick).toHaveBeenCalledTimes(1);
    expect(onPick.mock.calls[0][0]).toMatchObject({
      title: "Explain a CSV",
      prompt: "Explain this CSV… chart the top movers.",
      attachmentId: "airdrop-claims.csv",
    });
  });

  it("disables every chip when disabled", () => {
    render(<SuggestionChips onPick={() => undefined} disabled />);
    const chips = screen
      .getByTestId("first-run-chips")
      .querySelectorAll<HTMLButtonElement>(".fr-chip");
    for (const chip of chips) {
      expect(chip.disabled).toBe(true);
    }
  });
});
