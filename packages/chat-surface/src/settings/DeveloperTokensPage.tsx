// Developer tokens — Settings → Advanced (DESIGN-SPEC §4 · PRD PR-5.9).
//
//   * Local CLI token list — name + masked (`key_prefix…`) + last-used + Revoke
//     (FR-5.24).
//   * "Create a token" — mints a token whose plaintext is shown ONCE
//     ("shown once, then keychain"); reads never carry the secret again.
//
// The page holds NO long-lived plaintext: the create response's `plaintext` is
// revealed in a dismissable panel and dropped from state the moment the user
// acknowledges it. All minting / listing / revocation is a host concern behind
// the injected `DeveloperTokensPort`, so the page is framework-agnostic and
// testable with a mock port. Create / Revoke are immediate one-shot actions →
// `onToast`, never the dirty savebar (FR-5.7).
//
// Substrate-agnostic; colors resolve only to design-system v2 tokens.

import {
  useCallback,
  useEffect,
  useId,
  useState,
  type CSSProperties,
  type ReactElement,
} from "react";

import type { ApiKeySummary } from "@0x-copilot/api-types";
import { Button, TextInput } from "@0x-copilot/design-system";

import { Krow, SecHead, SetCard, SetNote } from "./SettingsChrome";
import {
  lastUsedLabel,
  maskDeveloperToken,
  type DeveloperTokensPort,
} from "./data/developerTokens";

// DESIGN-SPEC §4 "shown once, then keychain".
export const DEVELOPER_TOKENS_ONCE_NOTE =
  "A token's secret is shown once, right after you create it, then stored in your keychain. Copy it now — you can't see it again.";

export interface DeveloperTokensPageProps {
  /** Host-injected minting / listing / revocation seam. */
  readonly port: DeveloperTokensPort;
  /**
   * One-shot confirmation sink (wire to `SettingsSurfaceController.showToast`).
   * Create / Revoke fire it — the page never uses the dirty savebar.
   */
  readonly onToast?: (message: string) => void;
}

/** The just-minted token, revealed once. */
interface RevealedToken {
  readonly id: string;
  readonly label: string;
  readonly plaintext: string;
}

function toMessage(err: unknown, fallback: string): string {
  if (err instanceof Error && err.message) return err.message;
  if (typeof err === "string" && err) return err;
  return fallback;
}

// ---------------------------------------------------------------------------
// Styles (token-only chrome).
// ---------------------------------------------------------------------------

const sectionStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: "var(--space-sm)",
};

const listStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: "var(--space-sm)",
};

const createRowStyle: CSSProperties = {
  display: "flex",
  flexWrap: "wrap",
  alignItems: "center",
  gap: "var(--space-sm)",
};

const labelInputStyle: CSSProperties = {
  flex: "1 1 220px",
  minWidth: 180,
};

const revealPanelStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: "var(--space-sm)",
  padding: "var(--space-md)",
  borderRadius: "var(--radius-md)",
  border: "1px solid var(--color-accent)",
  backgroundColor: "var(--color-accent-soft)",
};

const secretStyle: CSSProperties = {
  margin: 0,
  padding: "8px 10px",
  borderRadius: "var(--radius-sm)",
  border: "1px solid var(--color-border-strong)",
  backgroundColor: "var(--color-surface)",
  fontFamily: "var(--font-mono)",
  fontSize: "var(--font-size-xs)",
  color: "var(--color-text)",
  wordBreak: "break-all",
};

const mutedNoteStyle: CSSProperties = {
  margin: 0,
  fontSize: "var(--font-size-sm)",
  color: "var(--color-text-muted)",
};

