// Add-a-provider-key flow (DESIGN-SPEC §5 · PRD PR-5.4, FR-5.12).
//
// A 3-StepDots modal over the reusable <Modal>/<StepDots> chrome:
//
//   1. Enter key  — masked `sk-…` input → Continue
//   2. Validate   — "Validating with {provider}…" spinner (role="status")
//   3. Default    — choose default model → Add
//
// Security invariant: the plaintext key lives ONLY in this component's local
// state for the duration of one flow, and leaves exactly once — passed to the
// injected `onSubmit` (which the page routes to `ProviderKeysPort.save`, the
// single PUT body). It is never re-displayed, never logged, and is cleared when
// the modal closes. A failed validation surfaces a `role="alert"` and stores
// NOTHING (US-5.3).
//
// The page injects both async operations, so this component knows nothing about
// Transport or the facade — the storage/validation seam is entirely the host's:
//   * `onValidate(apiKey)` — step-2 gate (defaults to `checkProviderKeyFormat`)
//   * `onSubmit({ apiKey, model })` — step-3 store (called at most once)
//
// Substrate-agnostic; colors resolve only to design-system v2 tokens.

import {
  useCallback,
  useEffect,
  useState,
  type CSSProperties,
  type ReactElement,
} from "react";

import { Button, Field, Select, TextInput } from "@0x-copilot/design-system";

import { Modal, StepDots } from "./Modal";
import {
  checkProviderKeyFormat,
  type ProviderCatalogEntry,
  type ProviderKeyValidation,
} from "./data/providerKeys";

export interface AddProviderKeySubmit {
  readonly apiKey: string;
  readonly model: string;
}

export interface AddProviderKeyModalProps {
  readonly open: boolean;
  /** The provider whose key is being added / rotated. */
  readonly provider: ProviderCatalogEntry;
  /** "add" (new key) vs "rotate" (replace an existing one) — affects copy only. */
  readonly mode?: "add" | "rotate";
  readonly onClose: () => void;
  /**
   * Step-2 validation. Resolves `{ ok: true, models? }` to advance to step 3,
   * or `{ ok: false, error }` to bounce back to step 1 with an inline alert and
   * no stored key. Defaults to the pure `checkProviderKeyFormat`.
   */
  readonly onValidate?: (apiKey: string) => Promise<ProviderKeyValidation>;
  /**
   * Step-3 store — receives the plaintext key exactly once. Resolve to finish
   * (the modal calls `onClose`); reject to keep the flow open with an alert.
   */
  readonly onSubmit: (submit: AddProviderKeySubmit) => Promise<void>;
}

type Step = 1 | 2 | 3;

function toMessage(err: unknown, fallback: string): string {
  if (err instanceof Error && err.message) return err.message;
  if (typeof err === "string" && err) return err;
  return fallback;
}

const bodyBlockStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: "var(--space-md)",
};

const validatingRowStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: "var(--space-sm)",
  fontSize: "var(--font-size-sm)",
  color: "var(--color-text-muted)",
};

const spinnerStyle: CSSProperties = {
  flex: "0 0 auto",
  width: 14,
  height: 14,
  borderRadius: "var(--radius-full)",
  border: "2px solid var(--color-border-strong)",
  borderTopColor: "var(--color-accent)",
};

const errorStyle: CSSProperties = {
  margin: 0,
  fontSize: "var(--font-size-xs)",
  color: "var(--color-danger)",
};

const hintStyle: CSSProperties = {
  margin: 0,
  fontSize: "var(--font-size-xs)",
  color: "var(--color-text-muted)",
};

// Scoped spin keyframes (design-system owns no spinner primitive). Gated off
// under reduce-motion so [data-reduce-motion] and the OS setting both win.
const SPINNER_CSS = `
@keyframes akm-spin { to { transform: rotate(360deg); } }
.akm-spinner { animation: akm-spin 0.7s linear infinite; }
[data-reduce-motion="1"] .akm-spinner,
[data-reduce-motion="always"] .akm-spinner { animation: none; }
@media (prefers-reduced-motion: reduce) { .akm-spinner { animation: none; } }
`;

