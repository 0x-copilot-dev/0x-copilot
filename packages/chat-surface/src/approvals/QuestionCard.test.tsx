// The question card. What matters here is that it behaves like a question and
// not like an approval: single-select answers on click, an empty answer never
// resolves anything, and a question with no options is still answerable.

import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import { QuestionCard } from "./QuestionCard";
import { composeAnswer, isAnswerable, parseQuestion } from "./question";
import type { QuestionSpec } from "./question";

function spec(overrides: Partial<QuestionSpec> = {}): QuestionSpec {
  return {
    header: "Quick question",
    question: "Which treasury should the Launch Week payouts come from?",
    hint: null,
    options: [
      { label: "Ops Safe", description: null, recommended: true },
      { label: "Growth Safe", description: null, recommended: false },
      { label: "Split evenly", description: null, recommended: false },
    ],
    multiSelect: false,
    allowFreeText: true,
    ...overrides,
  };
}

describe("QuestionCard — single select", () => {
  it("answers on click, with no confirm step", () => {
    // One choice is one decision; a confirm button would make it two.
    const onAnswer = vi.fn();
    render(<QuestionCard spec={spec()} onAnswer={onAnswer} />);
    fireEvent.click(screen.getByTestId("qc-option-Ops Safe"));
    expect(onAnswer).toHaveBeenCalledWith({
      selected: ["Ops Safe"],
      freeText: null,
      answer: "Ops Safe",
    });
  });

  it("marks the recommended option without pre-selecting it", () => {
    render(<QuestionCard spec={spec()} onAnswer={vi.fn()} />);
    expect(screen.getByText("Recommended")).toBeInTheDocument();
    // Marked, not chosen — the chip carries no on-state until it's clicked.
    expect(screen.getByTestId("qc-option-Ops Safe")).not.toHaveAttribute(
      "data-on",
    );
  });

  it("sends a typed answer through the same callback", () => {
    const onAnswer = vi.fn();
    render(<QuestionCard spec={spec()} onAnswer={onAnswer} />);
    fireEvent.change(screen.getByTestId("qc-free-text"), {
      target: { value: "  The multisig, not either Safe  " },
    });
    fireEvent.click(screen.getByTestId("qc-send"));
    expect(onAnswer).toHaveBeenCalledWith({
      selected: [],
      freeText: "The multisig, not either Safe",
      answer: "The multisig, not either Safe",
    });
  });

  it("cannot send an empty answer", () => {
    const onAnswer = vi.fn();
    render(<QuestionCard spec={spec()} onAnswer={onAnswer} />);
    expect(screen.getByTestId("qc-send")).toBeDisabled();
    fireEvent.click(screen.getByTestId("qc-send"));
    expect(onAnswer).not.toHaveBeenCalled();
  });

  it("offers free text even when the server disallowed it and gave no options", () => {
    // Otherwise the run sits blocked behind a card with no controls at all.
    render(
      <QuestionCard
        spec={spec({ options: [], allowFreeText: false })}
        onAnswer={vi.fn()}
      />,
    );
    expect(screen.getByTestId("qc-free-text")).toBeInTheDocument();
  });

  it("states the pause, because the pause is the cost", () => {
    render(<QuestionCard spec={spec()} onAnswer={vi.fn()} />);
    expect(screen.getByText(/The run is paused here/)).toBeInTheDocument();
  });

  it("collapses to the answer once resolved", () => {
    render(
      <QuestionCard
        spec={spec()}
        resolved
        answer="Ops Safe"
        onAnswer={vi.fn()}
      />,
    );
    expect(screen.getByTestId("qc-answer")).toHaveTextContent("Ops Safe");
    expect(screen.queryByTestId("qc-option-Ops Safe")).toBeNull();
    // Nothing is still paused, so the footer goes with it.
    expect(screen.queryByText(/The run is paused here/)).toBeNull();
  });
});

