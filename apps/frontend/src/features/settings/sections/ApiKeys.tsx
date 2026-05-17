// PR B3 / 8.0.3g — personal API keys settings panel.
// PR 8.2 — Personal | Workspace tab strip.
// PR 8.3 — Workspace tab now wires to real workspace-issued admin tokens.
//
// Both tabs share a single body component (`ApiKeysBody`) that takes the
// API surface as a prop. Each tab instance owns its own keys / draft /
// revealed state so switching tabs doesn't blow away in-flight UI state.

import type {
  ApiKeyKind,
  ApiKeyListResponse,
  ApiKeySummary,
  CreateApiKeyRequest,
  CreateApiKeyResponse,
} from "@enterprise-search/api-types";
import {
  Button,
  Card,
  Field,
  TextInput,
} from "@enterprise-search/design-system";
import type { ReactElement } from "react";
import { useCallback, useEffect, useState } from "react";
import {
  createMyApiKey,
  createWorkspaceApiKey,
  listMyApiKeys,
  listWorkspaceApiKeys,
  revokeMyApiKey,
  revokeWorkspaceApiKey,
  rotateMyApiKey,
  rotateWorkspaceApiKey,
} from "../../../api/meApi";
import { useAuth } from "../../auth/AuthContext";
import { errorMessage } from "../../../utils/errors";

/** API surface for one scope. Lets the body stay scope-agnostic. */
interface ApiKeysOps {
  list: () => Promise<ApiKeyListResponse>;
  create: (request: CreateApiKeyRequest) => Promise<CreateApiKeyResponse>;
  revoke: (apiKeyId: string) => Promise<void>;
  rotate: (apiKeyId: string) => Promise<CreateApiKeyResponse>;
}

const PERSONAL_OPS: ApiKeysOps = {
  list: listMyApiKeys,
  create: createMyApiKey,
  revoke: revokeMyApiKey,
  rotate: rotateMyApiKey,
};

const WORKSPACE_OPS: ApiKeysOps = {
  list: listWorkspaceApiKeys,
  create: createWorkspaceApiKey,
  revoke: revokeWorkspaceApiKey,
  rotate: rotateWorkspaceApiKey,
};

interface RevealedKey {
  api_key_id: string;
  label: string;
  plaintext: string;
}

export function ApiKeys(): ReactElement {
  const auth = useAuth();
  const isAdmin =
    auth.identity?.permission_scopes?.includes("admin:users") ?? false;
  const [tab, setTab] = useState<ApiKeyKind>("personal");

  // Force-back to personal if a non-admin somehow lands on workspace
  // (shouldn't happen — the tab is disabled — but defensive).
  useEffect(() => {
    if (tab === "workspace" && !isAdmin) {
      setTab("personal");
    }
  }, [tab, isAdmin]);

  return (
    <div className="settings-section">
      <h2>API keys</h2>
      <div className="api-keys__tabs" role="tablist" aria-label="API key scope">
        <button
          type="button"
          role="tab"
          aria-selected={tab === "personal"}
          className={
            tab === "personal"
              ? "api-keys__tab api-keys__tab--active"
              : "api-keys__tab"
          }
          onClick={() => setTab("personal")}
        >
          Personal
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === "workspace"}
          className={
            tab === "workspace"
              ? "api-keys__tab api-keys__tab--active"
              : "api-keys__tab"
          }
          onClick={() => setTab("workspace")}
          disabled={!isAdmin}
          title={
            isAdmin
              ? "Workspace-issued tokens"
              : "Workspace tokens are admin-only"
          }
        >
          Workspace{" "}
          <span className="settings-nav__badge settings-nav__badge--admin">
            Admin
          </span>
        </button>
      </div>

      {tab === "personal" ? (
        <ApiKeysBody
          scope="personal"
          ops={PERSONAL_OPS}
          intro="Personal bearer tokens for CI / scripts. The full secret is shown only once at creation — copy it now or rotate the key."
        />
      ) : null}

      {tab === "workspace" ? (
        <ApiKeysBody
          scope="workspace"
          ops={WORKSPACE_OPS}
          intro="Workspace-issued tokens for shared automations. Any admin can revoke. Audit attribution stays with the admin who minted the key."
        />
      ) : null}
    </div>
  );
}

