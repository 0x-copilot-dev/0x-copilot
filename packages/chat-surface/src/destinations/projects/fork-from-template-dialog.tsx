// ForkFromTemplateDialog — P6.5-B1
//
// Modal that instantiates a new project from a `ProjectTemplate`. Per
// projects-extensions-prd §7.6, the dialog requires a name and lets the
// user override the snapshot's color/icon defaults before clicking Fork.
//
// Out of scope for this dialog (host can wire later if desired):
//   - Member override list  — §7.6 lists this as editable; we surface
//     the suggested member count read-only here and leave that editing
//     to a follow-up dialog so the SP-1 footprint stays small.
//   - Connector allowlist override — same rationale; the snapshot's
//     allowlist mode is summarised but not edited from this dialog.
//
// File-naming follows the kebab-case dialog convention (cf.
// `transfer-ownership-dialog.tsx`).
//
// Pure presentation; `onFork` is host-owned.
//
// SP-1 primitives:
//   - <StatusPill> for the snapshot summary

import {
  useCallback,
  useEffect,
  useState,
  type CSSProperties,
  type FormEvent,
  type ReactElement,
} from "react";

import { StatusPill, type StatusTone } from "../../shell/StatusPill";

import type { ProjectTemplateId } from "./TemplateGallery";

// ── Tokens ───────────────────────────────────────────────────────────

const PANEL_BACKGROUND = "var(--color-surface)";
const PANEL_BORDER = "var(--color-border)";
const PANEL_BORDER_STRONG = "var(--color-border-strong)";
const TEXT_PRIMARY = "var(--color-text)";
const TEXT_SECONDARY = "var(--color-text-muted)";
const TEXT_FAINT = "var(--color-text-subtle)";
const ACCENT = "var(--color-accent)";
const ACCENT_CONTRAST = "var(--color-accent-contrast)";
const DANGER = "var(--color-danger)";

// ── Public types ─────────────────────────────────────────────────────

/** Snapshot summary the dialog renders read-only for context. */
export interface ForkFromTemplateSnapshotSummary {
  readonly defaultIconEmoji: string | null;
  readonly defaultColorHue: number | null;
  readonly suggestedMemberCount: number;
  /** Tri-mode allowlist summary (§5.1):
   *   - `null` → inherit
   *   - `[]` → none
   *   - `[slug, ...]` → allowlist */
  readonly defaultConnectorAllowlist: ReadonlyArray<string> | null;
  readonly seededTodosCount: number;
  readonly seededRoutinesCount: number;
}

export interface ForkFromTemplateDialogProps {
  readonly open: boolean;
  readonly onClose: () => void;

  readonly templateId: ProjectTemplateId;
  readonly templateName: string;
  readonly snapshot: ForkFromTemplateSnapshotSummary;

  /** Fired with the user's overrides. The host performs the POST and
   *  rejects on failure; the dialog surfaces the rejection inline and
   *  remains open. On success the dialog closes itself. */
  readonly onFork: (payload: {
    readonly templateId: ProjectTemplateId;
    readonly name: string;
    readonly description: string | null;
    readonly iconEmoji: string | null;
    readonly colorHue: number | null;
  }) => Promise<void>;
}

// ── Helpers ──────────────────────────────────────────────────────────

const COLOR_HUES: ReadonlyArray<number> = [
  0, 30, 60, 120, 180, 210, 260, 300, 330,
];

const COMMON_EMOJI: ReadonlyArray<string> = [
  "📁",
  "📂",
  "🚀",
  "🎯",
  "💼",
  "🧭",
];

function connectorTone(allowlist: ReadonlyArray<string> | null): {
  tone: StatusTone;
  label: string;
} {
  if (allowlist === null) return { tone: "muted", label: "Inherit" };
  if (allowlist.length === 0) return { tone: "warning", label: "None" };
  return {
    tone: "info",
    label: `${allowlist.length} connector${allowlist.length === 1 ? "" : "s"}`,
  };
}

// ── Component ────────────────────────────────────────────────────────

