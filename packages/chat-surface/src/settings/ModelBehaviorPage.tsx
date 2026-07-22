// <ModelBehaviorPage /> — Settings → Models & keys → Model & behavior
// (DESIGN-SPEC §4 · PRD PR-5.6, FR-5.16/5.17/5.18). Fills the "model-behavior"
// slot of `SettingsSurface`. Four blocks:
//
//   Default model    a <Select> with two optgroups — "Cloud · your keys"
//                    (from connected provider keys) and "Local · your machine"
//                    (from installed local models). Options are SUPPLIED BY THE
//                    HOST (composed from PR-5.4 provider keys + PR-5.5 local
//                    models), never hardcoded here (FR-5.16).
//   Reasoning depth  Auto · Quick · Standard · Deep (a <Select>).
//   Web access       a toggle.
//   Approval policy  the <ApprovalPolicy> block (read-only / write / on-chain-
//                    spend-destructive), relocated from the web
//                    `ToolUsePolicyPanel` (FR-5.17).
//   Spend guardrail  Monthly API cap ($, across all provider keys) + Pause-runs-
//                    at-cap toggle (FR-5.18).
//
// SUBSTRATE-AGNOSTIC + CONTROLLED. The page reflects `value` and reports each
// edit through `onChange` (a shallow `Partial<ModelBehaviorValue>`; nested
// blocks are passed whole so the host can `{...prev, ...patch}`). It NEVER
// persists — persistence + the saved baseline + the dirty computation are host
// concerns (the workspace-defaults / tool-use-policy / spend-cap wiring lands in
// the desktop-mount PR). Unsaved edits dock a SaveBar on the surface through the
// injected `controller` (FR-5.7); the two are kept distinct — this page uses the
// dirty savebar, never a one-shot toast.
//
// Colors resolve ONLY to design-system v2 tokens (via the chrome primitives).

import {
  useEffect,
  useId,
  useRef,
  type CSSProperties,
  type ReactElement,
} from "react";

import type { ReasoningDepth } from "@0x-copilot/api-types";
import { Select, TextInput, Toggle } from "@0x-copilot/design-system";

import { ApprovalPolicy, type ApprovalPolicyValue } from "./ApprovalPolicy";
import { Frow, SecTitle, SetCard, SetNote } from "./SettingsChrome";
import type { SettingsSurfaceController } from "./SettingsSurface";

// ---------------------------------------------------------------------------
// Vocabulary.
// ---------------------------------------------------------------------------

/**
 * Reasoning depth (DESIGN-SPEC §4). The wire vocabulary is the runtime's
 * canonical `ReasoningDepth` (`fast`/`balanced`/`deep`, imported from
 * api-types — the single source of truth shared with the composer + run
 * schema). "Auto" is NOT a fourth enum value: it is the `null` sentinel meaning
 * "no persisted default → the runtime baseline", so the field is
 * `ReasoningDepth | null` and the Select maps `null ↔ ""` for the DOM exactly
 * like the Default-model field. The design's Auto/Quick/Standard/Deep labels
 * sit over the values null/fast/balanced/deep.
 */
export type { ReasoningDepth };

export const REASONING_DEPTHS: ReadonlyArray<{
  readonly value: ReasoningDepth | null;
  readonly label: string;
}> = [
  { value: null, label: "Auto" },
  { value: "fast", label: "Quick" },
  { value: "balanced", label: "Standard" },
  { value: "deep", label: "Deep" },
];

/** Spend guardrail (DESIGN-SPEC §4). `monthlyCapUsd === null` means no cap. */
export interface SpendGuardrailValue {
  readonly monthlyCapUsd: number | null;
  readonly pauseAtCap: boolean;
}

export interface ModelBehaviorValue {
  /** Default model id — matches a supplied option value, or null for none. */
  readonly defaultModel: string | null;
  /** Canonical depth, or `null` for "Auto" (no persisted default). */
  readonly reasoningDepth: ReasoningDepth | null;
  readonly webAccess: boolean;
  readonly approvalPolicy: ApprovalPolicyValue;
  readonly spend: SpendGuardrailValue;
}

/**
 * A single option in the Default-model select. `value` is the persisted model
 * id (and the `<option value>`); `label` is the display name; `sub` is optional
 * mono metadata (provider / param size) shown only as an `<option>` suffix.
 */
export interface ModelBehaviorModelOption {
  readonly value: string;
  readonly label: string;
  readonly sub?: string;
  readonly disabled?: boolean;
}

/** Shallow patch: nested blocks (approvalPolicy/spend) are passed whole. */
export type ModelBehaviorPatch = Partial<ModelBehaviorValue>;

