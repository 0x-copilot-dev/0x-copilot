import type {
  CreateSkillRequest,
  Skill,
  SkillListResponse,
  UpdateSkillRequest,
} from "@enterprise-search/api-types";
import type { RequestIdentity } from "./config";
import { identityParams } from "./config";
import { assertOk, httpDelete, httpGet, httpPost, jsonHeaders } from "./http";

export async function listSkills(identity: RequestIdentity): Promise<Skill[]> {
  const payload = await httpGet<SkillListResponse>("/v1/skills", identity);
  return payload.skills;
}

export function createSkill(
  payload: Omit<CreateSkillRequest, "org_id" | "user_id">,
  identity: RequestIdentity,
): Promise<Skill> {
  return httpPost<Skill>("/v1/skills", {
    ...payload,
    org_id: identity.orgId,
    user_id: identity.userId,
  });
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

export function deleteSkill(
  skillId: string,
  identity: RequestIdentity,
): Promise<void> {
  return httpDelete(`/v1/skills/${skillId}`, identity);
}
