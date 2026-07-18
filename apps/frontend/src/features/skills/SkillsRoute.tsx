// SkillsRoute — host binder for the Phase 4 Skills destination
// (desktop-redesign phase-4 PRD FR-4.26 / FR-4.27 / FR-4.28 / FR-4.29).
//
// The presentational catalog (`<SkillsDestination>`, PR-4.9) lives in
// `@0x-copilot/chat-surface` and takes props/callbacks only. This route is
// the substrate-side binder that:
//
//   1. Sources the skill list from the existing `useSkills` hook (which
//      wraps `GET /v1/skills` via `skillsApi.listSkills`) and projects the
//      authoring `Skill[]` down to the card-row `SkillSummary[]`, wrapped in
//      the 4-state `SectionResult` the destination consumes.
//   2. Wires the card + header callbacks (FR-4.27 / FR-4.28):
//        - Run  → start a run (create conversation + run that instructs the
//                 model to use the skill) then navigate to that run's thread.
//        - Edit → open the skill editor route for the given skill.
//        - New  → open the skill editor route for a fresh skill.
//
// Navigation is injected as callback props so the host App (PR-4.11 IA-fold)
// owns the router; this route never imports App.tsx / routes.ts. `onOpenRun`
// falls back to the web `?conversationId=` deep-link (same contract
// ShareScreen uses) so the route also works standalone.
//
// Boundary: component from `@0x-copilot/chat-surface`, types from
// `@0x-copilot/api-types`; no `apps/* → apps/*` imports. The skill-run
// instruction reuses the composer's canonical `skillInstructionPrompt`
// helper (single source of truth for "invoke skill by name").

import { useCallback, useMemo, useState, type ReactElement } from "react";

import { SkillsDestination } from "@0x-copilot/chat-surface";
import type {
  SectionResult,
  Skill,
  SkillId,
  SkillSummary,
} from "@0x-copilot/api-types";

import type { RequestIdentity } from "../../api/config";
import { createConversation, createRun } from "../../api/agentApi";
import { errorMessage } from "../../utils/errors";
import { skillInstructionPrompt } from "../chat/prompts";
import { useSkills } from "./useSkills";

interface SkillsRouteProps {
  readonly identity: RequestIdentity;
  /**
   * Navigate to the run surface (the chat thread) for a freshly-started
   * run. PR-4.11 wires this to the host router; when omitted the route
   * falls back to the web `?conversationId=` deep-link (matches
   * ShareScreen's post-fork navigation).
   */
  readonly onOpenRun?: (conversationId: string) => void;
  /**
   * Open the skill editor route. `null` opens the editor for a NEW skill
   * (FR-4.28); a `SkillId` opens it to edit that skill (FR-4.27). Host-wired
   * in PR-4.11 (no editor route exists in the web AppRoute union yet).
   */
  readonly onOpenSkillEditor?: (skillId: SkillId | null) => void;
}

// ---------------------------------------------------------------------------
// Skill (authoring shape) → SkillSummary (card row) projection.
//
// `run_count` defaults to 0 until the backend surfaces per-skill run counts
// (see api-types/src/skills.ts + PRD §11 backend gaps). `name` prefers the
// human `display_name`, falling back to the stable `name` slug.
// ---------------------------------------------------------------------------

function toSummary(skill: Skill): SkillSummary {
  return {
    id: skill.skill_id as SkillId,
    name: skill.display_name || skill.name,
    description: skill.description,
    run_count: 0,
    updated_at: skill.updated_at,
  };
}

// ===========================================================================
// SkillsRoute
// ===========================================================================

export function SkillsRoute({
  identity,
  onOpenRun,
  onOpenSkillEditor,
}: SkillsRouteProps): ReactElement {
  const { skills, loading, error, refresh } = useSkills(identity);
  const [runError, setRunError] = useState<string | null>(null);

  // ---- Skill list → SectionResult (4-state machine, FR-4.2) ----------
  const items = useMemo<SectionResult<
    ReadonlyArray<SkillSummary>
  > | null>(() => {
    if (error !== null) {
      return { status: "error", error };
    }
    if (loading) {
      return null; // loading skeleton
    }
    return { status: "ok", data: skills.map(toSummary) };
  }, [error, loading, skills]);

  // ---- Run → start a run + navigate to the run's thread (FR-4.27) -----
  const openRun = useCallback(
    (conversationId: string): void => {
      if (onOpenRun !== undefined) {
        onOpenRun(conversationId);
        return;
      }
      if (typeof window !== "undefined") {
        window.location.href = `/?conversationId=${encodeURIComponent(conversationId)}`;
      }
    },
    [onOpenRun],
  );

  const startSkillRun = useCallback(
    async (id: SkillId): Promise<void> => {
      const skill = skills.find((s) => s.skill_id === id);
      const displayName = skill?.display_name || skill?.name || "";
      setRunError(null);
      try {
        const conversation = await createConversation(identity, {
          title: displayName ? `Run: ${displayName}` : "Run skill",
        });
        await createRun(
          conversation.conversation_id,
          skillInstructionPrompt(displayName),
          identity,
        );
        openRun(conversation.conversation_id);
      } catch (err: unknown) {
        setRunError(errorMessage(err, "Could not start the skill run."));
      }
    },
    [skills, identity, openRun],
  );

  const handleRunSkill = useCallback(
    (id: SkillId): void => {
      void startSkillRun(id);
    },
    [startSkillRun],
  );

  // ---- Edit / New → open the skill editor route (FR-4.27 / FR-4.28) ---
  const handleEditSkill = useCallback(
    (id: SkillId): void => {
      onOpenSkillEditor?.(id);
    },
    [onOpenSkillEditor],
  );

  const handleNewSkill = useCallback((): void => {
    onOpenSkillEditor?.(null);
  }, [onOpenSkillEditor]);

  // ---- Render --------------------------------------------------------
  return (
    <section
      aria-label="Skills destination"
      data-testid="skills-route"
      data-state={items === null ? "loading" : items.status}
      style={{ height: "100%", width: "100%", overflow: "auto" }}
    >
      {runError !== null && (
        <div
          role="status"
          data-testid="skills-route-run-error"
          style={{
            margin: 16,
            padding: 12,
            border: "1px solid var(--color-border-strong)",
            borderRadius: 8,
            backgroundColor: "var(--color-surface)",
            fontSize: 13,
          }}
        >
          {runError}
        </div>
      )}
      <SkillsDestination
        items={items}
        onRunSkill={handleRunSkill}
        onEditSkill={handleEditSkill}
        onNewSkill={handleNewSkill}
        onRetry={() => {
          void refresh();
        }}
      />
    </section>
  );
}
