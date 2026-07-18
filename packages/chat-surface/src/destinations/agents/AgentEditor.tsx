// <AgentEditor /> — tabbed editor for the Agents destination.
//
// Source:
//   - docs/atlas-new-design/destinations/agents-prd.md §7.4 (editor),
//     §3.1 (wire types), §4.3/§4.4 (create + edit endpoints).
//   - cross-audit §1.6 + master §3.6 (ARIA tabs pattern).
//   - Phase 5 RoutineEditor (the tabbed-editor pattern this file mirrors —
//     same SP-1 discipline, same composer-as-instructions trick).
//
// Invariants (DRY):
//   - SP-1 primitives only (StatusPill from design-system). Composer is
//     reused from `../../composer/Composer` — there is one and only one
//     composer in chat-surface. We don't fork it.
//   - The editor handles BOTH create (no `initialValue` ⇒ defaults from
//     `AGENT_EDITOR_DEFAULTS`) and edit (host passes `initialValue`).
//     One component, two flows — no `<AgentCreatePage>` and
//     `<AgentEditPage>` duplicates.
//   - Pure presentation. `onSave` receives the assembled draft; the host
//     owns POST /v1/agents (create) or PATCH /v1/agents/<id> (edit).
//   - The "Save as version" CTA is exposed via `onSnapshot` callback (host
//     owns POST /v1/agents/<id>/versions per agents-prd §4.7). Hidden in
//     the create flow (no agent id to snapshot).
//   - ARIA tabs pattern (master §3.6): `role="tablist"` / `role="tab"` /
//     `role="tabpanel"`, arrow keys cycle, Home/End jump, tabIndex toggles.
//   - Save button shows in-flight feedback via `disabled` and label flip.
//     The host controls the in-flight state via `disabled` + a `saving`
//     hint. The editor itself does not own network state.
//
// Local types mirror agents-prd §3.1. They live here until
// `packages/api-types/src/agents.ts` lands (P8-A5); at that point this
// file replaces the local declarations with imports and the public
// shape is unchanged.

import {
  useCallback,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactElement,
  type ReactNode,
} from "react";

import { StatusPill } from "@0x-copilot/design-system";

import { Composer } from "../../composer/Composer";

// ===========================================================================
// Local types — mirror agents-prd §3.1 (see file header).
// ===========================================================================

export type AgentOrigin = "system" | "community" | "custom";
export type AgentStatus = "installed" | "available" | "disabled" | "draft";
export type AgentAutonomy = "manual_approval" | "auto_apply";
export type AgentReasoningDepth = "fast" | "balanced" | "deep";

export interface AgentEditorPermissions {
  readonly autonomy: AgentAutonomy;
  readonly max_tool_calls_per_run: number;
  readonly max_output_tokens: number;
  readonly read_only: boolean;
  readonly blocked_tool_families: ReadonlyArray<string>;
}

export interface AgentEditorModelDefault {
  readonly model_id: string;
  readonly reasoning_depth: AgentReasoningDepth;
}

/**
 * Full editor draft — the payload `onSave` receives. Matches agents-prd
 * §3.1 `Agent` minus server-assigned fields (`id`, `tenant_id`, `version`,
 * `owner_user_id`, `created_at`, `updated_at`, `viewer_*`). `status` is
 * also editor-controlled per §4.4.
 */
export interface AgentEditorValue {
  readonly name: string;
  readonly slug: string;
  readonly description: string;
  readonly icon_emoji: string;
  readonly color_hue: number;
  readonly origin: AgentOrigin;
  readonly status: AgentStatus;
  readonly instructions: string;
  readonly model_default: AgentEditorModelDefault;
  readonly skills: ReadonlyArray<string>;
  readonly connectors_default: ReadonlyArray<string>;
  readonly permissions: AgentEditorPermissions;
}

// ===========================================================================
// Defaults — line up with agents-prd §7.4 + §4.3 create defaults.
// ===========================================================================

const DEFAULT_PERMISSIONS: AgentEditorPermissions = {
  autonomy: "manual_approval",
  max_tool_calls_per_run: 50,
  max_output_tokens: 32_000,
  read_only: false,
  blocked_tool_families: [],
};

