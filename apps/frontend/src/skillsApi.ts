import type {
  CreateSkillRequest,
  Skill,
  SkillListResponse,
  SkillScope,
  UpdateSkillRequest
} from "@enterprise-search/api-types";

const DEFAULT_ORG_ID = "org_123";
const DEFAULT_USER_ID = "user_123";

export const DEFAULT_SKILL_MARKDOWN = `---
name: launch-risk-review
description: Review launch plans and produce a concise risk summary.
allowed_tools: []
---

# Launch Risk Review

Use this skill when the user asks for launch readiness, launch risks, or release planning.
Return the top risks, owners, missing evidence, and recommended next steps.`;

export async function listSkills(): Promise<Skill[]> {
  const params = new URLSearchParams({ org_id: DEFAULT_ORG_ID, user_id: DEFAULT_USER_ID });
  const response = await fetch(`/v1/skills?${params}`);
  assertOk(response);
  const payload = (await response.json()) as SkillListResponse;
  return payload.skills;
}

export async function createSkill(markdown: string, scope: SkillScope): Promise<Skill> {
  const payload: CreateSkillRequest = {
    org_id: DEFAULT_ORG_ID,
    user_id: DEFAULT_USER_ID,
    markdown,
    scope
  };
  const response = await fetch("/v1/skills", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload)
  });
  assertOk(response);
  return (await response.json()) as Skill;
}

export async function updateSkill(
  skillId: string,
  markdown: string,
  enabled: boolean,
  scope: SkillScope
): Promise<Skill> {
  const params = new URLSearchParams({ org_id: DEFAULT_ORG_ID, user_id: DEFAULT_USER_ID });
  const payload: UpdateSkillRequest = { markdown, enabled, scope };
  const response = await fetch(`/v1/skills/${skillId}?${params}`, {
    method: "PUT",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload)
  });
  assertOk(response);
  return (await response.json()) as Skill;
}

export async function deleteSkill(skillId: string): Promise<void> {
  const params = new URLSearchParams({ org_id: DEFAULT_ORG_ID, user_id: DEFAULT_USER_ID });
  const response = await fetch(`/v1/skills/${skillId}?${params}`, {
    method: "DELETE"
  });
  assertOk(response);
}

function assertOk(response: Response): void {
  if (!response.ok) {
    throw new Error(`Request failed with ${response.status}`);
  }
}
