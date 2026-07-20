// Key storage & app lock — Settings → Advanced (DESIGN-SPEC §4 · PRD PR-5.9).
//
//   * Keychain note — "keys in macOS Keychain, encrypted at rest" (SetNote).
//   * Encrypt local run history — toggle.
//   * Require Touch ID to open — toggle. When the platform CANNOT provide
//     Touch ID the control is DISABLED with a visible, readable hint rather
//     than hidden abruptly (FR-5.23 / US-5.8; a11y §9: not color-only).
//   * Lock after — 5 min / 15 min / 1 hour / Never (Select).
//
// SUBSTRATE-AGNOSTIC. A controlled, presentation-only section: the values are
// controlled props and every edit is reported through `onChange` — the host
// (the desktop shell) owns whether Touch ID is available (a native capability
// it reads through its own port) and where the app-lock prefs persist. Like
// Appearance/Privacy this applies optimistically and has NO dirty savebar
// (FR-5.7). Colors resolve ONLY to design-system v2 tokens.

import {
  useCallback,
  useId,
  type ChangeEvent,
  type CSSProperties,
  type ReactElement,
} from "react";

import { Select, Toggle } from "@0x-copilot/design-system";

import { Frow, SecHead, SetCard, SetNote } from "./SettingsChrome";

// ---------------------------------------------------------------------------
// Vocabulary.
// ---------------------------------------------------------------------------

/** "Lock after" idle window (DESIGN-SPEC §4). `never` disables auto-lock. */
export type AppLockAfter = "5m" | "15m" | "1h" | "never";

export const APP_LOCK_AFTER_OPTIONS: ReadonlyArray<{
  readonly value: AppLockAfter;
  readonly label: string;
}> = [
  { value: "5m", label: "5 minutes" },
  { value: "15m", label: "15 minutes" },
  { value: "1h", label: "1 hour" },
  { value: "never", label: "Never" },
];

/** DESIGN-SPEC §4 keychain note. */
export const APP_LOCK_KEYCHAIN_NOTE =
  "Your provider keys and developer tokens live in the macOS Keychain, encrypted at rest — never sent to a 0xCopilot server.";

/** Default hint shown when the platform cannot provide Touch ID (FR-5.23). */
export const TOUCH_ID_UNAVAILABLE_HINT =
  "Touch ID isn't available on this device, so it can't gate opening the app.";

export interface AppLockValue {
  /** Encrypt local run history at rest. */
  readonly encryptHistory: boolean;
  /** Require Touch ID to open the app. */
  readonly requireTouchId: boolean;
  /** Auto-lock idle window. */
  readonly lockAfter: AppLockAfter;
}

/** A single edit; only ever carries a valid field value. */
export interface AppLockPatch {
  readonly encryptHistory?: boolean;
  readonly requireTouchId?: boolean;
  readonly lockAfter?: AppLockAfter;
}

/**
 * Host-supplied state for the "Protect secrets with macOS Keychain" toggle.
 * Desktop-only: the row renders only when the host passes this block (web has
 * no boot-secrets store, so it simply omits it).
 */
export interface KeychainProtectionValue {
  /** Current policy: true = keychain-encrypted, false = chmod-600 file. */
  readonly enabled: boolean;
  /** Whether the OS keychain exists on this platform (row disables if not). */
  readonly available: boolean;
  /** A toggle round-trip is in flight — the control disables meanwhile. */
  readonly busy?: boolean;
}

