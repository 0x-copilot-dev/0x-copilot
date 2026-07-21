// Provider keys (BYOK) — Settings → Models & keys (DESIGN-SPEC §4 · PRD PR-5.4).
//
//   * Connected list — logo + name + model chip + masked `key_hint` +
//     Rotate / Remove (FR-5.11).
//   * Add-a-provider list — every provider without a stored key renders an
//     "Add key" row (honest empty state, FR-5.13), plus the "Any
//     OpenAI-compatible endpoint works too" affordance.
//   * Add / Rotate open the 3-step <AddProviderKeyModal>.
//   * Keychain note (FR-5.13).
//
// The page holds NO plaintext key and never re-displays one — reads carry only
// the masked `key_hint`, and the Add flow's plaintext travels straight to the
// injected `ProviderKeysPort.save` (single PUT). All storage / validation is a
// host concern behind the port, so the page is framework-agnostic and testable
// with a mock port. Rotate / Remove are immediate one-shot actions → `onToast`,
// never the dirty savebar (FR-5.7).
//
// Substrate-agnostic; colors resolve only to design-system v2 tokens.

import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type CSSProperties,
  type ReactElement,
  type ReactNode,
} from "react";

import type { ProviderKeySummary } from "@0x-copilot/api-types";
import { Badge, Button } from "@0x-copilot/design-system";

import { Icon } from "../icons/Icon";

import {
  AddProviderKeyModal,
  type AddProviderKeySubmit,
} from "./AddProviderKeyModal";
import { Frow, Krow, SecHead, SetCard, SetNote } from "./SettingsChrome";
import {
  PROVIDER_CATALOG,
  checkProviderKeyFormat,
  type ProviderCatalogEntry,
  type ProviderKeyValidation,
  type ProviderKeysPort,
} from "./data/providerKeys";

// DESIGN-SPEC §4 keychain note.
// Honest storage claim (amended 2026-07-20): keys live TokenVault-encrypted in
// the local database, NOT in the macOS Keychain — the OS keychain only gates
// the encryption secrets when the user opts in (Key storage & app lock).
// The exported name keeps its legacy "KEYCHAIN_NOTE" identity for hosts/tests.
export const PROVIDER_KEYS_KEYCHAIN_NOTE =
  "Keys are encrypted at rest in your local vault and never sent to a 0xCopilot server.";

export interface ProviderKeysPageProps {
  /** Host-injected storage / validation seam (default: `createProviderKeysPort`). */
  readonly port: ProviderKeysPort;
  /** Provider rows to offer. Defaults to `PROVIDER_CATALOG`. */
  readonly providers?: readonly ProviderCatalogEntry[];
  /**
   * One-shot confirmation sink (wire to `SettingsSurfaceController.showToast`).
   * Rotate / Remove / Add fire it — the page never uses the dirty savebar.
   */
  readonly onToast?: (message: string) => void;
  /**
   * Fallback default-model chips per provider slug. The summary now carries a
   * server-projected `default_model` (PRD-F PR-F.5) which the row prefers;
   * these chips only fill in for older servers / keys stored without a model.
   * In-session Add-flow choices still win over both.
   */
  readonly modelChips?: Readonly<Record<string, string>>;
}

interface ModalTarget {
  readonly entry: ProviderCatalogEntry;
  readonly mode: "add" | "rotate";
}

function toMessage(err: unknown, fallback: string): string {
  if (err instanceof Error && err.message) return err.message;
  if (typeof err === "string" && err) return err;
  return fallback;
}

function formatUpdated(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleDateString();
}

const logoGlyphStyle: CSSProperties = {
  fontFamily: "var(--font-display)",
  fontSize: "var(--font-size-sm)",
  fontWeight: "var(--font-weight-semibold)",
};

// Colored brand marks for the known providers (design shows colored provider
// logos, not a neutral first-letter glyph). Each fills the 30×30 krow chip with
// the provider's brand colour; providers without an entry (Groq / xAI / any
// other) fall through to the neutral first-letter fallback below.
const brandTileStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  width: "100%",
  height: "100%",
};

const PROVIDER_BRAND: Readonly<
  Record<
    string,
    { readonly bg: string; readonly fg: string; readonly text: string }
  >
> = {
  openai: { bg: "#10a37f", fg: "#ffffff", text: "O" },
  anthropic: { bg: "#d97757", fg: "#ffffff", text: "A" },
  openrouter: { bg: "#6467f2", fg: "#ffffff", text: "OR" },
};