function ApiKeysBody({
  scope,
  ops,
  intro,
}: {
  scope: ApiKeyKind;
  ops: ApiKeysOps;
  intro: string;
}): ReactElement {
  const [keys, setKeys] = useState<readonly ApiKeySummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [draftLabel, setDraftLabel] = useState("");
  const [busy, setBusy] = useState(false);
  const [revealed, setRevealed] = useState<RevealedKey | null>(null);

  const refresh = useCallback(() => {
    ops
      .list()
      .then((response: ApiKeyListResponse) => {
        setKeys(response.keys);
        setError(null);
      })
      .catch((err: unknown) => {
        setError(errorMessage(err, "Could not load keys."));
      });
  }, [ops]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const onCreate = useCallback(() => {
    const label = draftLabel.trim();
    if (!label) {
      setError("Label is required.");
      return;
    }
    setBusy(true);
    setError(null);
    ops
      .create({ label })
      .then((response: CreateApiKeyResponse) => {
        setRevealed({
          api_key_id: response.key.id,
          label: response.key.label,
          plaintext: response.plaintext,
        });
        setDraftLabel("");
        refresh();
      })
      .catch((err: unknown) => {
        setError(errorMessage(err, "Could not create key."));
      })
      .finally(() => setBusy(false));
  }, [draftLabel, ops, refresh]);

  const onRevoke = useCallback(
    (api_key_id: string) => {
      ops
        .revoke(api_key_id)
        .then(() => refresh())
        .catch((err: unknown) =>
          setError(errorMessage(err, "Could not revoke key.")),
        );
    },
    [ops, refresh],
  );

  const onRotate = useCallback(
    (api_key_id: string) => {
      ops
        .rotate(api_key_id)
        .then((response) => {
          setRevealed({
            api_key_id: response.key.id,
            label: response.key.label,
            plaintext: response.plaintext,
          });
          refresh();
        })
        .catch((err: unknown) =>
          setError(errorMessage(err, "Could not rotate key.")),
        );
    },
    [ops, refresh],
  );

  return (
    <>
      <p>{intro}</p>

      <Card>
        <Field
          label={scope === "personal" ? "New API key" : "New workspace key"}
          hint="Choose a memorable label. Keys inherit your account scopes."
        >
          <div className="settings-row">
            <TextInput
              type="text"
              value={draftLabel}
              maxLength={128}
              placeholder="e.g. ci-bot, deploy-prod"
              onChange={(event) => setDraftLabel(event.target.value)}
            />
            <Button
              variant="primary"
              onClick={onCreate}
              disabled={busy || !draftLabel.trim()}
            >
              {busy ? "Creating…" : "Create key"}
            </Button>
          </div>
        </Field>
      </Card>

      {revealed && (
        <Card>
          <Field
            label={`New key for "${revealed.label}"`}
            hint="Copy this now. The server stores only the hash; you cannot retrieve it again."
          >
            <code className="settings-code-block">{revealed.plaintext}</code>
            <Button variant="secondary" onClick={() => setRevealed(null)}>
              I've saved it
            </Button>
          </Field>
        </Card>
      )}

      <Card>
        <Field label="Active keys">
          {keys === null ? (
            <p>Loading…</p>
          ) : keys.length === 0 ? (
            <p className="settings-meta">No keys yet.</p>
          ) : (
            <ul className="settings-key-list">
              {keys.map((key) => (
                <li key={key.id} className="settings-key-row">
                  <div>
                    <strong>{key.label}</strong>
                    <small>
                      {" "}
                      · prefix <code>{key.key_prefix}</code> · created{" "}
                      {new Date(key.created_at).toLocaleDateString()}
                      {key.last_used_at
                        ? ` · last used ${key.last_used_at}`
                        : " · never used"}
                      {key.rotated_from_id ? " · rotated" : ""}
                    </small>
                  </div>
                  <div className="settings-key-actions">
                    <Button
                      variant="secondary"
                      onClick={() => onRotate(key.id)}
                    >
                      Rotate
                    </Button>
                    <Button variant="danger" onClick={() => onRevoke(key.id)}>
                      Revoke
                    </Button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Field>
      </Card>

      {error && (
        <Card>
          <p role="alert">{error}</p>
        </Card>
      )}
    </>
  );
}
