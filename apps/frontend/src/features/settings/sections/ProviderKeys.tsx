// BYOK — bring-your-own model provider keys (Settings → AI & data).
//
// One row per supported provider (OpenAI / Anthropic / Google Gemini).
// A row is either:
//   * empty      — masked password input + Save
//   * saved      — key_hint + updated date + Replace / Remove
//   * replacing  — masked password input + Save / Cancel (hint stays
//                  active server-side until the new key lands)
//
// Security invariant mirrored from the wire contract
// (packages/api-types/src/providerKeys.ts): the plaintext key leaves
// this component exactly once, in the PUT body. Reads only ever carry
// the masked `key_hint`, so there is nothing to "reveal" here — do not
// add a show-key affordance.

import type {
  ProviderKeyProvider,
  ProviderKeySummary,
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
  deleteProviderKey,
  listProviderKeys,
  putProviderKey,
} from "../../../api/providerKeysApi";
import { errorMessage } from "../../../utils/errors";

interface ProviderRowSpec {
  provider: ProviderKeyProvider;
  label: string;
  placeholder: string;
}

const PROVIDERS: readonly ProviderRowSpec[] = [
  { provider: "openai", label: "OpenAI", placeholder: "sk-…" },
  { provider: "anthropic", label: "Anthropic", placeholder: "sk-ant-…" },
  { provider: "google", label: "Google Gemini", placeholder: "AIza…" },
];

export function ProviderKeys(): ReactElement {
  const [keys, setKeys] = useState<readonly ProviderKeySummary[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  const refresh = useCallback(() => {
    listProviderKeys()
      .then((response) => {
        setKeys(response.keys);
        setLoadError(null);
      })
      .catch((err: unknown) => {
        setLoadError(errorMessage(err, "Could not load provider keys."));
      });
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const handleSaved = useCallback((summary: ProviderKeySummary) => {
    setKeys((prev) => [
      ...(prev ?? []).filter((key) => key.provider !== summary.provider),
      summary,
    ]);
  }, []);

  const handleRemoved = useCallback((provider: ProviderKeyProvider) => {
    setKeys((prev) => (prev ?? []).filter((key) => key.provider !== provider));
  }, []);

  return (
    <div className="settings-section">
      <h2>Provider keys</h2>
      <p>
        Bring your own model provider keys. Your key is encrypted at rest and
        only used to run your own agents.
      </p>

      {loadError ? (
        <Card>
          <p role="alert">{loadError}</p>
        </Card>
      ) : keys === null ? (
        <Card>
          <p>Loading provider keys…</p>
        </Card>
      ) : (
        PROVIDERS.map((spec) => (
          <ProviderKeyRow
            key={spec.provider}
            spec={spec}
            summary={keys.find((key) => key.provider === spec.provider) ?? null}
            onSaved={handleSaved}
            onRemoved={handleRemoved}
          />
        ))
      )}
    </div>
  );
}

function ProviderKeyRow({
  spec,
  summary,
  onSaved,
  onRemoved,
}: {
  spec: ProviderRowSpec;
  summary: ProviderKeySummary | null;
  onSaved: (summary: ProviderKeySummary) => void;
  onRemoved: (provider: ProviderKeyProvider) => void;
}): ReactElement {
  const [draft, setDraft] = useState("");
  const [replacing, setReplacing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [rowError, setRowError] = useState<string | null>(null);

  const showInput = summary === null || replacing;

  const onSave = useCallback(() => {
    const apiKey = draft.trim();
    if (!apiKey || busy) {
      return;
    }
    setBusy(true);
    setRowError(null);
    putProviderKey(spec.provider, { api_key: apiKey })
      .then((saved) => {
        setDraft("");
        setReplacing(false);
        onSaved(saved);
      })
      .catch((err: unknown) => {
        setRowError(errorMessage(err, "Could not save key."));
      })
      .finally(() => setBusy(false));
  }, [busy, draft, onSaved, spec.provider]);

  const onRemove = useCallback(() => {
    if (busy) {
      return;
    }
    setBusy(true);
    setRowError(null);
    deleteProviderKey(spec.provider)
      .then(() => {
        setDraft("");
        setReplacing(false);
        onRemoved(spec.provider);
      })
      .catch((err: unknown) => {
        setRowError(errorMessage(err, "Could not remove key."));
      })
      .finally(() => setBusy(false));
  }, [busy, onRemoved, spec.provider]);

  return (
    <Card>
      {showInput ? (
        <Field
          label={`${spec.label} API key`}
          hint="Stored encrypted. Only the last 4 characters are ever shown again."
        >
          <div className="settings-row">
            <TextInput
              type="password"
              autoComplete="new-password"
              spellCheck={false}
              value={draft}
              placeholder={spec.placeholder}
              onChange={(event) => setDraft(event.target.value)}
            />
            <Button
              variant="primary"
              aria-label={`Save ${spec.label} key`}
              disabled={busy || !draft.trim()}
              onClick={onSave}
            >
              {busy ? "Saving…" : "Save"}
            </Button>
            {replacing ? (
              <Button
                variant="secondary"
                aria-label={`Cancel replacing ${spec.label} key`}
                onClick={() => {
                  setDraft("");
                  setReplacing(false);
                  setRowError(null);
                }}
              >
                Cancel
              </Button>
            ) : null}
          </div>
        </Field>
      ) : (
        <div className="settings-key-row">
          <div>
            <strong>{spec.label}</strong>
            <small>
              {" "}
              · key <code>{summary.key_hint}</code> · updated{" "}
              {new Date(summary.updated_at).toLocaleDateString()}
            </small>
          </div>
          <div className="settings-key-actions">
            <Button
              variant="secondary"
              aria-label={`Replace ${spec.label} key`}
              onClick={() => setReplacing(true)}
            >
              Replace
            </Button>
            <Button
              variant="danger"
              aria-label={`Remove ${spec.label} key`}
              disabled={busy}
              onClick={onRemove}
            >
              Remove
            </Button>
          </div>
        </div>
      )}
      {rowError ? <p role="alert">{rowError}</p> : null}
    </Card>
  );
}
