// Skills settings — the create / edit / view-markdown skill editor sub-feature.
// Extracted verbatim from `SettingsScreen.tsx` (behavior unchanged) so the shell
// stays thin. Self-contained, like ConnectorsSettings: the unit to mount from the
// Skills rail destination when the legacy screen is retired.

import type { Skill, SkillScope } from "@0x-copilot/api-types";
import {
  Badge,
  Button,
  Card,
  Field,
  Select,
  Switch,
  TextInput,
} from "@0x-copilot/design-system";
import type { FormEvent, ReactElement } from "react";
import { useState } from "react";

import type { SkillState } from "../../skills/useSkills";
import { errorMessage } from "../../../utils/errors";

const DEFAULT_SKILL_MARKDOWN = `---
name: custom-workflow
description: Describe what this skill does and when the agent should use it.
allowed_tools: []
---
# Custom Workflow

## When To Use

Use this skill when...

## Workflow

1. Clarify the goal.
2. Gather the required context.
3. Produce the requested output.
`;

export function SkillsSettings({
  skills,
}: {
  skills: SkillState;
}): ReactElement {
  const [displayName, setDisplayName] = useState("");
  const [scope, setScope] = useState<SkillScope>("user");
  const [markdown, setMarkdown] = useState(DEFAULT_SKILL_MARKDOWN);
  const [formError, setFormError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [editingSkillId, setEditingSkillId] = useState<string | null>(null);
  const [editDisplayName, setEditDisplayName] = useState("");
  const [editScope, setEditScope] = useState<SkillScope>("user");
  const [editMarkdown, setEditMarkdown] = useState("");
  const [editingError, setEditingError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  async function onSubmit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    try {
      setFormError(null);
      setSubmitting(true);
      await skills.create({
        markdown,
        displayName: displayName.trim() || undefined,
        enabled: true,
        scope,
      });
      setDisplayName("");
      setScope("user");
      setMarkdown(DEFAULT_SKILL_MARKDOWN);
    } catch (err) {
      setFormError(errorMessage(err, "Could not create skill."));
    } finally {
      setSubmitting(false);
    }
  }

  function beginEdit(skill: Skill): void {
    setEditingSkillId(skill.skill_id);
    setEditDisplayName(skill.display_name);
    setEditScope(skill.scope);
    setEditMarkdown(skill.markdown);
    setEditingError(null);
  }

  async function onSaveEdit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (editingSkillId === null) {
      return;
    }
    try {
      setEditingError(null);
      setSaving(true);
      await skills.update(editingSkillId, {
        display_name: editDisplayName,
        scope: editScope,
        markdown: editMarkdown,
      });
      setEditingSkillId(null);
    } catch (err) {
      setEditingError(errorMessage(err, "Could not update skill."));
    } finally {
      setSaving(false);
    }
  }

  async function onDelete(skillId: string): Promise<void> {
    try {
      setEditingError(null);
      setSaving(true);
      await skills.remove(skillId);
      setEditingSkillId(null);
    } catch (err) {
      setEditingError(errorMessage(err, "Could not delete skill."));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="settings-section">
      <div className="settings-section__header">
        <div>
          <h2>Skills</h2>
          <p>Enable preloaded workflows or add customer Skills as markdown.</p>
        </div>
        <Button
          type="button"
          variant="secondary"
          title="Refresh skills"
          onClick={() => void skills.refresh()}
        >
          Refresh
        </Button>
      </div>

      <Card>
        <form
          className="skill-editor-form"
          onSubmit={(event) => void onSubmit(event)}
        >
          <div className="skill-editor-form__row">
            <Field
              label="Display name"
              hint="Optional. Defaults to the markdown name."
            >
              <TextInput
                value={displayName}
                onChange={(event) => setDisplayName(event.target.value)}
                placeholder="Customer Handoff"
              />
            </Field>
            <Field label="Scope">
              <Select
                value={scope}
                onChange={(event) => setScope(event.target.value as SkillScope)}
              >
                <option value="user">User</option>
                <option value="org">Organization</option>
              </Select>
            </Field>
          </div>
          <Field
            label="Skill markdown"
            hint="Start with YAML frontmatter containing name and description."
          >
            <textarea
              className="skill-markdown-editor"
              value={markdown}
              onChange={(event) => setMarkdown(event.target.value)}
              spellCheck={false}
            />
          </Field>
          <Button type="submit" disabled={submitting} title="Add skill">
            Add skill
          </Button>
        </form>
        {formError ? <p className="app-error">{formError}</p> : null}
      </Card>

      <div className="connector-settings-list">
        {skills.loading ? (
          <Card>
            <p>Loading skills...</p>
          </Card>
        ) : null}
        {skills.error ? <p className="app-error">{skills.error}</p> : null}
        {!skills.loading && skills.skills.length === 0 ? (
          <Card>
            <p>No skills configured yet.</p>
          </Card>
        ) : null}
        {skills.skills.map((skill) => {
          const isEditing = editingSkillId === skill.skill_id;
          const isPreloaded = skill.source_type === "preloaded";
          const isSystem = skill.source_type === "system";
          // System skills are runtime infrastructure (e.g. search-subagent-logs).
          // They cannot be disabled or edited — disabling would break the
          // supervisor's ability to fulfil the protocol the skill defines.
          const isReadOnly = isPreloaded || isSystem;
          const readOnlyHint = isSystem
            ? "System Skills are required for runtime functionality and cannot be disabled."
            : "Preloaded Skills are read-only.";
          return (
            <Card className="connector-settings-row" key={skill.skill_id}>
              <div className="connector-settings-row__main">
                <div>
                  <h3>{skill.display_name}</h3>
                  <p>{skill.description || skill.virtual_path}</p>
                </div>
                <div className="skill-source-badges">
                  <Badge tone={skill.enabled ? "success" : "neutral"}>
                    {skill.enabled ? "enabled" : "disabled"}
                  </Badge>
                  <Badge tone="neutral">{skill.scope}</Badge>
                  <Badge tone={isSystem ? "accent" : "neutral"}>
                    {skill.source_type}
                  </Badge>
                </div>
              </div>
              <div className="connector-settings-row__controls">
                {isSystem ? (
                  <span className="settings-meta">Always on</span>
                ) : (
                  <Switch
                    label={skill.enabled ? "Enabled" : "Disabled"}
                    checked={skill.enabled}
                    onChange={(event) =>
                      void skills.setEnabled(
                        skill.skill_id,
                        event.target.checked,
                      )
                    }
                  />
                )}
                <span className="settings-meta">
                  Version {skill.version} - {skill.source_type}
                </span>
                <Button
                  type="button"
                  variant="secondary"
                  title={isReadOnly ? "View skill markdown" : "Edit this skill"}
                  onClick={() => beginEdit(skill)}
                >
                  {isReadOnly ? "View markdown" : "Edit"}
                </Button>
              </div>
              {isEditing && isReadOnly ? (
                <div className="skill-editor-form">
                  <Field
                    label={isSystem ? "System markdown" : "Preloaded markdown"}
                    hint={readOnlyHint}
                  >
                    <textarea
                      className="skill-markdown-editor"
                      readOnly
                      value={skill.markdown}
                      spellCheck={false}
                    />
                  </Field>
                  <Button
                    type="button"
                    variant="secondary"
                    title="Close markdown viewer"
                    onClick={() => setEditingSkillId(null)}
                  >
                    Close
                  </Button>
                </div>
              ) : null}
              {isEditing && !isReadOnly ? (
                <form
                  className="skill-editor-form"
                  onSubmit={(event) => void onSaveEdit(event)}
                >
                  <div className="skill-editor-form__row">
                    <Field label="Display name">
                      <TextInput
                        value={editDisplayName}
                        onChange={(event) =>
                          setEditDisplayName(event.target.value)
                        }
                      />
                    </Field>
                    <Field label="Scope">
                      <Select
                        value={editScope}
                        onChange={(event) =>
                          setEditScope(event.target.value as SkillScope)
                        }
                      >
                        <option value="user">User</option>
                        <option value="org">Organization</option>
                      </Select>
                    </Field>
                  </div>
                  <Field label="Skill markdown">
                    <textarea
                      className="skill-markdown-editor"
                      value={editMarkdown}
                      onChange={(event) => setEditMarkdown(event.target.value)}
                      spellCheck={false}
                    />
                  </Field>
                  {editingError ? (
                    <p className="app-error">{editingError}</p>
                  ) : null}
                  <div className="skill-row-actions">
                    <Button
                      type="submit"
                      disabled={saving}
                      title="Save skill changes"
                    >
                      Save changes
                    </Button>
                    <Button
                      type="button"
                      variant="secondary"
                      title="Cancel editing skill"
                      onClick={() => setEditingSkillId(null)}
                    >
                      Cancel
                    </Button>
                    <Button
                      type="button"
                      variant="danger"
                      disabled={saving}
                      title="Delete this skill"
                      onClick={() => void onDelete(skill.skill_id)}
                    >
                      Delete
                    </Button>
                  </div>
                </form>
              ) : null}
            </Card>
          );
        })}
      </div>
    </div>
  );
}