export const AGENT_EDITOR_DEFAULTS: AgentEditorValue = {
  name: "",
  slug: "",
  description: "",
  icon_emoji: "🤖",
  color_hue: 220,
  origin: "custom",
  status: "draft",
  instructions: "",
  model_default: {
    model_id: "anthropic:claude-sonnet-4-7",
    reasoning_depth: "balanced",
  },
  skills: [],
  connectors_default: [],
  permissions: DEFAULT_PERMISSIONS,
};

// ===========================================================================
// Tab IDs + ARIA wiring (per task brief: Identity / Behavior / Connectors /
// Skills / Permissions). Agents-prd §7.4 also names "Memory" but flags it
// as Phase 11 forward-compat — out of scope for P8-B2.
// ===========================================================================

export type AgentEditorTabId =
  | "identity"
  | "behavior"
  | "connectors"
  | "skills"
  | "permissions";

const TAB_ORDER: ReadonlyArray<AgentEditorTabId> = [
  "identity",
  "behavior",
  "connectors",
  "skills",
  "permissions",
];

const TAB_LABEL: Readonly<Record<AgentEditorTabId, string>> = {
  identity: "Identity",
  behavior: "Behavior",
  connectors: "Connectors",
  skills: "Skills",
  permissions: "Permissions",
};

// ===========================================================================
// Public props.
// ===========================================================================

/** Save-button feedback state — drives the footer label + disabled flag. */
export type AgentEditorSaveState = "idle" | "saving" | "saved" | "error";

export interface AgentEditorProps {
  /**
   * Initial draft. When undefined, the editor renders the
   * `AGENT_EDITOR_DEFAULTS` shape (create flow).
   */
  readonly initialValue?: AgentEditorValue;
  /**
   * Save handler. Receives the full assembled draft. Host owns the
   * POST /v1/agents (create) or PATCH /v1/agents/<id> (edit) transport.
   */
  readonly onSave: (value: AgentEditorValue) => void;
  /**
   * Cancel handler. Host owns the dirty-prompt confirm guard, if any.
   */
  readonly onCancel?: () => void;
  /**
   * Optional snapshot handler — fires when the user clicks
   * "Save as version" in the footer. Hidden in the create flow (no
   * agent id to snapshot against yet).
   */
  readonly onSnapshot?: () => void;
  /**
   * Which tab to render first. Defaults to "identity".
   */
  readonly initialTab?: AgentEditorTabId;
  /**
   * Connectors the owner has connected. Pure data; host fetches
   * `/v1/me/connectors` and passes the list in.
   */
  readonly availableConnectors?: ReadonlyArray<{
    readonly connector_id: string;
    readonly label: string;
  }>;
  /**
   * Skills available in the tenant. Pure data; host fetches
   * `/v1/tools?filter[kind]=skill` (or backend skills until Phase 9).
   */
  readonly availableSkills?: ReadonlyArray<{
    readonly skill_id: string;
    readonly label: string;
  }>;
  /**
   * Models available. Defaulted to a small built-in list so the editor
   * is usable in tests / Storybook without a host. Host should pass a
   * real list driven by `/v1/models`.
   */
  readonly availableModels?: ReadonlyArray<{
    readonly model_id: string;
    readonly label: string;
  }>;
  /**
   * Save-button feedback. Drives the footer label and disabled state.
   * Defaults to "idle".
   */
  readonly saveState?: AgentEditorSaveState;
  /**
   * Disabled state — used while a save is in flight. Tabs remain
   * navigable so users can re-read fields but inputs lock.
   */
  readonly disabled?: boolean;
}

const FALLBACK_MODELS: ReadonlyArray<{
  readonly model_id: string;
  readonly label: string;
}> = [
  { model_id: "anthropic:claude-opus-4-7", label: "Claude Opus 4.7" },
  { model_id: "anthropic:claude-sonnet-4-7", label: "Claude Sonnet 4.7" },
  { model_id: "anthropic:claude-haiku-4-7", label: "Claude Haiku 4.7" },
];

// ===========================================================================
// Component.
// ===========================================================================

