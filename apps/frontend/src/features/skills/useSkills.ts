import type {
  Skill,
  SkillScope,
  UpdateSkillRequest,
} from "@0x-copilot/api-types";
import { useMemo } from "react";
import type { RequestIdentity } from "../../api/config";
import { requireIdentity, useResource } from "../../api/useResource";
import {
  createSkill,
  deleteSkill,
  listSkills,
  updateSkill,
} from "../../api/skillsApi";

export interface SkillState {
  skills: Skill[];
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
  create: (payload: {
    markdown: string;
    displayName?: string;
    enabled?: boolean;
    scope?: SkillScope;
  }) => Promise<void>;
  update: (skillId: string, payload: UpdateSkillRequest) => Promise<void>;
  remove: (skillId: string) => Promise<void>;
  setEnabled: (skillId: string, enabled: boolean) => Promise<void>;
}

export function useSkills(identity: RequestIdentity | null): SkillState {
  const { data, loading, error, refresh } = useResource<Skill>(
    identity,
    listSkills,
    "Could not load skills",
  );

  const actions = useMemo(
    () => ({
      async create(payload: {
        markdown: string;
        displayName?: string;
        enabled?: boolean;
        scope?: SkillScope;
      }): Promise<void> {
        await createSkill(
          {
            markdown: payload.markdown,
            display_name: payload.displayName,
            enabled: payload.enabled,
            scope: payload.scope,
          },
          requireIdentity(identity),
        );
        await refresh();
      },
      async update(
        skillId: string,
        payload: UpdateSkillRequest,
      ): Promise<void> {
        await updateSkill(skillId, payload, requireIdentity(identity));
        await refresh();
      },
      async remove(skillId: string): Promise<void> {
        await deleteSkill(skillId, requireIdentity(identity));
        await refresh();
      },
      async setEnabled(skillId: string, enabled: boolean): Promise<void> {
        await updateSkill(skillId, { enabled }, requireIdentity(identity));
        await refresh();
      },
    }),
    [identity, refresh],
  );

  return { skills: data, loading, error, refresh, ...actions };
}
