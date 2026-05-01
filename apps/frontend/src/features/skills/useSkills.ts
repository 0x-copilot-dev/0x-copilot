import type {
  Skill,
  SkillScope,
  UpdateSkillRequest,
} from "@enterprise-search/api-types";
import { useCallback, useEffect, useMemo, useState } from "react";
import type { RequestIdentity } from "../../api/config";
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
  const [skills, setSkills] = useState<Skill[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    if (identity === null) {
      setLoading(false);
      return;
    }
    try {
      setLoading(true);
      setSkills(await listSkills(identity));
      setError(null);
    } catch (err) {
      setError(errorMessage(err, "Could not load skills"));
    } finally {
      setLoading(false);
    }
  }, [identity]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const actions = useMemo(
    () => ({
      refresh,
      async create(payload: {
        markdown: string;
        displayName?: string;
        enabled?: boolean;
        scope?: SkillScope;
      }): Promise<void> {
        const currentIdentity = requireIdentity(identity);
        await createSkill(
          {
            markdown: payload.markdown,
            display_name: payload.displayName,
            enabled: payload.enabled,
            scope: payload.scope,
          },
          currentIdentity,
        );
        await refresh();
      },
      async update(
        skillId: string,
        payload: UpdateSkillRequest,
      ): Promise<void> {
        const currentIdentity = requireIdentity(identity);
        await updateSkill(skillId, payload, currentIdentity);
        await refresh();
      },
      async remove(skillId: string): Promise<void> {
        const currentIdentity = requireIdentity(identity);
        await deleteSkill(skillId, currentIdentity);
        await refresh();
      },
      async setEnabled(skillId: string, enabled: boolean): Promise<void> {
        const currentIdentity = requireIdentity(identity);
        await updateSkill(skillId, { enabled }, currentIdentity);
        await refresh();
      },
    }),
    [identity, refresh],
  );

  return {
    skills,
    loading,
    error,
    ...actions,
  };
}

function errorMessage(err: unknown, fallback: string): string {
  return err instanceof Error ? err.message : fallback;
}

function requireIdentity(identity: RequestIdentity | null): RequestIdentity {
  if (identity === null) {
    throw new Error("Session identity is not loaded.");
  }
  return identity;
}