// Google's canonical 4-colour "G" (same paths the sign-in button uses).
function GoogleGlyph(): ReactElement {
  return (
    <svg width="18" height="18" viewBox="0 0 48 48" aria-hidden="true">
      <path
        fill="#EA4335"
        d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z"
      />
      <path
        fill="#4285F4"
        d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z"
      />
      <path
        fill="#FBBC05"
        d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z"
      />
      <path
        fill="#34A853"
        d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z"
      />
    </svg>
  );
}

function renderProviderLogo(entry: ProviderCatalogEntry): ReactNode {
  if (entry.id === "google") {
    return (
      <span style={{ ...brandTileStyle, backgroundColor: "#ffffff" }}>
        <GoogleGlyph />
      </span>
    );
  }
  const brand = PROVIDER_BRAND[entry.id];
  if (brand !== undefined) {
    return (
      <span
        style={{
          ...brandTileStyle,
          backgroundColor: brand.bg,
          color: brand.fg,
          fontFamily: "var(--font-display)",
          fontSize:
            brand.text.length > 1
              ? "var(--font-size-xs)"
              : "var(--font-size-sm)",
          fontWeight: "var(--font-weight-semibold)",
          letterSpacing: brand.text.length > 1 ? "-0.02em" : undefined,
        }}
      >
        {brand.text}
      </span>
    );
  }
  // Neutral fallback (Groq / xAI / any other) — first-letter glyph on the
  // default surface chip.
  return <span style={logoGlyphStyle}>{entry.label.charAt(0)}</span>;
}

const nameRowStyle: CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: "var(--space-sm)",
};

const listStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  // Flat rows divide with a top hairline (Krow) — no inter-row gap, so the
  // borders read as continuous dividers (design).
  gap: 0,
};

const sectionStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: "var(--space-sm)",
};

const rowErrorStyle: CSSProperties = {
  margin: "2px 0 0",
  fontSize: "var(--font-size-xs)",
  color: "var(--color-danger)",
};

// "Add key" / "Add a key" buttons carry a leading icon before the label.
const addKeyButtonStyle: CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: "var(--space-xs, 4px)",
};