export function AgentEditor(props: AgentEditorProps): ReactElement {
  const {
    initialValue,
    onSave,
    onCancel,
    onSnapshot,
    initialTab = "identity",
    availableConnectors = [],
    availableSkills = [],
    availableModels = FALLBACK_MODELS,
    saveState = "idle",
    disabled = false,
  } = props;

  const [value, setValue] = useState<AgentEditorValue>(
    () => initialValue ?? AGENT_EDITOR_DEFAULTS,
  );
  const [activeTab, setActiveTab] = useState<AgentEditorTabId>(initialTab);
  const tabRefs = useRef<Record<AgentEditorTabId, HTMLButtonElement | null>>({
    identity: null,
    behavior: null,
    connectors: null,
    skills: null,
    permissions: null,
  });

  const isDraft = useMemo(
    () =>
      value.name.trim().length === 0 || value.instructions.trim().length === 0,
    [value],
  );

  // -- Field setters ---------------------------------------------------------

  const updateField = useCallback(
    <K extends keyof AgentEditorValue>(key: K, next: AgentEditorValue[K]) => {
      setValue((prev) => ({ ...prev, [key]: next }));
    },
    [],
  );

  const updateModel = useCallback(
    <K extends keyof AgentEditorModelDefault>(
      key: K,
      next: AgentEditorModelDefault[K],
    ) => {
      setValue((prev) => ({
        ...prev,
        model_default: { ...prev.model_default, [key]: next },
      }));
    },
    [],
  );

  const updatePermissions = useCallback(
    <K extends keyof AgentEditorPermissions>(
      key: K,
      next: AgentEditorPermissions[K],
    ) => {
      setValue((prev) => ({
        ...prev,
        permissions: { ...prev.permissions, [key]: next },
      }));
    },
    [],
  );

  // -- Tab navigation (arrow keys / Home / End) ------------------------------

  const focusTab = useCallback((tab: AgentEditorTabId) => {
    setActiveTab(tab);
    const node = tabRefs.current[tab];
    node?.focus();
  }, []);

  const onTabKeyDown = useCallback(
    (event: ReactKeyboardEvent<HTMLDivElement>) => {
      const idx = TAB_ORDER.indexOf(activeTab);
      if (idx < 0) return;
      if (event.key === "ArrowRight") {
        event.preventDefault();
        focusTab(TAB_ORDER[(idx + 1) % TAB_ORDER.length]!);
      } else if (event.key === "ArrowLeft") {
        event.preventDefault();
        focusTab(TAB_ORDER[(idx - 1 + TAB_ORDER.length) % TAB_ORDER.length]!);
      } else if (event.key === "Home") {
        event.preventDefault();
        focusTab(TAB_ORDER[0]!);
      } else if (event.key === "End") {
        event.preventDefault();
        focusTab(TAB_ORDER[TAB_ORDER.length - 1]!);
      }
    },
    [activeTab, focusTab],
  );

  // -- Save / snapshot -------------------------------------------------------

  const handleSave = useCallback(() => {
    onSave(value);
  }, [onSave, value]);

  // -- Footer label tracks `saveState` (visual feedback per task brief) ------

  const saveLabel = useMemo(() => {
    if (saveState === "saving") return "Saving…";
    if (saveState === "saved") return "Saved ✓";
    if (saveState === "error") return "Retry save";
    return "Save";
  }, [saveState]);

  const saveDisabled = disabled || saveState === "saving";

  return (
    <div
      style={containerStyle}
      data-testid="agent-editor"
      data-status={isDraft ? "draft" : "ready"}
      data-save-state={saveState}
      data-active-tab={activeTab}
    >
      <div style={headerStyle}>
        <StatusPill
          tone={isDraft ? "idle" : "ready"}
          label={isDraft ? "Draft" : "Ready"}
          data-testid="agent-editor-status-pill"
        />
        <input
          type="text"
          value={value.name}
          maxLength={80}
          onChange={(e) => updateField("name", e.target.value)}
          placeholder="Agent name"
          aria-label="Agent name (header)"
          data-testid="agent-editor-name-header"
          style={nameHeaderInputStyle}
          disabled={disabled}
        />
        <button
          type="button"
          onClick={onCancel}
          data-testid="agent-editor-cancel"
          style={secondaryButtonStyle}
          disabled={disabled}
        >
          Cancel
        </button>
        <button
          type="button"
          onClick={handleSave}
          data-testid="agent-editor-save"
          data-save-state={saveState}
          style={primaryButtonStyle(saveState)}
          disabled={saveDisabled}
          aria-live="polite"
        >
          {saveLabel}
        </button>
      </div>

      <div
        role="tablist"
        aria-label="Agent editor"
        style={tabStripStyle}
        onKeyDown={onTabKeyDown}
      >
        {TAB_ORDER.map((tab) => (
          <button
            key={tab}
            ref={(node) => {
              tabRefs.current[tab] = node;
            }}
            type="button"
            role="tab"
            id={`agent-editor-tab-${tab}`}
            aria-selected={activeTab === tab}
            aria-controls={`agent-editor-tabpanel-${tab}`}
            tabIndex={activeTab === tab ? 0 : -1}
            onClick={() => setActiveTab(tab)}
            data-testid={`agent-editor-tab-${tab}`}
            style={tabButtonStyle(activeTab === tab)}
          >
            {TAB_LABEL[tab]}
          </button>
        ))}
      </div>

      <div
        role="tabpanel"
        id={`agent-editor-tabpanel-${activeTab}`}
        aria-labelledby={`agent-editor-tab-${activeTab}`}
        data-testid={`agent-editor-tabpanel-${activeTab}`}
      >
        {activeTab === "identity" ? (
          <IdentityTab
            value={value}
            disabled={disabled}
            onChangeName={(v) => updateField("name", v)}
            onChangeSlug={(v) => updateField("slug", v)}
            onChangeDescription={(v) => updateField("description", v)}
            onChangeIcon={(v) => updateField("icon_emoji", v)}
            onChangeHue={(v) => updateField("color_hue", v)}
          />
        ) : null}
        {activeTab === "behavior" ? (
          <BehaviorTab
            value={value}
            availableModels={availableModels}
            disabled={disabled}
            onChangeInstructions={(v) => updateField("instructions", v)}
            onChangeModelId={(v) => updateModel("model_id", v)}
            onChangeReasoningDepth={(v) => updateModel("reasoning_depth", v)}
          />
        ) : null}
        {activeTab === "connectors" ? (
          <ConnectorsTab
            selected={value.connectors_default}
            available={availableConnectors}
            disabled={disabled}
            onChange={(connectors) =>
              updateField("connectors_default", connectors)
            }
          />
        ) : null}
        {activeTab === "skills" ? (
          <SkillsTab
            selected={value.skills}
            available={availableSkills}
            disabled={disabled}
            onChange={(skills) => updateField("skills", skills)}
          />
        ) : null}
        {activeTab === "permissions" ? (
          <PermissionsTab
            permissions={value.permissions}
            disabled={disabled}
            onChange={updatePermissions}
          />
        ) : null}
      </div>

      {onSnapshot !== undefined ? (
        <div style={footerStyle} data-testid="agent-editor-footer">
          <span style={hintStyle}>
            Snapshot the live record as an immutable version. Routines pinning
            this agent can target the snapshot.
          </span>
          <button
            type="button"
            onClick={onSnapshot}
            data-testid="agent-editor-save-as-version"
            style={secondaryButtonStyle}
            disabled={disabled}
          >
            Save as version
          </button>
        </div>
      ) : null}
    </div>
  );
}