export function AddProviderKeyModal({
  open,
  provider,
  mode = "add",
  onClose,
  onValidate,
  onSubmit,
}: AddProviderKeyModalProps): ReactElement {
  const [step, setStep] = useState<Step>(1);
  const [apiKey, setApiKey] = useState("");
  const [model, setModel] = useState("");
  const [models, setModels] = useState<readonly string[]>(provider.models);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  // Fresh flow — and, critically, wiped plaintext — on every open.
  useEffect(() => {
    if (!open) return;
    setStep(1);
    setApiKey("");
    setModel("");
    setModels(provider.models);
    setError(null);
    setSubmitting(false);
  }, [open, provider]);

  const runValidate = useCallback(
    async (candidate: string) => {
      setError(null);
      setStep(2);
      try {
        const result = onValidate
          ? await onValidate(candidate)
          : checkProviderKeyFormat(provider, candidate);
        if (!result.ok) {
          setStep(1);
          setError(result.error ?? "That key could not be validated.");
          return;
        }
        const nextModels =
          result.models && result.models.length > 0
            ? result.models
            : provider.models;
        setModels(nextModels);
        setModel(nextModels[0] ?? "");
        setStep(3);
      } catch (err: unknown) {
        setStep(1);
        setError(toMessage(err, "Could not validate the key. Try again."));
      }
    },
    [onValidate, provider],
  );

  const handleContinue = useCallback(() => {
    const candidate = apiKey.trim();
    if (candidate.length === 0) return;
    void runValidate(candidate);
  }, [apiKey, runValidate]);

  const handleAdd = useCallback(() => {
    if (submitting) return;
    setSubmitting(true);
    setError(null);
    onSubmit({ apiKey: apiKey.trim(), model })
      .then(() => {
        onClose();
      })
      .catch((err: unknown) => {
        setError(toMessage(err, "Could not save the key."));
        setSubmitting(false);
      });
  }, [apiKey, model, onSubmit, onClose, submitting]);

  const title =
    mode === "rotate"
      ? `Rotate ${provider.label} key`
      : `Add ${provider.label} key`;

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={title}
      subtitle={provider.placeholder}
      logo={<span aria-hidden="true">{provider.label.charAt(0)}</span>}
      footer={
        <>
          <StepDots total={3} current={step} />
          <div
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: "var(--space-sm)",
            }}
          >
            {step === 3 ? (
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={() => {
                  setError(null);
                  setStep(1);
                }}
                data-testid="add-key-back"
              >
                Back
              </Button>
            ) : (
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={onClose}
                data-testid="add-key-cancel"
              >
                Cancel
              </Button>
            )}
            {step === 1 ? (
              <Button
                type="button"
                variant="primary"
                size="sm"
                disabled={apiKey.trim().length === 0}
                onClick={handleContinue}
                data-testid="add-key-continue"
              >
                Validate key
              </Button>
            ) : null}
            {step === 3 ? (
              <Button
                type="button"
                variant="primary"
                size="sm"
                disabled={submitting || model.length === 0}
                aria-disabled={submitting}
                onClick={handleAdd}
                data-testid="add-key-submit"
              >
                {submitting ? "Adding…" : mode === "rotate" ? "Rotate" : "Add"}
              </Button>
            ) : null}
          </div>
        </>
      }
    >
      <style>{SPINNER_CSS}</style>

      {step === 1 ? (
        <div style={bodyBlockStyle}>
          <Field
            label={`${provider.label} API key`}
            hint="Sent to your provider to validate, then stored encrypted. Only the last 4 characters are ever shown again."
          >
            <TextInput
              type="password"
              autoComplete="new-password"
              spellCheck={false}
              value={apiKey}
              placeholder={provider.placeholder}
              onChange={(event) => setApiKey(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") {
                  event.preventDefault();
                  handleContinue();
                }
              }}
              data-testid="add-key-input"
            />
          </Field>
          {error !== null ? (
            <p role="alert" style={errorStyle} data-testid="add-key-error">
              {error}
            </p>
          ) : null}
        </div>
      ) : null}

      {step === 2 ? (
        <div
          role="status"
          aria-busy="true"
          style={validatingRowStyle}
          data-testid="add-key-validating"
        >
          <span
            className="akm-spinner"
            aria-hidden="true"
            style={spinnerStyle}
          />
          <span>Validating with {provider.label}…</span>
        </div>
      ) : null}

      {step === 3 ? (
        <div style={bodyBlockStyle}>
          <p style={hintStyle}>
            Key validated. Choose the model runs use by default — you can change
            it any time in Model &amp; behavior.
          </p>
          <Field label="Default model">
            <Select
              value={model}
              onChange={(event) => setModel(event.target.value)}
              data-testid="add-key-model"
            >
              {models.map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </Select>
          </Field>
          {error !== null ? (
            <p role="alert" style={errorStyle} data-testid="add-key-error">
              {error}
            </p>
          ) : null}
        </div>
      ) : null}
    </Modal>
  );
}