export function ProviderKeysPage({
  port,
  providers = PROVIDER_CATALOG,
  onToast,
  modelChips,
}: ProviderKeysPageProps): ReactElement {
  const [keys, setKeys] = useState<readonly ProviderKeySummary[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [chosenModels, setChosenModels] = useState<Record<string, string>>({});
  const [removing, setRemoving] = useState<Record<string, boolean>>({});
  const [rowErrors, setRowErrors] = useState<Record<string, string>>({});
  const [modal, setModal] = useState<ModalTarget | null>(null);

  const refresh = useCallback(() => {
    setKeys(null);
    setLoadError(null);
    port
      .list()
      .then((next) => {
        setKeys(next);
        setLoadError(null);
      })
      .catch((err: unknown) => {
        setLoadError(toMessage(err, "Could not load provider keys."));
      });
  }, [port]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const summaryFor = useCallback(
    (slug: string): ProviderKeySummary | undefined =>
      (keys ?? []).find((key) => key.provider === slug),
    [keys],
  );

  const connected = useMemo(
    () =>
      providers
        .map((entry) => ({ entry, summary: summaryFor(entry.id) }))
        .filter(
          (
            row,
          ): row is {
            entry: ProviderCatalogEntry;
            summary: ProviderKeySummary;
          } => row.summary !== undefined,
        ),
    [providers, summaryFor],
  );

  const available = useMemo(
    () => providers.filter((entry) => summaryFor(entry.id) === undefined),
    [providers, summaryFor],
  );

  // Only providers the backend actually accepts can be added — a `comingSoon`
  // provider's "Add key" is disabled, and the generic "Add a key" CTA targets
  // the first *addable* provider, so no path dead-ends in a 422 (PRD-F FR-F.6).
  const addable = useMemo(
    () => available.filter((entry) => entry.comingSoon !== true),
    [available],
  );

  const handleValidate = useCallback(
    (entry: ProviderCatalogEntry) =>
      (apiKey: string): Promise<ProviderKeyValidation> =>
        port.validate
          ? port.validate(entry.id, apiKey)
          : Promise.resolve(checkProviderKeyFormat(entry, apiKey)),
    [port],
  );

  const handleSubmit = useCallback(
    (target: ModalTarget) =>
      async ({ apiKey, model }: AddProviderKeySubmit): Promise<void> => {
        // Persist the step-3 pick as THIS provider's default model (PR-F.5
        // per-provider column) in the same PUT that stores the key. The server
        // then projects it on `summary.default_model`, so the row chip is
        // server-sourced and survives reload per-provider on BOTH hosts — the
        // `modelChips` host hint becomes a legacy fallback, not the source.
        const summary = await port.save(
          target.entry.id,
          apiKey,
          model !== "" ? model : undefined,
        );
        setKeys((prev) => [
          ...(prev ?? []).filter((key) => key.provider !== summary.provider),
          summary,
        ]);
        setChosenModels((prev) => ({ ...prev, [target.entry.id]: model }));
        // Persist the step-3 pick as the workspace default so runs use it.
        // The key save above already succeeded — a defaults failure must not
        // read as a failed key add, so it degrades to honest toast copy.
        let defaultOutcome: "saved" | "failed" | "skipped" = "skipped";
        if (model !== "" && port.saveDefaultModel !== undefined) {
          try {
            await port.saveDefaultModel(target.entry.id, model);
            defaultOutcome = "saved";
          } catch {
            defaultOutcome = "failed";
          }
        }
        const keyVerb = target.mode === "rotate" ? "rotated" : "added";
        onToast?.(
          defaultOutcome === "saved"
            ? `${target.entry.label} key ${keyVerb} · ${model} is your default model.`
            : defaultOutcome === "failed"
              ? `${target.entry.label} key ${keyVerb}. Saving the default model failed — set it in Model & behavior.`
              : `${target.entry.label} key ${keyVerb}.`,
        );
      },
    [port, onToast],
  );

  const handleRemove = useCallback(
    (entry: ProviderCatalogEntry) => {
      if (removing[entry.id]) return;
      setRemoving((prev) => ({ ...prev, [entry.id]: true }));
      setRowErrors((prev) => {
        const next = { ...prev };
        delete next[entry.id];
        return next;
      });
      port
        .remove(entry.id)
        .then(() => {
          setKeys((prev) =>
            (prev ?? []).filter((key) => key.provider !== entry.id),
          );
          setChosenModels((prev) => {
            const next = { ...prev };
            delete next[entry.id];
            return next;
          });
          onToast?.(`${entry.label} key removed.`);
        })
        .catch((err: unknown) => {
          setRowErrors((prev) => ({
            ...prev,
            [entry.id]: toMessage(err, "Could not remove key."),
          }));
        })
        .finally(() => {
          setRemoving((prev) => {
            const next = { ...prev };
            delete next[entry.id];
            return next;
          });
        });
    },
    [port, onToast, removing],
  );

  // Row model chip (PRD-F PR-F.5). The freshest in-session Add-flow pick wins;
  // otherwise prefer the server's single-source `summary.default_model`
  // projection, and fall back to the host-supplied `modelChips` only when the
  // summary carries none (older servers / keys stored without a model).
  const chipFor = (slug: string): string | undefined =>
    chosenModels[slug] ??
    summaryFor(slug)?.default_model ??
    modelChips?.[slug] ??
    undefined;

  return (
    <>
      <SetCard
        title="Provider keys"
        meta="Bring your own model provider keys. Runs call your provider directly with the key you store here."
        data-testid="provider-keys-page"
      >
        <SetNote>{PROVIDER_KEYS_KEYCHAIN_NOTE}</SetNote>

        {keys === null && loadError === null ? (
          <p
            style={{
              margin: 0,
              fontSize: "var(--font-size-sm)",
              color: "var(--color-text-muted)",
            }}
            data-testid="provider-keys-loading"
          >
            Loading provider keys…
          </p>
        ) : loadError !== null ? (
          <div style={sectionStyle}>
            <p
              role="alert"
              style={{ margin: 0, color: "var(--color-danger)" }}
              data-testid="provider-keys-error"
            >
              {loadError}
            </p>
            <div>
              <Button
                type="button"
                variant="secondary"
                size="sm"
                onClick={refresh}
                data-testid="provider-keys-retry"
              >
                Retry
              </Button>
            </div>
          </div>
        ) : (
          <>
            {connected.length > 0 ? (
              <section style={sectionStyle}>
                <SecHead>Connected</SecHead>
                <div style={listStyle}>
                  {connected.map(({ entry, summary }) => {
                    const chip = chipFor(entry.id);
                    return (
                      <Krow
                        key={entry.id}
                        data-testid={`provider-row-${entry.id}`}
                        logo={renderProviderLogo(entry)}
                        name={
                          <span style={nameRowStyle}>
                            <span>{entry.label}</span>
                            {chip !== undefined ? (
                              <Badge
                                tone="success"
                                data-testid={`provider-model-chip-${entry.id}`}
                              >
                                {chip}
                              </Badge>
                            ) : null}
                          </span>
                        }
                        sub={
                          <>
                            key {summary.key_hint} · updated{" "}
                            {formatUpdated(summary.updated_at)}
                          </>
                        }
                        actions={
                          <>
                            <Button
                              type="button"
                              variant="ghost"
                              size="sm"
                              aria-label={`Rotate ${entry.label} key`}
                              onClick={() =>
                                setModal({ entry, mode: "rotate" })
                              }
                              data-testid={`provider-rotate-${entry.id}`}
                            >
                              Rotate
                            </Button>
                            <Button
                              type="button"
                              variant="ghost"
                              size="sm"
                              aria-label={`Remove ${entry.label} key`}
                              disabled={removing[entry.id] === true}
                              onClick={() => handleRemove(entry)}
                              data-testid={`provider-remove-${entry.id}`}
                            >
                              <Icon name="trash" size={13} />
                            </Button>
                          </>
                        }
                      />
                    );
                  })}
                </div>
                {connected.map(({ entry }) =>
                  rowErrors[entry.id] !== undefined ? (
                    <p
                      key={`err-${entry.id}`}
                      role="alert"
                      style={rowErrorStyle}
                      data-testid={`provider-row-error-${entry.id}`}
                    >
                      {rowErrors[entry.id]}
                    </p>
                  ) : null,
                )}
              </section>
            ) : null}

            <section style={sectionStyle}>
              <SecHead>Add a provider</SecHead>
              <div style={listStyle}>
                {available.map((entry) => (
                  <Krow
                    key={entry.id}
                    data-testid={`provider-available-${entry.id}`}
                    logo={renderProviderLogo(entry)}
                    name={<AddRowName entry={entry} />}
                    sub={entry.placeholder}
                    actions={
                      <Button
                        type="button"
                        variant="secondary"
                        size="sm"
                        aria-label={
                          entry.comingSoon === true
                            ? `${entry.label} coming soon`
                            : `Add ${entry.label} key`
                        }
                        disabled={entry.comingSoon === true}
                        onClick={() => setModal({ entry, mode: "add" })}
                        data-testid={`provider-add-${entry.id}`}
                        style={addKeyButtonStyle}
                      >
                        <Icon name="plus" size={14} />
                        {entry.comingSoon === true ? "Coming soon" : "Add key"}
                      </Button>
                    }
                  />
                ))}
              </div>
              <div data-testid="provider-compatible-note">
                <Frow
                  label="Another provider"
                  hint="Any OpenAI-compatible endpoint works too."
                >
                  <Button
                    type="button"
                    variant="primary"
                    size="sm"
                    aria-label="Add a key"
                    disabled={addable.length === 0}
                    onClick={() => {
                      const first = addable[0];
                      if (first !== undefined) {
                        setModal({ entry: first, mode: "add" });
                      }
                    }}
                    data-testid="provider-add-generic"
                    style={addKeyButtonStyle}
                  >
                    <Icon name="key" size={14} />
                    Add a key
                  </Button>
                </Frow>
              </div>
            </section>
          </>
        )}
      </SetCard>

      {modal !== null ? (
        <AddProviderKeyModal
          open
          provider={modal.entry}
          mode={modal.mode}
          onClose={() => setModal(null)}
          onValidate={handleValidate(modal.entry)}
          onSubmit={handleSubmit(modal)}
        />
      ) : null}
    </>
  );
}

// A provider that is OpenAI-wire compatible but not yet in the shipped
// `ProviderKeyProvider` union (Groq / xAI) gets a quiet "compatible" marker so
// the drift is visible in the UI, not silently equated with a backed provider
// (PRD §5.5 gap #5).
function AddRowName({ entry }: { entry: ProviderCatalogEntry }): ReactNode {
  if (entry.contractBacked) return entry.label;
  return (
    <span style={nameRowStyle}>
      <span>{entry.label}</span>
      <Badge tone="neutral">
        {entry.comingSoon === true ? "coming soon" : "compatible"}
      </Badge>
    </span>
  );
}
