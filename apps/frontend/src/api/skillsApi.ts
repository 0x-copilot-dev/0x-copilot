import type {
  CreateSkillRequest,
  Skill,
  SkillListResponse,
  UpdateSkillRequest,
} from "@enterprise-search/api-types";
import type { RequestIdentity } from "./config";
import { httpDelete, httpGet, httpPost, httpPutQuery } from "./http";

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

export function updateSkill(
  skillId: string,
  payload: UpdateSkillRequest,
  identity: RequestIdentity,
): Promise<Skill> {
  return httpPutQuery<Skill>(`/v1/skills/${skillId}`, payload, identity);
}

export function deleteSkill(
  skillId: string,
  identity: RequestIdentity,
): Promise<void> {
  return httpDelete(`/v1/skills/${skillId}`, identity);
}
