// Privacy & retention — Settings → Data & privacy (DESIGN-SPEC §4 · PRD PR-5.7).
//
//   * Local-history note — "every run/step recorded to local history; full
//     record on the Activity page" (SetNote) + an "Open Activity" jump.
//   * Keep run history for — Forever / 90 / 30 / 7 days (Select). Controlled +
//     optimistic (like Appearance): the change is reported through
//     `onRetentionChange` and the host persists it — this page has NO dirty
//     savebar (FR-5.7).
//   * Memory — a toggle ("remember details across chats") + "Review N memories →"
//     which routes via `onReviewMemories` (a host nav callback).
//   * Export everything — a one-shot host callback (`onExport`) that writes a
//     full copy to `~/copilot/export`; the page fires a toast, never the
//     savebar (FR-5.7).
//   * Delete all history — DESTRUCTIVE. Gated behind a typed confirmation
//     (FR-5.20): the danger button stays disabled until the confirm phrase is
//     typed, and `onDeleteAll` is NEVER invoked automatically.
//
// SUBSTRATE-AGNOSTIC. This is a *controlled, presentation-only* section. It
// never touches the filesystem, run history, routing, or storage: retention /
// memory are controlled props, and export / delete / activity / review are
// injected HOST callbacks (chat-surface stays framework-agnostic — the host,
// e.g. the desktop shell, performs the actual fs write and cascade delete).
//
// Colors resolve ONLY to design-system v2 tokens; danger actions use the
// semantic ember/`--color-danger` tokens (single-accent discipline, §0).

import {
  useCallback,
  useId,
  useState,
  type ChangeEvent,
  type CSSProperties,
  type ReactElement,
} from "react";

import { Button, Select, TextInput, Toggle } from "@0x-copilot/design-system";

import { Frow, SecHead, SetCard, SetNote } from "./SettingsChrome";

// ---------------------------------------------------------------------------
// Vocabulary. The component works in the DESIGN-SPEC §4 vocabulary; the host
// maps it to/from whatever retention contract it persists against.
// ---------------------------------------------------------------------------

/** "Keep run history for" — DESIGN-SPEC §4 order (Forever first). */
export type RetentionChoice = "forever" | "90d" | "30d" | "7d";

export const RETENTION_OPTIONS: ReadonlyArray<{
  readonly value: RetentionChoice;
  readonly label: string;
}> = [
  { value: "forever", label: "Forever" },
  { value: "90d", label: "90 days" },
  { value: "30d", label: "30 days" },
  { value: "7d", label: "7 days" },
];

/** Default export destination surfaced in the hint (DESIGN-SPEC §4). */
export const PRIVACY_EXPORT_PATH = "~/copilot/export";

/**
 * The phrase a user must type to arm the destructive "Delete all history"
 * action (FR-5.20). Compared case-insensitively after trimming. Exported so
 * the host and tests share one source of truth for the confirm gate.
 */