export interface ModelBehaviorPageProps {
  readonly value: ModelBehaviorValue;
  /** Report a field edit. The host merges + owns persistence and the SaveBar. */
  readonly onChange: (patch: ModelBehaviorPatch) => void;
  /**
   * Cloud models the user has keys for (PR-5.4). Rendered under the
   * "Cloud · your keys" optgroup. Empty ⇒ the optgroup is omitted.
   */
  readonly cloudModels?: readonly ModelBehaviorModelOption[];
  /**
   * Local models installed on the machine (PR-5.5). Rendered under the
   * "Local · your machine" optgroup. Empty ⇒ the optgroup is omitted.
   */
  readonly localModels?: readonly ModelBehaviorModelOption[];
  /**
   * Surface controller — a dirty section docks its SaveBar here (FR-5.7). The
   * `renderSection(slug, controller)` slot supplies it; tests pass a stub.
   */
  readonly controller: SettingsSurfaceController;
  /** Whether the section has unsaved edits (host computes vs its baseline). */
  readonly dirty?: boolean;
  readonly onSave?: () => void;
  readonly onDiscard?: () => void;
  /** A write is in flight — disables Save + shows the saving label. */
  readonly saving?: boolean;
  /** A failed save — surfaced inline as a role="alert" (distinct from load error). */
  readonly saveError?: string | null;
  /** Snapshot still loading — render a quiet skeleton, never a blank. */
  readonly loading?: boolean;
  /** Load error — surfaced as a role="alert" with a Retry affordance. */
  readonly error?: string | null;
  readonly onRetry?: () => void;
}

// ---------------------------------------------------------------------------
// Styles (token-only).
// ---------------------------------------------------------------------------

// Section wrapper: the SecTitle heading above, then the sub-cards, stacked
// (design `.set-sec` — the section title sits above its cards).
const pageStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: "var(--space-lg)",
};

const capRowStyle: CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: "var(--space-xs)",
};

const capPrefixStyle: CSSProperties = {
  fontSize: "var(--font-size-sm)",
  color: "var(--color-text-muted)",
};

const capInputStyle: CSSProperties = {
  width: 96,
};

const alertStyle: CSSProperties = {
  margin: 0,
  padding: "10px 12px",
  borderRadius: "var(--radius-md)",
  border: "1px solid var(--color-danger)",
  backgroundColor: "var(--color-danger-bg)",
  color: "var(--color-text)",
  fontSize: "var(--font-size-xs)",
};

