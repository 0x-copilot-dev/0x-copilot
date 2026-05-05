// PR 3.2 — Skills tab body for the right-rail workspace pane.
//
// Pure presentational. Receives the user's skills (from
// `useSkills(identity)`) and an `onPick(skill)` handler that the
// parent wires to the assistant-ui composer (`aui.composer().setText`).
// Disabled skills are visible but unclickable; the manage link routes
// to Settings → Skills via the existing `onOpenSettings` hook.

import {
  Badge,
  Button,
  Card,
  classNames,
} from "@enterprise-search/design-system";
import type { Skill } from "@enterprise-search/api-types";
import type { ReactElement } from "react";

export interface SkillsTabProps {
  skills: readonly Skill[];
  loading?: boolean;
  error?: string | null;
  onPick?: (skill: Skill) => void;
  onOpenSettings?: () => void;
}

export function SkillsTab({
  skills,
  loading,
  error,
  onPick,
  onOpenSettings,
}: SkillsTabProps): ReactElement {
  const enabled = skills.filter((skill) => skill.enabled);
  if (skills.length === 0) {
    return (
      <div
        className="atlas-workspace-tab atlas-workspace-tab--empty"
        data-testid="workspace-skills-tab-empty"
      >
        {loading ? (
          <p>Loading skills…</p>
        ) : error ? (
          <p role="alert">Couldn’t load skills — {error}</p>
        ) : (
          <p>You don’t have any skills yet.</p>
        )}
        {onOpenSettings ? (
          <Button
            type="button"
            variant="secondary"
            size="sm"
            onClick={onOpenSettings}
          >
            Manage skills
          </Button>
        ) : null}
      </div>
    );
  }

  return (
    <div className="atlas-workspace-tab" data-testid="workspace-skills-tab">
      <ul
        className="atlas-workspace-tab__list"
        aria-label={`${enabled.length} skills`}
      >
        {skills.map((skill) => {
          const disabled = !skill.enabled || onPick === undefined;
          return (
            <li
              key={skill.skill_id}
              className={classNames(
                "atlas-workspace-tab__item",
                disabled && "atlas-workspace-tab__item--disabled",
              )}
              data-skill-id={skill.skill_id}
            >
              <Card>
                <button
                  type="button"
                  className="atlas-workspace-skill-row"
                  disabled={disabled}
                  onClick={() => onPick?.(skill)}
                  aria-label={`Insert /${skill.name} into composer`}
                >
                  <div className="atlas-workspace-skill-row__head">
                    <code className="atlas-workspace-skill-row__slash">
                      /{skill.name}
                    </code>
                    {skill.scope === "org" ? (
                      <Badge tone="accent">workspace</Badge>
                    ) : (
                      <Badge tone="neutral">you</Badge>
                    )}
                    {!skill.enabled ? (
                      <Badge tone="warning">disabled</Badge>
                    ) : null}
                  </div>
                  <p className="atlas-workspace-skill-row__title">
                    {skill.display_name}
                  </p>
                  {skill.description ? (
                    <p className="atlas-workspace-skill-row__description">
                      {skill.description}
                    </p>
                  ) : null}
                </button>
              </Card>
            </li>
          );
        })}
      </ul>
      {onOpenSettings ? (
        <footer className="atlas-workspace-tab__footer">
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={onOpenSettings}
          >
            Manage skills
          </Button>
        </footer>
      ) : null}
    </div>
  );
}