export const PRIVACY_DELETE_CONFIRM_PHRASE = "delete all history";

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface PrivacyPageProps {
  /** Current "Keep run history for" selection. */
  readonly retention: RetentionChoice;
  /**
   * Report a retention change. Optimistic — the host persists it (US-5.6:
   * "the value persists"); there is no savebar on this page.
   */
  readonly onRetentionChange: (next: RetentionChoice) => void;

  /** Whether the agent remembers details across chats. */
  readonly memoryEnabled: boolean;
  /** Report a memory-capture toggle. */
  readonly onMemoryToggle: (next: boolean) => void;
  /** Count of saved memories (drives the "Review N memories →" affordance). */
  readonly memoryCount: number;
  /** Route to the memory list (host nav callback). */
  readonly onReviewMemories: () => void;

  /** Route to the Activity page (host nav callback). */
  readonly onOpenActivity: () => void;

  /**
   * Start the "Export everything" flow. A one-shot host callback that writes a
   * full copy to {@link exportPath}. May be async; the page awaits it, then
   * fires `onToast`. NEVER the dirty savebar (FR-5.7).
   */
  readonly onExport: () => void | Promise<void>;

  /**
   * Execute the destructive "Delete all history". Only ever invoked AFTER the
   * user types the confirm phrase (FR-5.20) — never automatically. May be
   * async; the page awaits it, then resets the confirm field + fires a toast.
   */
  readonly onDeleteAll: () => void | Promise<void>;

  /** One-shot confirmation sink (wire to `SettingsSurfaceController.showToast`). */
  readonly onToast?: (message: string) => void;

  /** Export destination shown in the hint. Defaults to `~/copilot/export`. */
  readonly exportPath?: string;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function toMessage(err: unknown, fallback: string): string {
  if (err instanceof Error && err.message) return err.message;
  if (typeof err === "string" && err) return err;
  return fallback;
}

function isRetentionChoice(value: string): value is RetentionChoice {
  return RETENTION_OPTIONS.some((option) => option.value === value);
}

// ---------------------------------------------------------------------------
// Styles (token-only chrome).
// ---------------------------------------------------------------------------

const linkButtonStyle: CSSProperties = {
  appearance: "none",
  background: "transparent",
  border: "none",
  padding: 0,
  font: "inherit",
  fontSize: "var(--font-size-sm)",
  fontWeight: "var(--font-weight-medium)",
  color: "var(--color-accent)",
  cursor: "pointer",
  textAlign: "left",
};

const mutedNoteStyle: CSSProperties = {
  margin: 0,
  fontSize: "var(--font-size-sm)",
  color: "var(--color-text-muted)",
};

const rowErrorStyle: CSSProperties = {
  margin: 0,
  fontSize: "var(--font-size-xs)",
  color: "var(--color-danger)",
};

const dangerZoneStyle: CSSProperties = {
  display: "flex",
  flexWrap: "wrap",
  alignItems: "center",
  gap: "var(--space-sm)",
};

const confirmInputStyle: CSSProperties = {
  flex: "1 1 220px",
  minWidth: 200,
};

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export function PrivacyPage({
  retention,
  onRetentionChange,
  memoryEnabled,
  onMemoryToggle,
  memoryCount,
  onReviewMemories,
  onOpenActivity,
  onExport,
  onDeleteAll,
  onToast,
  exportPath = PRIVACY_EXPORT_PATH,
}: PrivacyPageProps): ReactElement {
  const reactId = useId();
  const retentionId = `${reactId}-retention`;
  const memoryId = `${reactId}-memory`;
  const confirmId = `${reactId}-delete-confirm`;

  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);

  const [confirmText, setConfirmText] = useState("");
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  const confirmMatches =
    confirmText.trim().toLowerCase() === PRIVACY_DELETE_CONFIRM_PHRASE;

  const handleRetentionChange = useCallback(
    (event: ChangeEvent<HTMLSelectElement>) => {
      const next = event.target.value;
      if (isRetentionChoice(next)) {
        onRetentionChange(next);
      }
    },
    [onRetentionChange],
  );

  const handleExport = useCallback(() => {
    if (exporting) return;
    setExporting(true);
    setExportError(null);
    // Invoke the host callback synchronously (a click calls it immediately),
    // then chain on its possibly-promise result.
    Promise.resolve(onExport())
      .then(() => {
        onToast?.(`Export queued to ${exportPath}.`);
      })
      .catch((err: unknown) => {
        setExportError(toMessage(err, "Could not start export."));
      })
      .finally(() => {
        setExporting(false);
      });
  }, [exporting, onExport, onToast, exportPath]);

  const handleDelete = useCallback(() => {
    // Never delete without the typed confirmation (FR-5.20).
    if (!confirmMatches || deleting) return;
    setDeleting(true);
    setDeleteError(null);
    Promise.resolve(onDeleteAll())
      .then(() => {
        setConfirmText("");
        onToast?.("All history deleted.");
      })
      .catch((err: unknown) => {
        setDeleteError(toMessage(err, "Could not delete history."));
      })
      .finally(() => {
        setDeleting(false);
      });
  }, [confirmMatches, deleting, onDeleteAll, onToast]);

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: "var(--space-lg)",
      }}
      data-testid="privacy-page"
    >
      {/* Retention + Activity ------------------------------------------------ */}
      <SetCard
        title="Privacy & retention"
        meta="Everything the agent does is recorded to local history on this device."
        data-testid="privacy-retention-card"
      >
        <SetNote data-testid="privacy-history-note">
          Every run and step is recorded to local history. See the full record
          on the Activity page.
        </SetNote>

        <Frow
          label="Keep run history for"
          hint="Older runs are pruned automatically. Forever keeps everything."
          htmlFor={retentionId}
        >
          <Select
            id={retentionId}
            value={retention}
            onChange={handleRetentionChange}
            data-testid="privacy-retention-select"
          >
            {RETENTION_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </Select>
        </Frow>

        <Frow
          label="Activity"
          hint="The full record of everything the agent has done."
        >
          <Button
            type="button"
            variant="secondary"
            size="sm"
            onClick={onOpenActivity}
            data-testid="privacy-open-activity"
          >
            Open Activity
          </Button>
        </Frow>
      </SetCard>

      {/* Memory -------------------------------------------------------------- */}
      <SetCard title="Memory" data-testid="privacy-memory-card">
        <Frow
          label="Remember details across chats"
          hint="Let the agent keep durable notes about you and your work."
          htmlFor={memoryId}
        >
          <Toggle
            id={memoryId}
            checked={memoryEnabled}
            aria-label="Remember details across chats"
            data-testid="privacy-memory-toggle"
            onChange={(event) => onMemoryToggle(event.currentTarget.checked)}
          />
        </Frow>

        {memoryCount > 0 ? (
          <button
            type="button"
            style={linkButtonStyle}
            onClick={onReviewMemories}
            data-testid="privacy-review-memories"
          >
            {`Review ${memoryCount} ${
              memoryCount === 1 ? "memory" : "memories"
            } →`}
          </button>
        ) : (
          <p style={mutedNoteStyle} data-testid="privacy-memories-empty">
            No saved memories yet.
          </p>
        )}
      </SetCard>

      {/* Export -------------------------------------------------------------- */}
      <SetCard
        title="Export everything"
        meta={`Save a full copy of your chats, runs, and memory to ${exportPath}.`}
        data-testid="privacy-export-card"
      >
        <div>
          <Button
            type="button"
            variant="secondary"
            onClick={handleExport}
            disabled={exporting}
            data-testid="privacy-export"
          >
            {exporting ? "Exporting…" : "Export everything"}
          </Button>
        </div>
        {exportError !== null ? (
          <p
            role="alert"
            style={rowErrorStyle}
            data-testid="privacy-export-error"
          >
            {exportError}
          </p>
        ) : null}
      </SetCard>

      {/* Delete all (danger) ------------------------------------------------- */}
      <SetCard title="Delete all history" data-testid="privacy-delete-card">
        <SetNote tone="danger" data-testid="privacy-delete-note">
          This permanently deletes every chat, run, and memory on this device.
          It cannot be undone. Type “{PRIVACY_DELETE_CONFIRM_PHRASE}” to
          confirm.
        </SetNote>

        <SecHead>Danger zone</SecHead>

        <div style={dangerZoneStyle}>
          <TextInput
            id={confirmId}
            value={confirmText}
            onChange={(event) => setConfirmText(event.target.value)}
            placeholder={PRIVACY_DELETE_CONFIRM_PHRASE}
            aria-label={`Type “${PRIVACY_DELETE_CONFIRM_PHRASE}” to confirm deletion`}
            autoComplete="off"
            spellCheck={false}
            style={confirmInputStyle}
            data-testid="privacy-delete-confirm"
          />
          <Button
            type="button"
            variant="danger"
            disabled={!confirmMatches || deleting}
            onClick={handleDelete}
            data-testid="privacy-delete-all"
          >
            {deleting ? "Deleting…" : "Delete all history"}
          </Button>
        </div>
        {deleteError !== null ? (
          <p
            role="alert"
            style={rowErrorStyle}
            data-testid="privacy-delete-error"
          >
            {deleteError}
          </p>
        ) : null}
      </SetCard>
    </div>
  );
}