describe("QuestionCard — multi select", () => {
  const MULTI = spec({
    header: "Pick the channels",
    question: "Where should the launch thread be cross-posted?",
    options: [
      { label: "X", description: null, recommended: false },
      { label: "Discord", description: null, recommended: false },
      { label: "Farcaster", description: null, recommended: false },
    ],
    multiSelect: true,
  });

  it("toggles without answering, then confirms once", () => {
    const onAnswer = vi.fn();
    render(<QuestionCard spec={MULTI} onAnswer={onAnswer} />);
    fireEvent.click(screen.getByTestId("qc-option-X"));
    fireEvent.click(screen.getByTestId("qc-option-Discord"));
    // Selecting is not answering here — that's the whole difference.
    expect(onAnswer).not.toHaveBeenCalled();
    expect(screen.getByTestId("qc-confirm")).toHaveTextContent("Use these 2");
    fireEvent.click(screen.getByTestId("qc-confirm"));
    expect(onAnswer).toHaveBeenCalledWith({
      selected: ["X", "Discord"],
      freeText: null,
      answer: "X, Discord",
    });
  });

  it("de-selects on a second click", () => {
    render(<QuestionCard spec={MULTI} onAnswer={vi.fn()} />);
    fireEvent.click(screen.getByTestId("qc-option-X"));
    fireEvent.click(screen.getByTestId("qc-option-X"));
    expect(screen.getByTestId("qc-count")).toHaveTextContent("0 of 3");
    expect(screen.getByTestId("qc-confirm")).toBeDisabled();
  });

  it("sends an empty answer for Skip so the host can decline the interrupt", () => {
    const onAnswer = vi.fn();
    render(<QuestionCard spec={MULTI} onAnswer={onAnswer} />);
    fireEvent.click(screen.getByTestId("qc-skip"));
    expect(onAnswer).toHaveBeenCalledWith({
      selected: [],
      freeText: null,
      answer: "",
    });
  });
});

describe("parseQuestion", () => {
  it("reads the payload the backend already emits", () => {
    const parsed = parseQuestion({
      header: "Quick question",
      question: "Which treasury?",
      hint: "Both have a Launch Week label",
      options: [
        { label: "Ops Safe", recommended: true },
        { label: "Growth Safe", description: "the older one" },
      ],
      multi_select: false,
      allow_free_text: true,
    });
    expect(parsed?.options.map((o) => o.label)).toEqual([
      "Ops Safe",
      "Growth Safe",
    ]);
    expect(parsed?.options[0]?.recommended).toBe(true);
    expect(parsed?.hint).toBe("Both have a Launch Week label");
  });

  it("accepts bare string options, as the tool contract normalises them", () => {
    const parsed = parseQuestion({ question: "Pick one", options: ["a", "b"] });
    expect(parsed?.options.map((o) => o.label)).toEqual(["a", "b"]);
  });

  it("defaults free text ON so a question is always answerable", () => {
    expect(parseQuestion({ question: "Why?" })?.allowFreeText).toBe(true);
  });

  it("returns null when there is no question rather than inventing one", () => {
    expect(parseQuestion({ options: ["a"] })).toBeNull();
    expect(parseQuestion(null)).toBeNull();
    expect(parseQuestion("Which treasury?")).toBeNull();
  });

  it("caps the option list", () => {
    const parsed = parseQuestion({
      question: "Pick",
      options: Array.from({ length: 30 }, (_, i) => `opt-${i}`),
    });
    expect(parsed?.options.length).toBe(8);
  });

  it("knows when a spec has no way to answer it", () => {
    const parsed = parseQuestion({
      question: "Why?",
      allow_free_text: false,
    });
    expect(parsed).not.toBeNull();
    expect(isAnswerable(parsed!)).toBe(false);
  });
});

describe("composeAnswer", () => {
  it("joins a selection and a typed addition the way the tool does", () => {
    expect(composeAnswer(["X", "Discord"], "and Telegram")).toBe(
      "X, Discord, and Telegram",
    );
  });

  it("is null when there is nothing to send", () => {
    expect(composeAnswer([], "")).toBeNull();
    expect(composeAnswer([], "   ")).toBeNull();
  });
});
