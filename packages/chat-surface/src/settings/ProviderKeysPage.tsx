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
   * Known default-model chips per provider slug. The summary contract carries
   * no model field yet (PRD §5.5 drift), so the host may supply chips for
   * server-loaded keys; in-session Add-flow choices are merged on top.
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

const nameRowStyle: CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: "var(--space-sm)",
};

const listStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: "var(--space-sm)",
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
        const summary = await port.save(target.entry.id, apiKey);
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

  const chipFor = (slug: string): string | undefined =>
    chosenModels[slug] ?? modelChips?.[slug];

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
                        logo={
                          <span style={logoGlyphStyle}>
                            {entry.label.charAt(0)}
                          </span>
                        }
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
                              <Icon name="trash" size={14} />
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
                    logo={
                      <span style={logoGlyphStyle}>
                        {entry.label.charAt(0)}
                      </span>
                    }
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