export interface AppLockPageProps {
  readonly value: AppLockValue;
  /**
   * Report an edit. Optimistic — the host persists it (there is no savebar on
   * this page, FR-5.7).
   */
  readonly onChange: (patch: AppLockPatch) => void;
  /**
   * Keychain protection for app secrets (desktop). Absent ⇒ the row is not
   * rendered. NOT part of `AppLockValue`/`onChange`: flipping it performs a
   * real secrets migration in the host (and may raise an OS prompt), so it
   * gets its own explicit callback instead of the optimistic patch path.
   */
  readonly keychainProtection?: KeychainProtectionValue;
  readonly onKeychainProtectionChange?: (enabled: boolean) => void;
  /**
   * Whether the platform can provide Touch ID. When `false` the Touch-ID toggle
   * renders DISABLED with an explanatory hint (FR-5.23) rather than vanishing.
   * Defaults to `true`.
   */
  readonly touchIdAvailable?: boolean;
  /** Hint shown when Touch ID is unavailable. */
  readonly touchIdUnavailableHint?: string;
  /** Prefs still loading — render a quiet note, never a blank. */
  readonly loading?: boolean;
  /** Load/save error — surfaced as a role="alert" with a Retry affordance. */
  readonly error?: string | null;
  readonly onRetry?: () => void;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function isLockAfter(value: string): value is AppLockAfter {
  return APP_LOCK_AFTER_OPTIONS.some((option) => option.value === value);
}

// ---------------------------------------------------------------------------
// Styles (token-only).
// ---------------------------------------------------------------------------

const hintStyle: CSSProperties = {
  margin: "4px 0 0",
  fontSize: "var(--font-size-xs)",
  lineHeight: "var(--line-height-base)",
  color: "var(--color-text-muted)",
};

const alertRowStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  gap: "var(--space-md)",
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

export function AppLockPage({
  value,
  onChange,
  keychainProtection,
  onKeychainProtectionChange,
  touchIdAvailable = true,
  touchIdUnavailableHint = TOUCH_ID_UNAVAILABLE_HINT,
  loading = false,
  error = null,
  onRetry,
}: AppLockPageProps): ReactElement {
  const reactId = useId();
  const keychainId = `${reactId}-keychain-protection`;
  const encryptId = `${reactId}-encrypt-history`;
  const touchIdId = `${reactId}-require-touch-id`;
  const touchIdHintId = `${reactId}-touch-id-hint`;
  const lockAfterId = `${reactId}-lock-after`;

  const handleLockAfter = useCallback(
    (event: ChangeEvent<HTMLSelectElement>) => {
      const next = event.target.value;
      if (isLockAfter(next)) {
        onChange({ lockAfter: next });
      }
    },
    [onChange],
  );

  if (loading) {
    return (
      <SetCard
        title="Key storage & app lock"
        meta="How your keys and local history are protected on this device."
        data-testid="app-lock-page"
      >
        <SetNote data-testid="app-lock-loading">Loading settings…</SetNote>
      </SetCard>
    );
  }

  if (error !== null) {
    return (
      <SetCard
        title="Key storage & app lock"
        meta="How your keys and local history are protected on this device."
        data-testid="app-lock-page"
      >
        <p role="alert" data-testid="app-lock-error" style={alertRowStyle}>
          <span>{error}</span>
          {onRetry !== undefined ? (
            <button
              type="button"
              onClick={onRetry}
              style={retryButtonStyle}
              data-testid="app-lock-retry"
            >
              Retry
            </button>
          ) : null}
        </p>
      </SetCard>
    );
  }

  // When Touch ID is unavailable the control is disabled AND its persisted value
  // is shown as off (an unavailable capability can't be "required").
  const touchIdChecked = touchIdAvailable && value.requireTouchId;

  return (
    <SetCard
      title="Key storage & app lock"
      meta="How your keys and local history are protected on this device."
      data-testid="app-lock-page"
    >
      <SetNote data-testid="app-lock-keychain-note">
        {APP_LOCK_KEYCHAIN_NOTE}
      </SetNote>

      <SecHead>On-device protection</SecHead>

      {keychainProtection !== undefined ? (
        <Frow
          label="Protect secrets with macOS Keychain"
          hint={
            keychainProtection.available
              ? "Off: secrets stay in a file only your account can read — no keychain prompts. On: macOS encrypts them and asks permission after app updates."
              : "The OS keychain is not available on this platform."
          }
          htmlFor={keychainId}
        >
          <Toggle
            id={keychainId}
            checked={keychainProtection.enabled}
            disabled={
              !keychainProtection.available || keychainProtection.busy === true
            }
            aria-label="Protect secrets with macOS Keychain"
            data-testid="app-lock-keychain-protection"
            onChange={(event) =>
              onKeychainProtectionChange?.(event.currentTarget.checked)
            }
          />
        </Frow>
      ) : null}

      <Frow
        label="Encrypt local run history"
        hint="Encrypt chats, runs, and memory at rest on this machine."
        htmlFor={encryptId}
      >
        <Toggle
          id={encryptId}
          checked={value.encryptHistory}
          aria-label="Encrypt local run history"
          data-testid="app-lock-encrypt-history"
          onChange={(event) =>
            onChange({ encryptHistory: event.currentTarget.checked })
          }
        />
      </Frow>

      <Frow
        label="Require Touch ID to open"
        hint="Unlock the app with Touch ID each time it opens."
        htmlFor={touchIdId}
      >
        <Toggle
          id={touchIdId}
          checked={touchIdChecked}
          disabled={!touchIdAvailable}
          aria-label="Require Touch ID to open"
          aria-describedby={touchIdAvailable ? undefined : touchIdHintId}
          data-testid="app-lock-require-touch-id"
          onChange={(event) =>
            onChange({ requireTouchId: event.currentTarget.checked })
          }
        />
      </Frow>
      {!touchIdAvailable ? (
        <p
          id={touchIdHintId}
          style={hintStyle}
          data-testid="app-lock-touch-id-hint"
        >
          {touchIdUnavailableHint}
        </p>
      ) : null}

      <Frow
        label="Lock after"
        hint="Automatically lock the app after this much idle time."
        htmlFor={lockAfterId}
      >
        <Select
          id={lockAfterId}
          value={value.lockAfter}
          onChange={handleLockAfter}
          data-testid="app-lock-lock-after"
        >
          {APP_LOCK_AFTER_OPTIONS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </Select>
      </Frow>
    </SetCard>
  );
}