// ===========================================================================
// Identity tab.
// ===========================================================================

interface IdentityTabProps {
  readonly value: AgentEditorValue;
  readonly disabled: boolean;
  readonly onChangeName: (v: string) => void;
  readonly onChangeSlug: (v: string) => void;
  readonly onChangeDescription: (v: string) => void;
  readonly onChangeIcon: (v: string) => void;
  readonly onChangeHue: (v: number) => void;
}

function IdentityTab(props: IdentityTabProps): ReactElement {
  const {
    value,
    disabled,
    onChangeName,
    onChangeSlug,
    onChangeDescription,
    onChangeIcon,
    onChangeHue,
  } = props;
  return (
    <FieldGroup>
      <Field label="Name" hint="Required, up to 80 characters.">
        <input
          type="text"
          value={value.name}
          onChange={(e) => onChangeName(e.target.value)}
          maxLength={80}
          aria-label="Name"
          data-testid="agent-editor-name-input"
          style={textInputStyle}
          disabled={disabled}
        />
      </Field>
      <Field label="Slug" hint="URL-safe identifier. Auto-generated if blank.">
        <input
          type="text"
          value={value.slug}
          onChange={(e) => onChangeSlug(e.target.value)}
          maxLength={80}
          aria-label="Slug"
          data-testid="agent-editor-slug-input"
          style={textInputStyle}
          disabled={disabled}
          placeholder={value.name.toLowerCase().replace(/\s+/g, "-")}
        />
      </Field>
      <Field
        label="Description"
        hint="One sentence. Shown on the gallery card."
      >
        <textarea
          value={value.description}
          onChange={(e) => onChangeDescription(e.target.value)}
          maxLength={280}
          rows={2}
          aria-label="Description"
          data-testid="agent-editor-description-input"
          style={{ ...textInputStyle, height: "auto", padding: "8px 10px" }}
          disabled={disabled}
        />
      </Field>
      <Field label="Icon" hint="Single emoji.">
        <input
          type="text"
          value={value.icon_emoji}
          onChange={(e) => onChangeIcon(e.target.value)}
          maxLength={4}
          aria-label="Icon emoji"
          data-testid="agent-editor-icon-input"
          style={{ ...textInputStyle, width: 64, textAlign: "center" }}
          disabled={disabled}
        />
      </Field>
      <Field label="Color hue" hint="HSL hue 0–359.">
        <input
          type="range"
          min={0}
          max={359}
          value={value.color_hue}
          onChange={(e) => onChangeHue(Number.parseInt(e.target.value, 10))}
          aria-label="Color hue"
          data-testid="agent-editor-hue-input"
          disabled={disabled}
          style={{ width: 200 }}
        />
        <span
          aria-hidden="true"
          data-testid="agent-editor-hue-swatch"
          style={{
            display: "inline-block",
            width: 20,
            height: 20,
            borderRadius: 4,
            background: `hsl(${value.color_hue}, 60%, 50%)`,
            verticalAlign: "middle",
            marginLeft: 8,
          }}
        />
      </Field>
    </FieldGroup>
  );
}

