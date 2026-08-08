// Inline BYOK add-key form (SPEC §"KeyForm" · PRD-P1 §3).
//
// A SINGLE-STEP form with NOTHING to choose:
//
//   paste a key → we infer the provider → Connect <Provider>
//
// The provider toggle is gone. It asked the user to answer a question the key
// already answers, and it did not scale: a fourth provider made it wrap, and
// each new one would push a real decision further down the card. Inference is
// `detectProviderFromKey` (settings/data/providerKeys.ts), which mirrors the
// server's `_KNOWN_PREFIXES`. A key that matches nothing is the ONLY case that
// asks anything — it opens the provider list.
//
// WHY IT DOES NOT RESOLVE PER KEYSTROKE: `sk-` is a prefix of `sk-ant-` and
// `sk-or-`, so a live verdict walks through "OpenAI" on its way to Anthropic
// and the label flips under the cursor. The verdict is taken on PASTE (how
// anyone actually enters a key), on blur, and behind a debounce — never on
// every character.
//
// Security invariant (mirrors AddProviderKeyModal): the plaintext key lives
// ONLY in this component's local `apiKey` state and leaves exactly once — the
// `port.save(provider, key)` PUT body. It is never re-displayed in full, never
// logged, and is cleared on unmount. A rejected save surfaces a `role="alert"`
// and stores NOTHING; `onConnected` never fires on failure.
//
// The old form wiped the key whenever the provider changed, to stop a plaintext
// key crossing to a provider it was not meant for. That guarantee cannot work
// here — correcting the provider for an already-pasted key is the entire
// feature — so it is replaced by a VISIBLE one: the settled row names the
// provider, and the primary button reads "Connect <Provider>". The destination
// is on screen, in two places, at the moment of the click.
//
// Substrate-agnostic: I/O is the injected `ProviderKeysPort` only. Colors
// resolve to design-system tokens (`onboarding.css`); the per-provider leading
// dot is inline swatch DATA from `FirstRunKeyProvider.dotColor` (SPEC §Data).

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ReactElement,
} from "react";

import { TextInput } from "@0x-copilot/design-system";

import {
  detectProviderFromKey,
  MIN_PLAUSIBLE_KEY_LENGTH,
  type ProviderKeysPort,
} from "../settings/data/providerKeys";
import {
  checkFirstRunKeyFormat,
  FIRST_RUN_COPY,
  FIRST_RUN_KEY_PROVIDERS,
  type FirstRunKeyProvider,
} from "./firstRun";

/** The result handed to the surface on a successful connect. */
export interface KeyFormConnected {
  readonly provider: string;
  readonly label: string;
  readonly dotColor: string;
  readonly keyHint: string; // masked suffix from ProviderKeySummary.key_hint
  readonly modelId: string | null; // resolved later (P3 composer model pill)
}

/** Pure client-side format verdict — same shape `checkFirstRunKeyFormat` returns. */
export type KeyFormFormatCheck = (
  provider: FirstRunKeyProvider,
  apiKey: string,
) => { readonly ok: true } | { readonly ok: false; readonly error: string };

export interface KeyFormProps {
  /** Reuse the existing provider-keys seam (never a bare fetch). */
  readonly port: ProviderKeysPort;
  /** Providers offered when a key matches nothing. Default `FIRST_RUN_KEY_PROVIDERS`. */
  readonly providers?: readonly FirstRunKeyProvider[];
  /** Masked-input placeholder. Default `FIRST_RUN_COPY.keyForm.placeholder`. */
  readonly placeholder?: string;
  /** Sub-note under the input. Default `FIRST_RUN_COPY.keyForm.note`. */
  readonly note?: string;
  /** Primary-button label when no provider is resolved yet. */
  readonly connectLabel?: string;
  /** Pre-flight format check. Default `checkFirstRunKeyFormat`. */
  readonly formatCheck?: KeyFormFormatCheck;
  /**
   * Milliseconds of idle typing before a verdict is taken. Paste and blur
   * settle immediately regardless. Exposed so tests need not wait in real time.
   */
  readonly settleDelayMs?: number;
  /** Fired once, after a successful `port.save`. → surface: engine=key, stage=ready. */
  readonly onConnected: (result: KeyFormConnected) => void;
  readonly onCancel?: () => void;
}