const rowErrorStyle: CSSProperties = {
  margin: "2px 0 0",
  fontSize: "var(--font-size-xs)",
  color: "var(--color-danger)",
};

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export function DeveloperTokensPage({
  port,
  onToast,
}: DeveloperTokensPageProps): ReactElement {
  const reactId = useId();
  const labelInputId = `${reactId}-token-label`;

  const [tokens, setTokens] = useState<readonly ApiKeySummary[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [label, setLabel] = useState("");
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);
  const [revealed, setRevealed] = useState<RevealedToken | null>(null);
  const [revoking, setRevoking] = useState<Record<string, boolean>>({});
  const [rowErrors, setRowErrors] = useState<Record<string, string>>({});

  const refresh = useCallback(() => {
    setTokens(null);
    setLoadError(null);
    port
      .list()
      .then((next) => {
        setTokens(next);
        setLoadError(null);
      })
      .catch((err: unknown) => {
        setLoadError(toMessage(err, "Could not load developer tokens."));
      });
  }, [port]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const handleCreate = useCallback(() => {
    const trimmed = label.trim();
    if (creating) return;
    if (trimmed.length === 0) {
      setCreateError("Give the token a name so you can recognize it later.");
      return;
    }
    setCreating(true);
    setCreateError(null);
    port
      .create(trimmed)
      .then((res) => {
        setTokens((prev) => [res.key, ...(prev ?? [])]);
        setRevealed({
          id: res.key.id,
          label: res.key.label,
          plaintext: res.plaintext,
        });
        setLabel("");
        onToast?.(`Created “${res.key.label}”.`);
      })
      .catch((err: unknown) => {
        setCreateError(toMessage(err, "Could not create token."));
      })
      .finally(() => {
        setCreating(false);
      });
  }, [creating, label, port, onToast]);

  const handleRevoke = useCallback(
    (token: ApiKeySummary) => {
      if (revoking[token.id]) return;
      setRevoking((prev) => ({ ...prev, [token.id]: true }));
      setRowErrors((prev) => {
        const next = { ...prev };
        delete next[token.id];
        return next;
      });
      port
        .revoke(token.id)
        .then(() => {
          setTokens((prev) => (prev ?? []).filter((t) => t.id !== token.id));
          // If we just revoked the token we were still revealing, drop the
          // secret from the panel too.
          setRevealed((prev) => (prev?.id === token.id ? null : prev));
          onToast?.(`Revoked “${token.label}”.`);
        })
        .catch((err: unknown) => {
          setRowErrors((prev) => ({
            ...prev,
            [token.id]: toMessage(err, "Could not revoke token."),
          }));
        })
        .finally(() => {
          setRevoking((prev) => {
            const next = { ...prev };
            delete next[token.id];
            return next;
          });
        });
    },
    [port, onToast, revoking],
  );

  return (
    <SetCard
      title="Developer tokens"
      meta="Local tokens the copilot CLI uses to authenticate on this device."
      data-testid="developer-tokens-page"
    >
      <SetNote data-testid="developer-tokens-once-note">
        {DEVELOPER_TOKENS_ONCE_NOTE}
      </SetNote>

      {/* Create ------------------------------------------------------------- */}
      <section style={sectionStyle}>
        <SecHead>Create a token</SecHead>
        <div style={createRowStyle}>
          <TextInput
            id={labelInputId}
            value={label}
            onChange={(event) => setLabel(event.target.value)}
            placeholder="e.g. laptop CLI"
            aria-label="Token name"
            autoComplete="off"
            spellCheck={false}
            style={labelInputStyle}
            data-testid="developer-tokens-label"
          />
          <Button
            type="button"
            variant="primary"
            onClick={handleCreate}
            disabled={creating}
            data-testid="developer-tokens-create"
          >
            {creating ? "Creating…" : "Create a token"}
          </Button>
        </div>
        {createError !== null ? (
          <p
            role="alert"
            style={rowErrorStyle}
            data-testid="developer-tokens-create-error"
          >
            {createError}
          </p>
        ) : null}

        {revealed !== null ? (
          <div style={revealPanelStyle} data-testid="developer-tokens-reveal">
            <span
              style={{
                fontSize: "var(--font-size-sm)",
                fontWeight: "var(--font-weight-medium)",
                color: "var(--color-text)",
              }}
            >
              “{revealed.label}” — copy this now, it won't be shown again.
            </span>
            <code style={secretStyle} data-testid="developer-tokens-secret">
              {revealed.plaintext}
            </code>
            <div>
              <Button
                type="button"
                variant="secondary"
                size="sm"
                onClick={() => setRevealed(null)}
                data-testid="developer-tokens-reveal-done"
              >
                Done
              </Button>
            </div>
          </div>
        ) : null}
      </section>

      {/* List --------------------------------------------------------------- */}
      <section style={sectionStyle}>
        <SecHead>Your tokens</SecHead>

        {tokens === null && loadError === null ? (
          <p style={mutedNoteStyle} data-testid="developer-tokens-loading">
            Loading developer tokens…
          </p>
        ) : loadError !== null ? (
          <div style={sectionStyle}>
            <p
              role="alert"
              style={{ margin: 0, color: "var(--color-danger)" }}
              data-testid="developer-tokens-error"
            >
              {loadError}
            </p>
            <div>
              <Button
                type="button"
                variant="secondary"
                size="sm"
                onClick={refresh}
                data-testid="developer-tokens-retry"
              >
                Retry
              </Button>
            </div>
          </div>
        ) : (tokens ?? []).length === 0 ? (
          <p style={mutedNoteStyle} data-testid="developer-tokens-empty">
            No developer tokens yet. Create one above to use the copilot CLI.
          </p>
        ) : (
          <div style={listStyle}>
            {(tokens ?? []).map((token) => (
              <div key={token.id}>
                <Krow
                  data-testid={`developer-token-row-${token.id}`}
                  name={token.label}
                  sub={
                    <>
                      {maskDeveloperToken(token)} · {lastUsedLabel(token)}
                    </>
                  }
                  actions={
                    <Button
                      type="button"
                      variant="danger"
                      size="sm"
                      aria-label={`Revoke ${token.label}`}
                      disabled={revoking[token.id] === true}
                      onClick={() => handleRevoke(token)}
                      data-testid={`developer-token-revoke-${token.id}`}
                    >
                      Revoke
                    </Button>
                  }
                />
                {rowErrors[token.id] !== undefined ? (
                  <p
                    role="alert"
                    style={rowErrorStyle}
                    data-testid={`developer-token-row-error-${token.id}`}
                  >
                    {rowErrors[token.id]}
                  </p>
                ) : null}
              </div>
            ))}
          </div>
        )}
      </section>
    </SetCard>
  );
}
