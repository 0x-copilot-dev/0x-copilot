import type {
  CreateSkillRequest,
  Skill,
  SkillListResponse,
  UpdateSkillRequest,
} from "@enterprise-search/api-types";
import type { RequestIdentity } from "./config";
import { identityParams } from "./config";
import { assertOk, jsonHeaders } from "./http";

export async function listSkills(identity: RequestIdentity): Promise<Skill[]> {
  const response = await fetch(`/v1/skills?${identityParams(identity)}`);
  await assertOk(response);
  const payload = (await response.json()) as SkillListResponse;
  return payload.skills;
}

export async function createSkill(
  payload: Omit<CreateSkillRequest, "org_id" | "user_id">,
  identity: RequestIdentity,
): Promise<Skill> {
  const response = await fetch("/v1/skills", {
    method: "POST",
    headers: jsonHeaders(),
    body: JSON.stringify({
      ...payload,
      org_id: identity.orgId,
      user_id: identity.userId,
    }),
  });
  await assertOk(response);
  return (await response.json()) as Skill;
}

export async function updateSkill(
  skillId: string,
  payload: UpdateSkillRequest,
  identity: RequestIdentity,
): Promise<Skill> {
  const response = await fetch(
    `/v1/skills/${skillId}?${identityParams(identity)}`,
    {
      method: "PUT",
      headers: jsonHeaders(),
      body: JSON.stringify(payload),
    },
  );
  await assertOk(response);
  return (await response.json()) as Skill;
}

export async function deleteSkill(
  skillId: string,
  identity: RequestIdentity,
): Promise<void> {
  const response = await fetch(
    `/v1/skills/${skillId}?${identityParams(identity)}`,
    {
      method: "DELETE",
    },
  );
  await assertOk(response);
}
