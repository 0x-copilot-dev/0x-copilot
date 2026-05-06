// PR 4.3 — Settings → AI & data → Model & behavior.
//
// One panel, five workspace-policy knobs. Reads/writes through the
// existing ``useWorkspaceDefaults`` hook so we don't duplicate the
// PR 1.6 contract; the new ``behavior_overrides`` block on the wire
// rides through with no extra route. Save is debounced (300 ms) so
// rapid swatch / slider clicks coalesce into one round-trip.
//
// Knobs:
//   * system_prompt_override (textarea, ≤ 8 KB)
//   * temperature             (slider 0.0–1.0, step 0.05)
//   * citation_density        (3-pill: minimal / standard / thorough)
//   * refusal_behavior        (3-pill: standard / strict / permissive)
//   * default_reasoning_effort (3-pill: low / medium / high)
//
// ``training_data_opt_out`` lives on the Privacy & data panel.

import type {
  CitationDensity,
  ReasoningEffort,
  RefusalBehavior,
  UpdateWorkspaceDefaultsRequest,
  WorkspaceBehaviorOverrides,
} from "@enterprise-search/api-types";
import {
  Card,
  Field,
  TextInput,
  classNames,
} from "@enterprise-search/design-system";
import type { ReactElement } from "react";
import { useCallback, useEffect, useRef, useState } from "react";
import type { UseWorkspaceDefaultsResult } from "../useWorkspaceDefaults";
import { ToolUsePolicyPanel } from "./ToolUsePolicyPanel";

const SAVE_DEBOUNCE_MS = 300;
const SYSTEM_PROMPT_MAX = 8 * 1024;
const TEMPERATURE_MIN = 0;
const TEMPERATURE_MAX = 1;
const TEMPERATURE_STEP = 0.05;

const CITATION_DENSITY_OPTIONS: ReadonlyArray<{
  id: CitationDensity;
  label: string;
  hint: string;
}> = [
  {
    id: "minimal",
    label: "Minimal",
    hint: "Cite only the load-bearing claims.",
  },
  {
    id: "standard",
    label: "Standard",
    hint: "Cite material claims (default).",
  },
  { id: "thorough", label: "Thorough", hint: "Cite every supported claim." },
];

const REFUSAL_BEHAVIOR_OPTIONS: ReadonlyArray<{
  id: RefusalBehavior;
  label: string;
  hint: string;
}> = [
  { id: "standard", label: "Standard", hint: "Default safety policy." },
  { id: "strict", label: "Strict", hint: "Refuse borderline cases." },
  {
    id: "permissive",
    label: "Permissive",
    hint: "Lean to answer; policy still applies.",
  },
];

const REASONING_EFFORT_OPTIONS: ReadonlyArray<{
  id: ReasoningEffort;
  label: string;
  hint: string;
}> = [
  { id: "low", label: "Low", hint: "Faster; cheaper." },
  { id: "medium", label: "Medium", hint: "Balanced (default)." },
  { id: "high", label: "High", hint: "Deeper reasoning; slower." },
];

