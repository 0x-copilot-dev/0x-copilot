// Inline BYOK add-key form (SPEC §"KeyForm" · PRD-P1 §3).
//
// A SINGLE-STEP form (no validate-spinner / model-pick step — that is the
// 3-step settings `AddProviderKeyModal`; the FTUE gate is deliberately faster):
//
//   provider tri-toggle → `sk-…` password input → Connect
//
// Security invariant (mirrors AddProviderKeyModal): the plaintext key lives
// ONLY in this component's local `apiKey` state and leaves exactly once — the
// `port.save(provider, key)` PUT body. It is never re-displayed, never logged,
// and is cleared on provider switch and on unmount. A rejected save surfaces a
// `role="alert"` and stores NOTHING; `onConnected` never fires on failure.
//
// Substrate-agnostic: I/O is the injected `ProviderKeysPort` only. Colors
// resolve to design-system tokens (`onboarding.css`); the per-option leading
// dot is inline swatch DATA from `FirstRunKeyProvider.dotColor` (SPEC §Data).

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ReactElement,
} from "react";

import { Button, TextInput } from "@0x-copilot/design-system";

import { SegmentedControl } from "../settings/controls";
import type { ProviderKeysPort } from "../settings/data/providerKeys";
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

export interface KeyFormProps {
  /** Reuse the existing provider-keys seam (never a bare fetch). */
  readonly port: ProviderKeysPort;
  /** Provider rows for the tri-toggle. Default `FIRST_RUN_KEY_PROVIDERS`. */
  readonly providers?: readonly FirstRunKeyProvider[];
  /** Fired once, after a successful `port.save`. → surface: engine=key, stage=ready. */
  readonly onConnected: (result: KeyFormConnected) => void;
  readonly onCancel?: () => void;
}

function toMessage(err: unknown, fallback: string): string {
  if (err instanceof Error && err.message) return err.message;
  if (typeof err === "string" && err) return err;
  return fallback;
}

export function KeyForm({
  port,
  providers = FIRST_RUN_KEY_PROVIDERS,
  onConnected,
  onCancel,
}: KeyFormProps): ReactElement {
  const [providerId, setProviderId] = useState<string>(
    () => providers[0]?.id ?? "",
  );
  const [apiKey, setApiKey] = useState("");
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

  const provider =
    providers.find((p) => p.id === providerId) ?? providers[0] ?? null;

  // Provider switch must never carry a half-typed key across (no plaintext
  // leak between providers) — wipe it and clear any stale error.
  const handleProviderChange = useCallback((next: string) => {
    setProviderId(next);
    setApiKey("");
    setError(null);
  }, []);

  const handleConnect = useCallback(() => {
    if (connecting || provider === null) return;
    const format = checkFirstRunKeyFormat(provider, apiKey);
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
  }, [apiKey, connecting, onConnected, port, provider]);

  if (provider === null) return <></>;

  const options = providers.map((p) => ({
    value: p.id,
    label: (
      <span className="fr-kf__opt">
        <span
          className="fr-kf__dot"
          aria-hidden="true"
          data-swatch={p.dotColor}
          style={{ backgroundColor: p.dotColor }}
        />
        {p.label}
      </span>
    ),
  }));

  return (
    <div className="fr-kf" data-testid="first-run-keyform">
      <SegmentedControl
        className="fr-kf__prov"
        ariaLabel="Provider"
        options={options}
        value={providerId}
        onChange={handleProviderChange}
      />

      <TextInput
        className="fr-kf__input"
        type="password"
        autoComplete="new-password"
        spellCheck={false}
        value={apiKey}
        placeholder={FIRST_RUN_COPY.keyForm.placeholder}
        aria-label={`${provider.label} API key`}
        onChange={(event) => setApiKey(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter") {
            event.preventDefault();
            handleConnect();
          }
        }}
        data-testid="first-run-key-input"
      />

      <p className="fr-kf__note" data-testid="first-run-key-note">
        {FIRST_RUN_COPY.keyForm.note}
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
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={onCancel}
            data-testid="first-run-key-cancel"
          >
            Cancel
          </Button>
        ) : null}
        <Button
          type="button"
          variant="primary"
          size="sm"
          disabled={connecting || apiKey.trim().length === 0}
          aria-disabled={connecting}
          onClick={handleConnect}
          data-testid="first-run-key-connect"
        >
          {connecting ? "Connecting…" : FIRST_RUN_COPY.keyForm.btn}
        </Button>
      </div>
    </div>
  );
}
