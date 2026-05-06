// PR B3 / 8.0.3g — personal API keys settings panel.
//
// Lists active keys, mints new ones (showing the plaintext exactly
// once), revokes, and rotates. The plaintext returned by POST /
// rotate is stored only in transient component state and cleared
// when the dismiss-revealed-secret button fires.

import type {
  ApiKeyListResponse,
  ApiKeySummary,
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
  listMyApiKeys,
  revokeMyApiKey,
  rotateMyApiKey,
} from "../../../api/meApi";

interface RevealedKey {
  api_key_id: string;
  label: string;
  plaintext: string;
}

export function ApiKeys(): ReactElement {
  const [keys, setKeys] = useState<readonly ApiKeySummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [draftLabel, setDraftLabel] = useState("");
  const [busy, setBusy] = useState(false);
  const [revealed, setRevealed] = useState<RevealedKey | null>(null);

  const refresh = useCallback(() => {
    listMyApiKeys()
      .then((response: ApiKeyListResponse) => {
        setKeys(response.keys);
        setError(null);
      })
      .catch((err: unknown) => {
        setError(err instanceof Error ? err.message : "Could not load keys.");
      });
  }, []);

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
    createMyApiKey({ label })
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
        setError(err instanceof Error ? err.message : "Could not create key.");
      })
      .finally(() => setBusy(false));
  }, [draftLabel, refresh]);

  const onRevoke = useCallback(
    (api_key_id: string) => {
      revokeMyApiKey(api_key_id)
        .then(() => refresh())
        .catch((err: unknown) =>
          setError(
            err instanceof Error ? err.message : "Could not revoke key.",
          ),
        );
    },
    [refresh],
  );

  const onRotate = useCallback(
    (api_key_id: string) => {
      rotateMyApiKey(api_key_id)
        .then((response) => {
          setRevealed({
            api_key_id: response.key.id,
            label: response.key.label,
            plaintext: response.plaintext,
          });
          refresh();
        })
        .catch((err: unknown) =>
          setError(
            err instanceof Error ? err.message : "Could not rotate key.",
          ),
        );
    },
    [refresh],
  );

  return (
    <div className="settings-section">
      <h2>API keys</h2>
      <p>
        Personal bearer tokens for CI / scripts. The full secret is shown only
        once at creation — copy it now or rotate the key.
      </p>

      <Card>
        <Field
          label="New API key"
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
        <Field
          label="Active keys"
          hint={keys === null ? "Loading…" : `${keys.length} active`}
        >
          {keys === null ? null : keys.length === 0 ? (
            <p>No active keys yet. Create one above.</p>
          ) : (
            <ul className="settings-key-list">
              {keys.map((key) => (
                <li key={key.id} className="settings-key-row">
                  <div>
                    <strong>{key.label}</strong>
                    <code>atlas_pk_{key.key_prefix}_…</code>
                    <small>
                      Created {key.created_at}
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
    </div>
  );
}