// ===========================================================================
// Behavior tab — instructions (Composer) + model + reasoning depth.
// ===========================================================================

interface BehaviorTabProps {
  readonly value: AgentEditorValue;
  readonly availableModels: ReadonlyArray<{
    readonly model_id: string;
    readonly label: string;
  }>;
  readonly disabled: boolean;
  readonly onChangeInstructions: (v: string) => void;
  readonly onChangeModelId: (v: string) => void;
  readonly onChangeReasoningDepth: (v: AgentReasoningDepth) => void;
}

function BehaviorTab(props: BehaviorTabProps): ReactElement {
  const {
    value,
    availableModels,
    disabled,
    onChangeInstructions,
    onChangeModelId,
    onChangeReasoningDepth,
  } = props;

  // Composer is the chat-surface "submit-on-Enter" surface; for the
  // Instructions field we treat Send as "commit the buffered text".
  // SP-1: we DO NOT fork the composer — we reuse it. The Composer
  // owns its own textarea state; we receive `text` on send and persist
  // it to the editor draft. Pressing Enter (or clicking Send) commits.
  const handleSend = useCallback(
    (text: string) => {
      onChangeInstructions(text);
    },
    [onChangeInstructions],
  );

  // Token count — pure client estimator (1 token ≈ 4 chars for English).
  const tokenEstimate = useMemo(
    () => Math.ceil(value.instructions.length / 4),
    [value.instructions],
  );

  return (
    <div data-testid="agent-editor-behavior">
      <Field
        label="Instructions"
        hint={`Multi-line. ${tokenEstimate.toLocaleString()} tokens estimated. The agent uses these instructions every run.`}
      >
        <Composer
          onSend={handleSend}
          placeholder="Describe what this agent should do every run…"
          disabled={disabled}
        />
        {value.instructions.length > 0 ? (
          <pre
            data-testid="agent-editor-instructions-preview"
            style={instructionsPreviewStyle}
            aria-live="polite"
          >
            {value.instructions}
          </pre>
        ) : null}
      </Field>
      <Field
        label="Model"
        hint="Default model used at run-start. Users may override."
      >
        <select
          value={value.model_default.model_id}
          onChange={(e) => onChangeModelId(e.target.value)}
          aria-label="Model"
          data-testid="agent-editor-model-input"
          style={selectStyle}
          disabled={disabled}
        >
          {availableModels.map((m) => (
            <option key={m.model_id} value={m.model_id}>
              {m.label}
            </option>
          ))}
        </select>
      </Field>
      <Field
        label="Reasoning depth"
        hint="Fast / Balanced / Deep. Tunes the runtime."
      >
        <div
          role="radiogroup"
          aria-label="Reasoning depth"
          data-testid="agent-editor-depth"
          style={radioGroupStyle}
        >
          {(
            ["fast", "balanced", "deep"] as ReadonlyArray<AgentReasoningDepth>
          ).map((d) => (
            <label
              key={d}
              style={radioLabelStyle}
              data-testid={`agent-editor-depth-${d}`}
            >
              <input
                type="radio"
                name="agent-depth"
                value={d}
                checked={value.model_default.reasoning_depth === d}
                onChange={() => onChangeReasoningDepth(d)}
                disabled={disabled}
              />
              <span>{d}</span>
            </label>
          ))}
        </div>
      </Field>
    </div>
  );
}