export function ModelAndBehavior({
  workspaceDefaults,
}: {
  workspaceDefaults: UseWorkspaceDefaultsResult;
}): ReactElement {
  const { defaults, loading, error, save } = workspaceDefaults;
  const overrides: WorkspaceBehaviorOverrides =
    defaults?.behavior_overrides ?? { training_data_opt_out: false };
  const [draft, setDraft] = useState<WorkspaceBehaviorOverrides>(overrides);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const debounceRef = useRef<number | null>(null);

  // Resync the draft when the server snapshot changes (initial load,
  // optimistic-rollback, etc.). The dependency array is the JSON of the
  // current overrides so we don't re-render on unrelated defaults churn.
  useEffect(() => {
    setDraft(overrides);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [JSON.stringify(overrides)]);

  useEffect(() => {
    return () => {
      if (debounceRef.current !== null) {
        window.clearTimeout(debounceRef.current);
      }
    };
  }, []);

  const persist = useCallback(
    (next: WorkspaceBehaviorOverrides) => {
      setDraft(next);
      if (defaults === null) {
        return;
      }
      if (debounceRef.current !== null) {
        window.clearTimeout(debounceRef.current);
      }
      debounceRef.current = window.setTimeout(() => {
        const request: UpdateWorkspaceDefaultsRequest = {
          default_model: defaults.default_model,
          default_connectors: defaults.default_connectors,
          retention_days: defaults.retention_days,
          behavior_overrides: next,
        };
        save(request).catch((err: unknown) => {
          setSubmitError(
            err instanceof Error
              ? err.message
              : "Could not save behavior overrides.",
          );
        });
      }, SAVE_DEBOUNCE_MS);
    },
    [defaults, save],
  );

  if (loading) {
    return (
      <div className="settings-section">
        <h2>Model &amp; behavior</h2>
        <Card>
          <p>Loading workspace defaults…</p>
        </Card>
      </div>
    );
  }
  if (defaults === null) {
    return (
      <div className="settings-section">
        <h2>Model &amp; behavior</h2>
        <Card>
          <p role="alert">{error ?? "Could not load workspace defaults."}</p>
        </Card>
      </div>
    );
  }

  return (
    <div className="settings-section">
      <h2>Model &amp; behavior</h2>
      <p>
        Workspace-wide defaults that flow into every new chat unless a user
        overrides them in their composer.
      </p>

      <Card>
        <Field
          label="System prompt override"
          hint={`Prepended to every assistant turn. Up to ${SYSTEM_PROMPT_MAX.toLocaleString()} characters.`}
        >
          <textarea
            className="settings-textarea"
            rows={4}
            maxLength={SYSTEM_PROMPT_MAX}
            placeholder="Always sign off as the GTM team."
            value={draft.system_prompt_override ?? ""}
            onChange={(event) =>
              persist({
                ...draft,
                system_prompt_override: event.target.value || null,
              })
            }
          />
        </Field>
      </Card>

      <Card>
        <Field
          label="Temperature"
          hint="Default model temperature for new chats. Per-run requests still win."
        >
          <div className="settings-slider-row">
            <input
              type="range"
              min={TEMPERATURE_MIN}
              max={TEMPERATURE_MAX}
              step={TEMPERATURE_STEP}
              value={draft.temperature ?? 0}
              onChange={(event) =>
                persist({
                  ...draft,
                  temperature: Number(event.target.value),
                })
              }
            />
            <TextInput
              type="number"
              min={TEMPERATURE_MIN}
              max={TEMPERATURE_MAX}
              step={TEMPERATURE_STEP}
              value={draft.temperature ?? 0}
              onChange={(event) =>
                persist({
                  ...draft,
                  temperature: Number(event.target.value),
                })
              }
            />
          </div>
        </Field>
      </Card>

      <PillGroup<CitationDensity>
        label="Citation density"
        hint="How aggressively the agent attaches citations to its claims."
        options={CITATION_DENSITY_OPTIONS}
        value={draft.citation_density ?? null}
        onChange={(citation_density) => persist({ ...draft, citation_density })}
      />

      <PillGroup<RefusalBehavior>
        label="Refusal behavior"
        hint="How conservatively the agent should treat borderline policy cases."
        options={REFUSAL_BEHAVIOR_OPTIONS}
        value={draft.refusal_behavior ?? null}
        onChange={(refusal_behavior) => persist({ ...draft, refusal_behavior })}
      />

      <PillGroup<ReasoningEffort>
        label="Default reasoning effort"
        hint="Used when a chat's model supports reasoning and the request omits an effort."
        options={REASONING_EFFORT_OPTIONS}
        value={draft.default_reasoning_effort ?? null}
        onChange={(default_reasoning_effort) =>
          persist({ ...draft, default_reasoning_effort })
        }
      />

      {submitError && (
        <Card>
          <p role="alert">{submitError}</p>
        </Card>
      )}

      <ToolUsePolicyPanel />
    </div>
  );
}

interface PillOption<T extends string> {
  id: T;
  label: string;
  hint: string;
}

function PillGroup<T extends string>({
  label,
  hint,
  options,
  value,
  onChange,
}: {
  label: string;
  hint: string;
  options: ReadonlyArray<PillOption<T>>;
  value: T | null;
  onChange: (next: T | null) => void;
}): ReactElement {
  return (
    <Card>
      <Field label={label} hint={hint}>
        <div
          className="settings-pill-group"
          role="radiogroup"
          aria-label={label}
        >
          {options.map((opt) => {
            const active = value === opt.id;
            return (
              <button
                key={opt.id}
                type="button"
                role="radio"
                aria-checked={active}
                className={classNames(
                  "settings-pill",
                  active && "settings-pill--active",
                )}
                title={opt.hint}
                onClick={() => onChange(active ? null : opt.id)}
              >
                {opt.label}
              </button>
            );
          })}
        </div>
      </Field>
    </Card>
  );
}