const DEFAULT_SETTLE_DELAY_MS = 400;

function toMessage(err: unknown, fallback: string): string {
  if (err instanceof Error && err.message) return err.message;
  if (typeof err === "string" && err) return err;
  return fallback;
}

/**
 * Last four characters only, the same shape the server's `key_hint` uses. The
 * head is never rendered — a settled row must not become a way to read back a
 * key the password field was hiding a moment ago.
 */
function maskKey(apiKey: string): string {
  const trimmed = apiKey.trim();
  if (trimmed.length <= 4) return "•".repeat(trimmed.length);
  return `${"•".repeat(Math.min(20, trimmed.length - 4))}${trimmed.slice(-4)}`;
}

export function KeyForm({
  port,
  providers = FIRST_RUN_KEY_PROVIDERS,
  placeholder,
  note = FIRST_RUN_COPY.keyForm.note,
  connectLabel = FIRST_RUN_COPY.keyForm.btn,
  formatCheck = checkFirstRunKeyFormat,
  settleDelayMs = DEFAULT_SETTLE_DELAY_MS,
  onConnected,
  onCancel,
}: KeyFormProps): ReactElement {
  const [apiKey, setApiKey] = useState("");
  /** A provider the user picked by hand; always beats the inferred one. */
  const [override, setOverride] = useState<string | null>(null);
  /** Whether a verdict has been taken on the current key (see the header). */
  const [settled, setSettled] = useState(false);
  /** The provider list is open — either by Change, or because nothing matched. */
  const [picking, setPicking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [connecting, setConnecting] = useState(false);

  // Guards against a state update after unmount (the save is async and the
  // surface may swap this out on success).
  const aliveRef = useRef(true);
  useEffect(() => {
    aliveRef.current = true;
    return () => {
      aliveRef.current = false;
    };
  }, []);

  const trimmed = apiKey.trim();
  const longEnough = trimmed.length >= MIN_PLAUSIBLE_KEY_LENGTH;

  // Idle-typing settle. Paste and blur bypass this; it exists only so a key
  // that WAS typed still resolves without the user doing anything else.
  useEffect(() => {
    if (!longEnough || settled) return undefined;
    const timer = setTimeout(() => {
      if (aliveRef.current) setSettled(true);
    }, settleDelayMs);
    return () => clearTimeout(timer);
  }, [longEnough, settled, settleDelayMs, apiKey]);

  const detected = longEnough ? detectProviderFromKey(trimmed) : null;
  const providerId = override ?? detected;
  const provider = providers.find((p) => p.id === providerId) ?? null;
  const resolved = settled && longEnough;
  const unknown = resolved && provider === null;

  const handleChange = useCallback((next: string) => {
    setApiKey(next);
    // Editing invalidates every prior conclusion: a fresh key must not inherit
    // the provider chosen for the one before it.
    setSettled(false);
    setOverride(null);
    setPicking(false);
    setError(null);
  }, []);

  const handleConnect = useCallback(() => {
    if (connecting || provider === null) return;
    const format = formatCheck(provider, apiKey);
    if (!format.ok) {
      setError(format.error);
      return;
    }
    setConnecting(true);
    setError(null);
    // The ONE place plaintext leaves the component.
    port
      .save(provider.id, apiKey.trim())
      .then((summary) => {
        if (!aliveRef.current) return;
        onConnected({
          provider: provider.id,
          label: provider.label,
          dotColor: provider.dotColor,
          keyHint: summary.key_hint,
          modelId: null,
        });
      })
      .catch((err: unknown) => {
        if (!aliveRef.current) return;
        setError(toMessage(err, "Could not connect that key. Try again."));
        setConnecting(false);
      });
  }, [apiKey, connecting, formatCheck, onConnected, port, provider]);

  const inputPlaceholder = placeholder ?? FIRST_RUN_COPY.keyForm.placeholder;
  const listOpen = picking || unknown;

  return (
    <div className="fr-kf" data-testid="first-run-keyform">
      {resolved ? (
        <div
          className={
            provider !== null
              ? "fr-kf__resolved"
              : "fr-kf__resolved fr-kf__resolved--unknown"
          }
          data-testid="first-run-key-resolved"
          data-provider={provider?.id ?? ""}
        >
          {provider !== null ? (
            <>
              <span
                className="fr-kf__dot"
                aria-hidden="true"
                data-swatch={provider.dotColor}
                style={{ backgroundColor: provider.dotColor }}
              />
              <span className="fr-kf__who">{provider.label}</span>
            </>
          ) : null}
          <button
            type="button"
            className="fr-kf__masked"
            onClick={() => setSettled(false)}
            aria-label="Edit key"
            data-testid="first-run-key-edit"
          >
            {maskKey(apiKey)}
          </button>
          {provider !== null ? (
            <button
              type="button"
              className="fr-kf__link"
              onClick={() => setPicking((open) => !open)}
              aria-expanded={picking}
              data-testid="first-run-key-change"
            >
              {FIRST_RUN_COPY.keyForm.change}
            </button>
          ) : null}
        </div>
      ) : (
        <TextInput
          className="fr-kf__input"
          type="password"
          autoComplete="new-password"
          spellCheck={false}
          value={apiKey}
          placeholder={inputPlaceholder}
          aria-label="API key"
          onChange={(event) => handleChange(event.target.value)}
          onPaste={() => {
            // React fires onChange after onPaste; settling on the next tick
            // lets the new value land first, so the verdict reads the pasted
            // key rather than the empty field it replaced.
            setTimeout(() => {
              if (aliveRef.current) setSettled(true);
            }, 0);
          }}
          onBlur={() => setSettled(true)}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              event.preventDefault();
              setSettled(true);
            }
          }}
          data-testid="first-run-key-input"
        />
      )}

      {unknown ? (
        <p className="fr-kf__error" data-testid="first-run-key-unknown">
          {FIRST_RUN_COPY.keyForm.unknown}
        </p>
      ) : null}

      {listOpen ? (
        <div
          className="fr-kf__picker"
          role="listbox"
          aria-label={FIRST_RUN_COPY.keyForm.choose}
          data-testid="first-run-key-picker"
        >
          {providers.map((p) => (
            <button
              key={p.id}
              type="button"
              role="option"
              aria-selected={p.id === providerId}
              className={
                p.id === providerId
                  ? "fr-kf__pick fr-kf__pick--on"
                  : "fr-kf__pick"
              }
              onClick={() => {
                setOverride(p.id);
                setPicking(false);
                setSettled(true);
                setError(null);
              }}
              data-testid={`first-run-key-pick-${p.id}`}
            >
              <span
                className="fr-kf__dot"
                aria-hidden="true"
                data-swatch={p.dotColor}
                style={{ backgroundColor: p.dotColor }}
              />
              <span className="fr-kf__pick-label">{p.label}</span>
              {p.keyPrefix !== undefined ? (
                <span className="fr-kf__pick-hint">{p.keyPrefix}…</span>
              ) : null}
            </button>
          ))}
        </div>
      ) : null}

      <p className="fr-kf__note" data-testid="first-run-key-note">
        {note}
      </p>

      {error !== null ? (
        <p
          role="alert"
          className="fr-kf__error"
          data-testid="first-run-key-error"
        >
          {error}
        </p>
      ) : null}

      <div className="fr-kf__actions">
        {onCancel !== undefined ? (
          <button
            type="button"
            className="gbtn"
            onClick={onCancel}
            data-testid="first-run-key-cancel"
          >
            Cancel
          </button>
        ) : null}
        <button
          type="button"
          className="gbtn gbtn--pri"
          disabled={connecting || provider === null}
          aria-disabled={connecting}
          onClick={handleConnect}
          data-testid="first-run-key-connect"
        >
          {connecting
            ? "Connecting…"
            : provider !== null
              ? `${FIRST_RUN_COPY.keyForm.btnFor} ${provider.label}`
              : connectLabel}
        </button>
      </div>
    </div>
  );
}