export function ForkFromTemplateDialog(
  props: ForkFromTemplateDialogProps,
): ReactElement | null {
  const { open, onClose, templateId, templateName, snapshot, onFork } = props;

  const [name, setName] = useState<string>("");
  const [description, setDescription] = useState<string>("");
  const [iconEmoji, setIconEmoji] = useState<string>(
    snapshot.defaultIconEmoji ?? "📁",
  );
  const [colorHue, setColorHue] = useState<number>(
    snapshot.defaultColorHue ?? 200,
  );
  const [submitting, setSubmitting] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  // Reset every time the dialog opens.
  useEffect(() => {
    if (open) {
      setName("");
      setDescription("");
      setIconEmoji(snapshot.defaultIconEmoji ?? "📁");
      setColorHue(snapshot.defaultColorHue ?? 200);
      setSubmitting(false);
      setError(null);
    }
  }, [open, snapshot.defaultIconEmoji, snapshot.defaultColorHue]);

  const canSubmit = name.trim().length > 0 && !submitting;

  const handleSubmit = useCallback(
    async (event?: FormEvent<HTMLFormElement>): Promise<void> => {
      if (event !== undefined) event.preventDefault();
      if (!canSubmit) return;
      setSubmitting(true);
      setError(null);
      try {
        await onFork({
          templateId,
          name: name.trim(),
          description:
            description.trim().length > 0 ? description.trim() : null,
          iconEmoji,
          colorHue,
        });
        onClose();
      } catch (e) {
        const message = e instanceof Error ? e.message : "Failed to fork";
        setError(message);
        setSubmitting(false);
      }
    },
    [
      canSubmit,
      colorHue,
      description,
      iconEmoji,
      name,
      onClose,
      onFork,
      templateId,
    ],
  );

  if (!open) return null;

  const summary = connectorTone(snapshot.defaultConnectorAllowlist);

  // ── Styles ─────────────────────────────────────────────────────────
  const backdrop: CSSProperties = {
    position: "fixed",
    inset: 0,
    backgroundColor: "rgba(0,0,0,0.6)",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    zIndex: 1100,
  };
  const card: CSSProperties = {
    width: 520,
    maxWidth: "calc(100vw - 32px)",
    backgroundColor: PANEL_BACKGROUND,
    color: TEXT_PRIMARY,
    border: `1px solid ${PANEL_BORDER}`,
    borderRadius: 12,
    padding: 20,
    display: "flex",
    flexDirection: "column",
    gap: 14,
  };
  const labelStyle: CSSProperties = {
    fontSize: 12,
    color: TEXT_SECONDARY,
    fontWeight: 500,
  };
  const inputStyle: CSSProperties = {
    height: 36,
    padding: "0 12px",
    borderRadius: 8,
    border: `1px solid ${PANEL_BORDER_STRONG}`,
    backgroundColor: "transparent",
    color: TEXT_PRIMARY,
    fontSize: 13,
    outline: "none",
  };
  const textareaStyle: CSSProperties = {
    minHeight: 60,
    padding: "10px 12px",
    borderRadius: 8,
    border: `1px solid ${PANEL_BORDER_STRONG}`,
    backgroundColor: "transparent",
    color: TEXT_PRIMARY,
    fontSize: 13,
    outline: "none",
    fontFamily: "inherit",
    resize: "vertical",
  };
  const cancelStyle: CSSProperties = {
    height: 34,
    padding: "0 12px",
    borderRadius: 8,
    border: `1px solid ${PANEL_BORDER}`,
    backgroundColor: "transparent",
    color: TEXT_SECONDARY,
    fontSize: 13,
    cursor: "pointer",
  };
  const submitStyle: CSSProperties = {
    height: 34,
    padding: "0 14px",
    borderRadius: 8,
    border: "none",
    backgroundColor: ACCENT,
    color: ACCENT_CONTRAST,
    fontSize: 13,
    fontWeight: 600,
    cursor: "pointer",
    opacity: !canSubmit ? 0.6 : 1,
  };
  const buttonRow: CSSProperties = {
    display: "flex",
    justifyContent: "flex-end",
    gap: 8,
    marginTop: 4,
  };
  const previewStyle: CSSProperties = {
    width: 48,
    height: 48,
    borderRadius: 10,
    backgroundColor: `hsl(${colorHue}, 55%, 35%)`,
    color: ACCENT_CONTRAST,
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    fontSize: 22,
    flexShrink: 0,
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="fork-from-template-title"
      style={backdrop}
      data-testid="fork-from-template-dialog"
      data-template-id={templateId}
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <form style={card} onSubmit={handleSubmit}>
        <h2
          id="fork-from-template-title"
          style={{ margin: 0, fontSize: 16, fontWeight: 600 }}
        >
          Fork from &ldquo;{templateName}&rdquo;
        </h2>

        <div
          style={{
            display: "flex",
            gap: 10,
            alignItems: "center",
            flexWrap: "wrap",
            fontSize: 12,
            color: TEXT_FAINT,
          }}
          data-testid="fork-from-template-snapshot-summary"
        >
          <StatusPill
            status={summary.tone}
            label={`Connectors: ${summary.label}`}
          />
          <span>
            {snapshot.suggestedMemberCount} suggested member
            {snapshot.suggestedMemberCount === 1 ? "" : "s"}
          </span>
          <span aria-hidden="true">·</span>
          <span>
            Will seed {snapshot.seededTodosCount} todo
            {snapshot.seededTodosCount === 1 ? "" : "s"} and{" "}
            {snapshot.seededRoutinesCount} routine
            {snapshot.seededRoutinesCount === 1 ? "" : "s"}.
          </span>
        </div>

        <label style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <span style={labelStyle}>New project name</span>
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            disabled={submitting}
            maxLength={80}
            style={inputStyle}
            data-testid="fork-from-template-name-input"
            aria-label="New project name"
            placeholder="e.g. Acme Q4 renewal"
            autoFocus
          />
        </label>

        <label style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <span style={labelStyle}>Description (optional)</span>
          <textarea
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            disabled={submitting}
            maxLength={200}
            style={textareaStyle}
            data-testid="fork-from-template-description-input"
            aria-label="Description"
          />
        </label>

        <div style={{ display: "flex", gap: 14, alignItems: "center" }}>
          <div
            style={previewStyle}
            data-testid="fork-from-template-preview"
            data-color-hue={colorHue}
            aria-label="Icon preview"
          >
            {iconEmoji}
          </div>
          <div
            style={{
              display: "flex",
              flexDirection: "column",
              gap: 6,
              flex: 1,
            }}
          >
            <span style={labelStyle}>Icon override</span>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
              {COMMON_EMOJI.map((glyph) => (
                <button
                  key={glyph}
                  type="button"
                  onClick={() => setIconEmoji(glyph)}
                  disabled={submitting}
                  data-testid={`fork-from-template-icon-${glyph}`}
                  aria-label={`Use icon ${glyph}`}
                  aria-pressed={glyph === iconEmoji}
                  style={{
                    width: 30,
                    height: 30,
                    borderRadius: 8,
                    border: `1px solid ${glyph === iconEmoji ? ACCENT : PANEL_BORDER}`,
                    backgroundColor: PANEL_BACKGROUND,
                    color: TEXT_PRIMARY,
                    cursor: submitting ? "not-allowed" : "pointer",
                    fontSize: 15,
                  }}
                >
                  {glyph}
                </button>
              ))}
            </div>
          </div>
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <span style={labelStyle}>Color override</span>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
            {COLOR_HUES.map((hue) => {
              const selected = hue === colorHue;
              return (
                <button
                  key={hue}
                  type="button"
                  onClick={() => setColorHue(hue)}
                  disabled={submitting}
                  data-testid={`fork-from-template-color-${hue}`}
                  aria-label={`Use hue ${hue}`}
                  aria-pressed={selected}
                  style={{
                    width: 26,
                    height: 26,
                    borderRadius: "50%",
                    backgroundColor: `hsl(${hue}, 55%, 45%)`,
                    border: `2px solid ${selected ? ACCENT : "transparent"}`,
                    cursor: submitting ? "not-allowed" : "pointer",
                  }}
                />
              );
            })}
          </div>
        </div>

        {error !== null ? (
          <div
            role="alert"
            style={{ color: DANGER, fontSize: 12 }}
            data-testid="fork-from-template-error"
          >
            {error}
          </div>
        ) : null}

        <div style={buttonRow}>
          <button
            type="button"
            onClick={onClose}
            disabled={submitting}
            style={cancelStyle}
            data-testid="fork-from-template-cancel"
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={!canSubmit}
            style={submitStyle}
            data-testid="fork-from-template-confirm"
            aria-label="Fork project"
          >
            {submitting ? "Forking…" : "Fork project"}
          </button>
        </div>
      </form>
    </div>
  );
}