const retryButtonStyle: CSSProperties = {
  flex: "0 0 auto",
  padding: "4px 10px",
  borderRadius: "var(--radius-sm)",
  border: "1px solid var(--color-border-strong)",
  background: "transparent",
  color: "var(--color-text)",
  font: "inherit",
  fontSize: "var(--font-size-xs)",
  fontWeight: "var(--font-weight-medium)",
  cursor: "pointer",
};

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export function ModelBehaviorPage({
  value,
  onChange,
  cloudModels = [],
  localModels = [],
  controller,
  dirty = false,
  onSave,
  onDiscard,
  saving = false,
  saveError = null,
  loading = false,
  error = null,
  onRetry,
}: ModelBehaviorPageProps): ReactElement {
  const reactId = useId();
  const defaultModelId = `${reactId}-default-model`;
  const reasoningId = `${reactId}-reasoning-depth`;
  const webAccessId = `${reactId}-web-access`;
  const capId = `${reactId}-monthly-cap`;
  const pauseId = `${reactId}-pause-at-cap`;

  // Dock / clear the SaveBar through the surface controller. The handlers are
  // held in refs so a host that passes fresh closures each render does NOT churn
  // (or loop) this effect — it re-registers only when `dirty`/`saving` flip.
  const onSaveRef = useRef(onSave);
  onSaveRef.current = onSave;
  const onDiscardRef = useRef(onDiscard);
  onDiscardRef.current = onDiscard;

  useEffect(() => {
    if (!dirty) {
      controller.setDirty(null);
      return;
    }
    controller.setDirty({
      onSave: () => onSaveRef.current?.(),
      onDiscard: () => onDiscardRef.current?.(),
      saving,
    });
    return () => controller.setDirty(null);
  }, [dirty, saving, controller]);

  const secTitle = (
    <SecTitle
      title="Model & behavior"
      description="How the agent thinks and how far it can go on its own."
    />
  );

  if (loading) {
    return (
      <div data-testid="model-behavior-page" style={pageStyle}>
        {secTitle}
        <SetCard>
          <SetNote data-testid="model-behavior-loading">
            Loading settings…
          </SetNote>
        </SetCard>
      </div>
    );
  }

  if (error !== null) {
    return (
      <div data-testid="model-behavior-page" style={pageStyle}>
        {secTitle}
        <SetCard>
          <div
            role="alert"
            data-testid="model-behavior-error"
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              gap: "var(--space-md)",
              ...alertStyle,
            }}
          >
            <span>{error}</span>
            {onRetry !== undefined ? (
              <button
                type="button"
                onClick={onRetry}
                data-testid="model-behavior-retry"
                style={retryButtonStyle}
              >
                Retry
              </button>
            ) : null}
          </div>
        </SetCard>
      </div>
    );
  }

  const hasAnyModel = cloudModels.length > 0 || localModels.length > 0;

  return (
    <div data-testid="model-behavior-page" style={pageStyle}>
      {secTitle}
      <SetCard title="Defaults">
        {saveError !== null ? (
          <p
            role="alert"
            data-testid="model-behavior-save-error"
            style={alertStyle}
          >
            {saveError}
          </p>
        ) : null}

        {/* Default model — two optgroups sourced from the host (FR-5.16). */}
        <Frow
          label="Default model"
          hint={
            hasAnyModel
              ? "Used for new runs unless a run overrides it."
              : "Add a provider key or download a local model to pick a default."
          }
          htmlFor={defaultModelId}
        >
          <Select
            id={defaultModelId}
            data-testid="default-model-select"
            aria-label="Default model"
            disabled={!hasAnyModel}
            value={value.defaultModel ?? ""}
            onChange={(event) =>
              onChange({
                defaultModel: event.currentTarget.value || null,
              })
            }
          >
            {hasAnyModel ? (
              <option value="">No default (choose per run)</option>
            ) : (
              <option value="">No models available</option>
            )}
            {cloudModels.length > 0 ? (
              <optgroup label="Cloud · your keys">
                {cloudModels.map((model) => (
                  <option
                    key={model.value}
                    value={model.value}
                    disabled={model.disabled}
                  >
                    {optionText(model)}
                  </option>
                ))}
              </optgroup>
            ) : null}
            {localModels.length > 0 ? (
              <optgroup label="Local · your machine">
                {localModels.map((model) => (
                  <option
                    key={model.value}
                    value={model.value}
                    disabled={model.disabled}
                  >
                    {optionText(model)}
                  </option>
                ))}
              </optgroup>
            ) : null}
          </Select>
        </Frow>

        {/* Reasoning depth. */}
        <Frow
          label="Reasoning depth"
          hint="How much the agent deliberates before acting."
          htmlFor={reasoningId}
        >
          <Select
            id={reasoningId}
            data-testid="reasoning-depth-select"
            aria-label="Reasoning depth"
            value={value.reasoningDepth ?? ""}
            onChange={(event) =>
              onChange({
                reasoningDepth:
                  (event.currentTarget.value as ReasoningDepth | "") || null,
              })
            }
          >
            {REASONING_DEPTHS.map((depth) => (
              <option key={depth.value ?? "auto"} value={depth.value ?? ""}>
                {depth.label}
              </option>
            ))}
          </Select>
        </Frow>

        {/* Web access. */}
        <Frow
          label="Web access"
          hint="Let the agent read from the public web during a run."
          htmlFor={webAccessId}
        >
          <Toggle
            id={webAccessId}
            data-testid="web-access-toggle"
            aria-label="Web access"
            checked={value.webAccess}
            onChange={(event) =>
              onChange({ webAccess: event.currentTarget.checked })
            }
          />
        </Frow>
      </SetCard>

      {/* Approval policy — relocated ToolUsePolicyPanel (FR-5.17). */}
      <ApprovalPolicy
        value={value.approvalPolicy}
        onChange={(approvalPolicy) => onChange({ approvalPolicy })}
      />

      {/* Spend guardrail (FR-5.18). */}
      <SetCard
        title="Spend guardrail"
        meta="Cap what the agent can spend on model API calls."
        data-testid="spend-guardrail"
      >
        <Frow
          label="Monthly API cap"
          hint="Across all provider keys. Leave blank for no cap."
          htmlFor={capId}
        >
          <span style={capRowStyle}>
            <span aria-hidden="true" style={capPrefixStyle}>
              $
            </span>
            <TextInput
              id={capId}
              data-testid="monthly-cap-input"
              aria-label="Monthly API cap in US dollars"
              type="text"
              inputMode="decimal"
              placeholder="No cap"
              style={capInputStyle}
              value={value.spend.monthlyCapUsd ?? ""}
              onChange={(event) =>
                onChange({
                  spend: {
                    ...value.spend,
                    monthlyCapUsd: parseCap(event.currentTarget.value),
                  },
                })
              }
            />
          </span>
        </Frow>

        <Frow
          label="Pause runs at cap"
          hint="Stop new runs once the monthly cap is reached."
          htmlFor={pauseId}
        >
          <Toggle
            id={pauseId}
            data-testid="pause-at-cap-toggle"
            aria-label="Pause runs at cap"
            checked={value.spend.pauseAtCap}
            onChange={(event) =>
              onChange({
                spend: {
                  ...value.spend,
                  pauseAtCap: event.currentTarget.checked,
                },
              })
            }
          />
        </Frow>
      </SetCard>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Helpers.
// ---------------------------------------------------------------------------

function optionText(model: ModelBehaviorModelOption): string {
  return model.sub !== undefined
    ? `${model.label} · ${model.sub}`
    : model.label;
}

/**
 * Parse the monthly-cap input to `number | null`. An empty / whitespace value
 * (or an unparseable one) is "no cap" (null); negatives clamp to 0 so the guard
 * can never be set to a nonsensical spend limit.
 */
function parseCap(raw: string): number | null {
  const trimmed = raw.trim();
  if (trimmed === "") return null;
  const parsed = Number(trimmed);
  if (!Number.isFinite(parsed)) return null;
  return parsed < 0 ? 0 : parsed;
}