// ===========================================================================
// Connectors tab.
// ===========================================================================

interface ConnectorsTabProps {
  readonly selected: ReadonlyArray<string>;
  readonly available: ReadonlyArray<{
    readonly connector_id: string;
    readonly label: string;
  }>;
  readonly disabled: boolean;
  readonly onChange: (next: ReadonlyArray<string>) => void;
}

function ConnectorsTab(props: ConnectorsTabProps): ReactElement {
  const { selected, available, disabled, onChange } = props;
  const toggle = useCallback(
    (id: string) => {
      if (selected.includes(id)) {
        onChange(selected.filter((s) => s !== id));
      } else {
        onChange([...selected, id]);
      }
    },
    [selected, onChange],
  );
  return (
    <div data-testid="agent-editor-connectors">
      <p style={hintStyle}>
        Default connector scope. Users installing the agent may narrow further;
        agents cannot widen beyond the owner's grants.
      </p>
      {available.length === 0 ? (
        <p
          data-testid="agent-editor-connectors-empty"
          role="status"
          style={emptyStyle}
        >
          No connectors available.
        </p>
      ) : (
        <ul style={listStyle}>
          {available.map((conn) => {
            const isOn = selected.includes(conn.connector_id);
            return (
              <li
                key={conn.connector_id}
                style={rowItemStyle}
                data-testid={`agent-editor-connector-${conn.connector_id}`}
              >
                <label style={radioLabelStyle}>
                  <input
                    type="checkbox"
                    checked={isOn}
                    onChange={() => toggle(conn.connector_id)}
                    aria-label={`Connector ${conn.label}`}
                    data-testid={`agent-editor-connector-${conn.connector_id}-toggle`}
                    disabled={disabled}
                  />
                  <span>{conn.label}</span>
                </label>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

// ===========================================================================
// Skills tab.
// ===========================================================================

interface SkillsTabProps {
  readonly selected: ReadonlyArray<string>;
  readonly available: ReadonlyArray<{
    readonly skill_id: string;
    readonly label: string;
  }>;
  readonly disabled: boolean;
  readonly onChange: (next: ReadonlyArray<string>) => void;
}

function SkillsTab(props: SkillsTabProps): ReactElement {
  const { selected, available, disabled, onChange } = props;
  const toggle = useCallback(
    (id: string) => {
      if (selected.includes(id)) {
        onChange(selected.filter((s) => s !== id));
      } else {
        onChange([...selected, id]);
      }
    },
    [selected, onChange],
  );
  return (
    <div data-testid="agent-editor-skills">
      <p style={hintStyle}>
        Skills the agent may invoke. Per agents-prd §7.4, until Phase 9 lands
        this list reads from <code>services/backend/skills</code> directly.
      </p>
      {available.length === 0 ? (
        <p
          data-testid="agent-editor-skills-empty"
          role="status"
          style={emptyStyle}
        >
          No skills available.
        </p>
      ) : (
        <ul style={listStyle}>
          {available.map((skill) => {
            const isOn = selected.includes(skill.skill_id);
            return (
              <li
                key={skill.skill_id}
                style={rowItemStyle}
                data-testid={`agent-editor-skill-${skill.skill_id}`}
              >
                <label style={radioLabelStyle}>
                  <input
                    type="checkbox"
                    checked={isOn}
                    onChange={() => toggle(skill.skill_id)}
                    aria-label={`Skill ${skill.label}`}
                    data-testid={`agent-editor-skill-${skill.skill_id}-toggle`}
                    disabled={disabled}
                  />
                  <span>{skill.label}</span>
                </label>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

// ===========================================================================
// Permissions tab.
// ===========================================================================

interface PermissionsTabProps {
  readonly permissions: AgentEditorPermissions;
  readonly disabled: boolean;
  readonly onChange: <K extends keyof AgentEditorPermissions>(
    key: K,
    next: AgentEditorPermissions[K],
  ) => void;
}

const AUTONOMY_LABEL: Readonly<Record<AgentAutonomy, string>> = {
  manual_approval: "Manual approval (user confirms each side-effect)",
  auto_apply: "Auto-apply (side-effects fire without confirmation)",
};

function PermissionsTab(props: PermissionsTabProps): ReactElement {
  const { permissions, disabled, onChange } = props;

  const [blockedRaw, setBlockedRaw] = useState(
    permissions.blocked_tool_families.join(", "),
  );

  const commitBlocked = useCallback(
    (next: string) => {
      setBlockedRaw(next);
      const list = next
        .split(",")
        .map((s) => s.trim())
        .filter((s) => s.length > 0);
      onChange("blocked_tool_families", list);
    },
    [onChange],
  );

  return (
    <div data-testid="agent-editor-permissions">
      <FieldGroup>
        <Field
          label="Autonomy"
          hint="Agents-prd §3.1 AgentPermissions.autonomy."
        >
          <div
            role="radiogroup"
            aria-label="Autonomy"
            data-testid="agent-editor-autonomy"
            style={radioGroupStyle}
          >
            {(
              ["manual_approval", "auto_apply"] as ReadonlyArray<AgentAutonomy>
            ).map((a) => (
              <label
                key={a}
                style={radioLabelStyle}
                data-testid={`agent-editor-autonomy-${a}`}
              >
                <input
                  type="radio"
                  name="agent-autonomy"
                  value={a}
                  checked={permissions.autonomy === a}
                  onChange={() => onChange("autonomy", a)}
                  disabled={disabled}
                />
                <span>{AUTONOMY_LABEL[a]}</span>
              </label>
            ))}
          </div>
        </Field>
        <Field
          label="Read-only"
          hint="Restricts ALL connectors to read scope at fire time."
        >
          <label
            style={radioLabelStyle}
            data-testid="agent-editor-read-only-toggle"
          >
            <input
              type="checkbox"
              checked={permissions.read_only}
              onChange={(e) => onChange("read_only", e.target.checked)}
              disabled={disabled}
            />
            <span>Force read-only at fire time</span>
          </label>
        </Field>
        <Field label="Max tool calls per run" hint="0 = no cap.">
          <input
            type="number"
            min={0}
            max={10_000}
            value={permissions.max_tool_calls_per_run}
            onChange={(e) =>
              onChange(
                "max_tool_calls_per_run",
                Number.parseInt(e.target.value || "0", 10),
              )
            }
            aria-label="Max tool calls per run"
            data-testid="agent-editor-max-tool-calls"
            style={numberInputStyle}
            disabled={disabled}
          />
        </Field>
        <Field
          label="Max output tokens"
          hint="Hard upper bound on output tokens per run."
        >
          <input
            type="number"
            min={0}
            max={1_000_000}
            value={permissions.max_output_tokens}
            onChange={(e) =>
              onChange(
                "max_output_tokens",
                Number.parseInt(e.target.value || "0", 10),
              )
            }
            aria-label="Max output tokens"
            data-testid="agent-editor-max-output-tokens"
            style={numberInputStyle}
            disabled={disabled}
          />
        </Field>
        <Field
          label="Blocked tool families"
          hint="Comma-separated tool family names (e.g. filesystem, network)."
        >
          <input
            type="text"
            value={blockedRaw}
            onChange={(e) => commitBlocked(e.target.value)}
            aria-label="Blocked tool families"
            data-testid="agent-editor-blocked-tool-families"
            placeholder="filesystem, network"
            style={textInputStyle}
            disabled={disabled}
          />
        </Field>
      </FieldGroup>
    </div>
  );
}

// ===========================================================================
// Layout primitives — local, kept tiny on purpose.
// ===========================================================================

function FieldGroup(props: { readonly children: ReactNode }): ReactElement {
  return <div style={fieldGroupStyle}>{props.children}</div>;
}

function Field(props: {
  readonly label: string;
  readonly hint?: string;
  readonly children: ReactNode;
}): ReactElement {
  return (
    <div style={fieldStyle}>
      <label style={labelStyle}>{props.label}</label>
      {props.children}
      {props.hint !== undefined ? <p style={hintStyle}>{props.hint}</p> : null}
    </div>
  );
}

// ===========================================================================
// Styles.
// ===========================================================================

const containerStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 12,
  padding: 16,
  background: "var(--color-bg)",
  color: "var(--color-text)",
  border: "1px solid var(--color-border)",
  borderRadius: 10,
  boxSizing: "border-box",
};

const headerStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 12,
  flexWrap: "wrap",
};

const nameHeaderInputStyle: CSSProperties = {
  flex: 1,
  minWidth: 200,
  height: 32,
  padding: "0 10px",
  borderRadius: 6,
  border: "1px solid var(--color-border)",
  background: "transparent",
  color: "var(--color-text)",
  fontSize: "var(--font-size-md)",
  fontWeight: 600,
};

const tabStripStyle: CSSProperties = {
  display: "flex",
  gap: 0,
  borderBottom: "1px solid var(--color-border)",
};

const tabButtonStyle = (selected: boolean): CSSProperties => ({
  display: "inline-flex",
  alignItems: "center",
  background: "transparent",
  border: "none",
  borderBottom: `2px solid ${selected ? "var(--color-accent)" : "transparent"}`,
  color: selected ? "var(--color-text)" : "var(--color-text-muted)",
  padding: "8px 14px",
  fontSize: "var(--font-size-sm)",
  fontFamily: "inherit",
  cursor: "pointer",
});

const fieldGroupStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 14,
  padding: "12px 0",
};

const fieldStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 6,
};

const labelStyle: CSSProperties = {
  fontSize: "var(--font-size-xs)",
  fontWeight: 600,
  color: "var(--color-text-muted)",
};

const hintStyle: CSSProperties = {
  margin: "4px 0 0 0",
  fontSize: "var(--font-size-xs)",
  color: "var(--color-text-muted)",
  fontStyle: "italic",
};

const textInputStyle: CSSProperties = {
  height: 30,
  padding: "0 10px",
  borderRadius: 6,
  border: "1px solid var(--color-border)",
  background: "transparent",
  color: "var(--color-text)",
  fontSize: "var(--font-size-sm)",
  fontFamily: "inherit",
  boxSizing: "border-box",
};

const numberInputStyle: CSSProperties = {
  ...textInputStyle,
  width: 120,
};

const selectStyle: CSSProperties = {
  height: 30,
  padding: "0 8px",
  borderRadius: 6,
  border: "1px solid var(--color-border)",
  background: "transparent",
  color: "var(--color-text)",
  fontSize: "var(--font-size-sm)",
  fontFamily: "inherit",
};

const primaryButtonStyle = (
  saveState: AgentEditorSaveState,
): CSSProperties => ({
  background:
    saveState === "saved"
      ? "var(--color-success, #16a34a)"
      : saveState === "error"
        ? "var(--color-danger, #dc2626)"
        : "var(--color-accent)",
  color: "var(--color-bg)",
  border: "none",
  borderRadius: 6,
  padding: "6px 14px",
  fontSize: "var(--font-size-sm)",
  fontWeight: 600,
  cursor: saveState === "saving" ? "wait" : "pointer",
});

const secondaryButtonStyle: CSSProperties = {
  background: "transparent",
  color: "var(--color-text)",
  border: "1px solid var(--color-border)",
  borderRadius: 6,
  padding: "5px 12px",
  fontSize: "var(--font-size-sm)",
  cursor: "pointer",
};

const radioGroupStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 6,
};

const radioLabelStyle: CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: 8,
  fontSize: "var(--font-size-sm)",
  color: "var(--color-text)",
};

const emptyStyle: CSSProperties = {
  margin: "8px 0",
  fontSize: "var(--font-size-xs)",
  color: "var(--color-text-muted)",
};

const listStyle: CSSProperties = {
  listStyle: "none",
  margin: 0,
  padding: 0,
  display: "flex",
  flexDirection: "column",
  gap: 4,
};

const rowItemStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  padding: "4px 0",
};

const instructionsPreviewStyle: CSSProperties = {
  margin: "8px 0 0 0",
  padding: "8px 10px",
  borderRadius: 6,
  border: "1px solid var(--color-border)",
  background: "var(--color-bg-elevated)",
  color: "var(--color-text)",
  fontSize: "var(--font-size-xs)",
  fontFamily: "inherit",
  whiteSpace: "pre-wrap",
  maxHeight: 200,
  overflow: "auto",
};

const footerStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  gap: 12,
  padding: "10px 0 0 0",
  borderTop: "1px solid var(--color-border)",
};
