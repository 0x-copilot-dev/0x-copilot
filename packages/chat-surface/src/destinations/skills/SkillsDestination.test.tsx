import type {
  SectionResult,
  SkillId,
  SkillSummary,
} from "@0x-copilot/api-types";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { SKILLS_SUBTITLE_COPY, SkillsDestination } from "./SkillsDestination";
import { runCountLabel } from "./SkillCard";

// Pin `now` so relative-time output is deterministic.
const NOW = Date.parse("2026-07-18T12:00:00Z");

const asSkillId = (s: string): SkillId => s as unknown as SkillId;

function skill(overrides: Partial<SkillSummary> = {}): SkillSummary {
  return {
    id: asSkillId("skill-1"),
    name: "Weekly revenue digest",
    description: "Pull last week's numbers, summarise, and post to Slack.",
    run_count: 3,
    updated_at: "2026-07-17T12:00:00Z",
    ...overrides,
  };
}

function ok(
  rows: ReadonlyArray<SkillSummary>,
): SectionResult<ReadonlyArray<SkillSummary>> {
  return { status: "ok", data: rows };
}

describe("SkillsDestination", () => {
  describe("ready state — cards", () => {
    it("renders name, description sub, and `N runs` per card", () => {
      render(<SkillsDestination items={ok([skill()])} now={NOW} />);
      const card = screen.getByTestId("skill-card");
      expect(within(card).getByTestId("skill-card-name").textContent).toBe(
        "Weekly revenue digest",
      );
      expect(
        within(card).getByTestId("skill-card-description").textContent,
      ).toContain("Pull last week's numbers");
      expect(within(card).getByTestId("skill-card-runs").textContent).toBe(
        "3 runs",
      );
    });

    it("singularises the run badge for a single run", () => {
      render(
        <SkillsDestination items={ok([skill({ run_count: 1 })])} now={NOW} />,
      );
      expect(screen.getByTestId("skill-card-runs").textContent).toBe("1 run");
    });

    it("renders the DESIGN-SPEC subtitle copy", () => {
      render(<SkillsDestination items={ok([skill()])} now={NOW} />);
      expect(screen.getByTestId("page-header-subtitle").textContent).toBe(
        SKILLS_SUBTITLE_COPY,
      );
    });

    it("renders one card per skill", () => {
      render(
        <SkillsDestination
          items={ok([
            skill({ id: asSkillId("a"), name: "A" }),
            skill({ id: asSkillId("b"), name: "B" }),
            skill({ id: asSkillId("c"), name: "C" }),
          ])}
          now={NOW}
        />,
      );
      expect(screen.getAllByTestId("skill-card")).toHaveLength(3);
    });
  });

  describe("callbacks", () => {
    it("Run fires onRunSkill with the skill id", () => {
      const onRunSkill = vi.fn();
      render(
        <SkillsDestination
          items={ok([skill({ id: asSkillId("s-run") })])}
          onRunSkill={onRunSkill}
          now={NOW}
        />,
      );
      fireEvent.click(screen.getByTestId("skill-card-run"));
      expect(onRunSkill).toHaveBeenCalledTimes(1);
      expect(onRunSkill).toHaveBeenCalledWith("s-run");
    });

    it("Edit fires onEditSkill with the skill id", () => {
      const onEditSkill = vi.fn();
      render(
        <SkillsDestination
          items={ok([skill({ id: asSkillId("s-edit") })])}
          onEditSkill={onEditSkill}
          now={NOW}
        />,
      );
      fireEvent.click(screen.getByTestId("skill-card-edit"));
      expect(onEditSkill).toHaveBeenCalledTimes(1);
      expect(onEditSkill).toHaveBeenCalledWith("s-edit");
    });

    it("New skill in the header fires onNewSkill", () => {
      const onNewSkill = vi.fn();
      render(
        <SkillsDestination
          items={ok([skill()])}
          onNewSkill={onNewSkill}
          now={NOW}
        />,
      );
      fireEvent.click(screen.getByTestId("page-header-primary-action"));
      expect(onNewSkill).toHaveBeenCalledTimes(1);
    });

    it("omits Run / Edit buttons when no callback is supplied", () => {
      render(<SkillsDestination items={ok([skill()])} now={NOW} />);
      expect(screen.queryByTestId("skill-card-run")).toBeNull();
      expect(screen.queryByTestId("skill-card-edit")).toBeNull();
    });
  });

  describe("4-state machine", () => {
    it("loading: renders skeleton cards with data-state=loading", () => {
      render(<SkillsDestination items={null} now={NOW} />);
      expect(screen.getByTestId("skills-destination")).toHaveAttribute(
        "data-state",
        "loading",
      );
      expect(
        screen.getAllByTestId("skills-skeleton-card").length,
      ).toBeGreaterThan(0);
    });

    it("error: renders an alert EmptyState with Retry that fires onRetry", () => {
      const onRetry = vi.fn();
      render(
        <SkillsDestination
          items={{ status: "error", error: "boom" }}
          onRetry={onRetry}
          now={NOW}
        />,
      );
      expect(screen.getByTestId("skills-destination")).toHaveAttribute(
        "data-state",
        "error",
      );
      expect(screen.getByRole("alert")).toBeInTheDocument();
      expect(screen.getByTestId("empty-state-body").textContent).toBe("boom");
      fireEvent.click(screen.getByTestId("empty-state-action"));
      expect(onRetry).toHaveBeenCalledTimes(1);
    });

    it("unavailable: renders the not-enabled empty-state", () => {
      render(<SkillsDestination items={{ status: "unavailable" }} now={NOW} />);
      expect(screen.getByTestId("skills-destination")).toHaveAttribute(
        "data-state",
        "unavailable",
      );
      expect(screen.getByTestId("empty-state-title").textContent).toBe(
        "Skills unavailable",
      );
    });

    it("empty: renders 'No skills yet' + New skill CTA", () => {
      const onNewSkill = vi.fn();
      render(
        <SkillsDestination items={ok([])} onNewSkill={onNewSkill} now={NOW} />,
      );
      expect(screen.getByTestId("skills-destination")).toHaveAttribute(
        "data-state",
        "ready",
      );
      expect(screen.getByTestId("empty-state-title").textContent).toBe(
        "No skills yet",
      );
      fireEvent.click(screen.getByTestId("empty-state-action"));
      expect(onNewSkill).toHaveBeenCalledTimes(1);
    });
  });
});

describe("runCountLabel", () => {
  it("pluralises and guards bad input", () => {
    expect(runCountLabel(0)).toBe("0 runs");
    expect(runCountLabel(1)).toBe("1 run");
    expect(runCountLabel(42)).toBe("42 runs");
    expect(runCountLabel(-5)).toBe("0 runs");
    expect(runCountLabel(Number.NaN)).toBe("0 runs");
    expect(runCountLabel(2.9)).toBe("2 runs");
  });
});
