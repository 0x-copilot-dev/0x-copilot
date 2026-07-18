// @vitest-environment node
import { describe, expect, it } from "vitest";

import type { SkillId } from "./brands";
import type { SkillSummary } from "./skills";

// Runtime assertions over the Skills catalog summary contract (desktop
// redesign, Phase 4). `SkillSummary` is the lightweight card-row
// projection distinct from the richer authoring `Skill` (FR-4.26/4.33).

describe("SkillSummary — shape", () => {
  const skill: SkillSummary = {
    id: "skill_001" as SkillId,
    name: "Weekly revenue digest",
    description: "Pull the numbers, chart them, and post to Slack.",
    run_count: 12,
    updated_at: "2026-07-18T08:00:00Z",
  };

  it("carries exactly the summary fields", () => {
    expect(Object.keys(skill).sort()).toEqual(
      ["description", "id", "name", "run_count", "updated_at"].sort(),
    );
  });

  it("run_count is a number (the `N runs` badge)", () => {
    expect(typeof skill.run_count).toBe("number");
  });
});
