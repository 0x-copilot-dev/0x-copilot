// WebhooksRoute — `/connectors/webhooks` sub-route binder
// (connectors-prd §4.10 + §9 HMAC management UX).
//
// Owns the secret-passing pattern (connectors-prd §9.2 + charter): when
// the user creates a webhook, the server returns `secret_plaintext` on
// the response envelope EXACTLY ONCE. The route holds the plaintext in
// component state, passes it once to the wizard's `RevealOnce`, and
// clears it when the wizard unmounts. The plaintext is NEVER persisted
// to localStorage, sessionStorage, secret storage, or any other side
// channel. Every subsequent GET of the webhook returns the redacted
// shape (no plaintext channel).
//
// Same lifecycle invariant applies for rotation: the rotate-response
// plaintext lives in state while the reveal window is open and gets
// cleared as soon as the user dismisses it.

import { useCallback, useEffect, useState, type ReactElement } from "react";

import { RevealOnce } from "@enterprise-search/chat-surface";
import type { Webhook } from "@enterprise-search/api-types";

import type { RequestIdentity } from "../../api/config";
import {
  createWebhook,
  deleteWebhook,
  fetchWebhooks,
  patchWebhook,
  rotateWebhookSecret,
  testFireWebhook,
  type CreateWebhookRequest,
  type WebhookListResponse,
} from "../../api/webhooksApi";
import { errorMessage } from "../../utils/errors";
import { maskWebhookUrl } from "./adapters";

interface WebhooksRouteProps {
  readonly identity: RequestIdentity;
  readonly onClose: () => void;
}

type ViewState =
  | { readonly kind: "loading" }
  | { readonly kind: "error"; readonly message: string }
  | { readonly kind: "ready"; readonly items: ReadonlyArray<Webhook> };

/**
 * One-shot reveal payload. Held in component state ONLY. Cleared when
 * the user dismisses the reveal banner OR when the wizard unmounts.
 */
interface RevealPayload {
  readonly webhookId: string;
  readonly secret: string;
  /** Set only on rotation when the previous secret is still in its grace
   *  window — surfaced through a second `RevealOnce` so receivers can
   *  validate both. */
  readonly graceSecret: string | null;
}

// ===========================================================================
// WebhooksRoute
// ===========================================================================

export function WebhooksRoute({
  identity,
  onClose,
}: WebhooksRouteProps): ReactElement {
  const [state, setState] = useState<ViewState>({ kind: "loading" });
  const [reloadToken, setReloadToken] = useState(0);
  const [pendingError, setPendingError] = useState<string | null>(null);
  const [wizardOpen, setWizardOpen] = useState(false);
  // Reveal payload — plaintext-secret state. NEVER persisted; cleared on
  // dismiss + on wizard unmount (the cleanup effect below).
  const [reveal, setReveal] = useState<RevealPayload | null>(null);

  // ---- Initial fetch -------------------------------------------------
  useEffect(() => {
    let cancelled = false;
    setState({ kind: "loading" });

    fetchWebhooks(identity, { limit: 50 })
      .then((list: WebhookListResponse) => {
        if (cancelled) return;
        setState({ kind: "ready", items: list.items });
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        setState({
          kind: "error",
          message: errorMessage(error, "Could not load webhooks."),
        });
      });

    return () => {
      cancelled = true;
    };
  }, [identity, reloadToken]);

  // ---- Reveal-payload safety ----------------------------------------
  // When the wizard closes the reveal state MUST be cleared. Belt-and-
  // braces: also clear on route unmount.
  useEffect(() => {
    if (!wizardOpen) {
      setReveal(null);
    }
  }, [wizardOpen]);
  useEffect(() => {
    return () => {
      setReveal(null);
    };
  }, []);

  const mergeWebhook = useCallback((webhook: Webhook): void => {
    setState((prev) => {
      if (prev.kind !== "ready") return prev;
      const idx = prev.items.findIndex((w) => w.id === webhook.id);
      if (idx === -1) {
        return { ...prev, items: [webhook, ...prev.items] };
      }
      const next = prev.items.slice();
      next[idx] = webhook;
      return { ...prev, items: next };
    });
  }, []);

  const dropWebhook = useCallback((id: string): void => {
    setState((prev) => {
      if (prev.kind !== "ready") return prev;
      return { ...prev, items: prev.items.filter((w) => w.id !== id) };
    });
  }, []);

  // ---- Mutations -----------------------------------------------------

  const handleCreate = useCallback(
    async (body: CreateWebhookRequest): Promise<void> => {
      setPendingError(null);
      try {
        const res = await createWebhook(identity, body);
        // Persist the row but NOT the plaintext. Plaintext stays in
        // reveal-state until the user dismisses.
        mergeWebhook(res.webhook);
        setReveal({
          webhookId: res.webhook.id,
          secret: res.secret_plaintext,
          graceSecret: null,
        });
      } catch (error: unknown) {
        setPendingError(errorMessage(error, "Could not create webhook."));
      }
    },
    [identity, mergeWebhook],
  );

  const handleRotate = useCallback(
    async (id: string): Promise<void> => {
      setPendingError(null);
      try {
        const res = await rotateWebhookSecret(identity, id);
        mergeWebhook(res.webhook);
        setReveal({
          webhookId: res.webhook.id,
          secret: res.secret_plaintext,
          graceSecret: res.grace_secret_plaintext,
        });
        setWizardOpen(true);
      } catch (error: unknown) {
        setPendingError(errorMessage(error, "Could not rotate secret."));
      }
    },
    [identity, mergeWebhook],
  );

  const handleDelete = useCallback(
    async (id: string): Promise<void> => {
      setPendingError(null);
      try {
        await deleteWebhook(identity, id);
        dropWebhook(id);
      } catch (error: unknown) {
        setPendingError(errorMessage(error, "Could not delete webhook."));
      }
    },
    [identity, dropWebhook],
  );

  const handleTogglePause = useCallback(
    async (webhook: Webhook): Promise<void> => {
      setPendingError(null);
      try {
        const next = await patchWebhook(identity, webhook.id, {
          status: webhook.status === "active" ? "paused" : "active",
        });
        mergeWebhook(next);
      } catch (error: unknown) {
        setPendingError(errorMessage(error, "Could not update webhook."));
      }
    },
    [identity, mergeWebhook],
  );

  const handleTestFire = useCallback(
    async (id: string): Promise<void> => {
      setPendingError(null);
      try {
        const res = await testFireWebhook(identity, id);
        if (!res.response_ok) {
          setPendingError(
            res.error !== undefined
              ? `Test fire failed: ${res.error}`
              : `Test fire returned ${res.response_status ?? "no response"}.`,
          );
        }
      } catch (error: unknown) {
        setPendingError(errorMessage(error, "Could not test-fire webhook."));
      }
    },
    [identity],
  );

  // ---- Wizard mount/unmount: clears reveal on close -----------------

  const closeWizard = useCallback((): void => {
    // The reveal-clearing effect (above) clears the secret when
    // wizardOpen flips false. We rely on that single source of truth
    // rather than calling setReveal(null) here too.
    setWizardOpen(false);
  }, []);

  // ---- Render --------------------------------------------------------

  return (
    <section
      aria-label="Webhooks"
      data-testid="webhooks-route"
      data-state={state.kind}
      style={{
        padding: 24,
        boxSizing: "border-box",
        display: "flex",
        flexDirection: "column",
        gap: 16,
        height: "100%",
        overflow: "auto",
      }}
    >
      <header
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
        }}
      >
        <div>
          <h2 style={{ margin: 0, fontSize: 18 }}>Webhooks</h2>
          <div style={{ fontSize: 12, color: "var(--color-text-muted)" }}>
            Outbound HMAC-signed webhooks for routines and integrations.
          </div>
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          <button
            type="button"
            data-testid="webhooks-route-add"
            onClick={() => setWizardOpen(true)}
          >
            Add webhook
          </button>
          <button
            type="button"
            data-testid="webhooks-route-close"
            onClick={onClose}
          >
            Back
          </button>
        </div>
      </header>

      {pendingError !== null && (
        <div
          role="status"
          data-testid="webhooks-route-pending-error"
          style={{
            padding: 12,
            border: "1px solid var(--color-border-strong)",
            borderRadius: 8,
            fontSize: 13,
          }}
        >
          {pendingError}
        </div>
      )}

      {state.kind === "loading" && (
        <div data-testid="webhooks-route-loading">Loading webhooks…</div>
      )}

      {state.kind === "error" && (
        <div role="alert" data-testid="webhooks-route-error">
          <div style={{ fontWeight: 600 }}>{state.message}</div>
          <button
            type="button"
            data-testid="webhooks-route-retry"
            onClick={() => setReloadToken((t) => t + 1)}
          >
            Retry
          </button>
        </div>
      )}

      {state.kind === "ready" && (
        <ul
          data-testid="webhooks-route-list"
          style={{ listStyle: "none", margin: 0, padding: 0 }}
        >
          {state.items.length === 0 ? (
            <li
              data-testid="webhooks-route-empty"
              style={{ color: "var(--color-text-muted)", fontSize: 13 }}
            >
              No webhooks yet.
            </li>
          ) : (
            state.items.map((webhook) => (
              <li
                key={webhook.id}
                data-testid="webhooks-route-row"
                data-webhook-id={webhook.id}
                data-webhook-status={webhook.status}
                style={{
                  padding: 12,
                  borderBottom: "1px solid var(--color-border)",
                  display: "flex",
                  gap: 12,
                  alignItems: "center",
                }}
              >
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: 14, fontWeight: 600 }}>
                    {maskWebhookUrl(webhook)}
                  </div>
                  <div
                    style={{
                      fontSize: 12,
                      color: "var(--color-text-muted)",
                    }}
                  >
                    {webhook.status} · {webhook.secret_strategy} ·{" "}
                    {webhook.hmac_algo}
                    {webhook.last_fire_at !== null
                      ? ` · last fired ${webhook.last_fire_at}`
                      : ""}
                  </div>
                </div>
                <button
                  type="button"
                  data-testid="webhooks-route-test-fire"
                  data-webhook-id={webhook.id}
                  onClick={() => {
                    void handleTestFire(webhook.id);
                  }}
                >
                  Test fire
                </button>
                <button
                  type="button"
                  data-testid="webhooks-route-rotate"
                  data-webhook-id={webhook.id}
                  onClick={() => {
                    void handleRotate(webhook.id);
                  }}
                >
                  Rotate
                </button>
                <button
                  type="button"
                  data-testid="webhooks-route-toggle"
                  data-webhook-id={webhook.id}
                  onClick={() => {
                    void handleTogglePause(webhook);
                  }}
                >
                  {webhook.status === "active" ? "Pause" : "Activate"}
                </button>
                <button
                  type="button"
                  data-testid="webhooks-route-delete"
                  data-webhook-id={webhook.id}
                  onClick={() => {
                    void handleDelete(webhook.id);
                  }}
                >
                  Delete
                </button>
              </li>
            ))
          )}
        </ul>
      )}

      {wizardOpen && (
        <WebhookCreateWizardScaffold
          onCancel={closeWizard}
          onCreate={async (body) => {
            await handleCreate(body);
          }}
          reveal={reveal}
          onDismissReveal={() => {
            // Single mutation: drop the plaintext. The wizard stays open
            // long enough for the user to see the dismissal feedback;
            // they click Close (or Cancel) to unmount it, at which
            // point the cleanup effect also runs.
            setReveal(null);
          }}
        />
      )}
    </section>
  );
}

// ===========================================================================
// WebhookCreateWizardScaffold — local mount until the chat-surface
// `<WebhookCreateWizard>` lands. The scaffold owns the SAME secret-passing
// contract: plaintext arrives via the `reveal` prop, never re-reads, and
// is dropped via `onDismissReveal`. When the package wizard lands, the
// scaffold is replaced 1:1 with no change to this route's data flow.
// ===========================================================================

interface WebhookCreateWizardScaffoldProps {
  readonly onCancel: () => void;
  readonly onCreate: (body: CreateWebhookRequest) => Promise<void>;
  readonly reveal: RevealPayload | null;
  readonly onDismissReveal: () => void;
}

function WebhookCreateWizardScaffold({
  onCancel,
  onCreate,
  reveal,
  onDismissReveal,
}: WebhookCreateWizardScaffoldProps): ReactElement {
  const [url, setUrl] = useState("");
  const [strategy, setStrategy] = useState<"rotating" | "static">("rotating");
  const [staticSecret, setStaticSecret] = useState("");
  const [submitting, setSubmitting] = useState(false);

  // Dummy clipboard adapter — RevealOnce expects a Promise-returning
  // `onCopy`. The web app surfaces real clipboard wiring through the
  // PortBundle; the wizard intentionally uses navigator.clipboard so
  // tests that mock window.navigator can verify the secret flow.
  const handleCopy = async (text: string): Promise<void> => {
    if (
      typeof navigator !== "undefined" &&
      navigator.clipboard !== undefined &&
      typeof navigator.clipboard.writeText === "function"
    ) {
      await navigator.clipboard.writeText(text);
    }
  };

  const handleSubmit = async (
    e: React.FormEvent<HTMLFormElement>,
  ): Promise<void> => {
    e.preventDefault();
    setSubmitting(true);
    try {
      const body: CreateWebhookRequest = {
        url,
        secret_strategy: strategy,
        ...(strategy === "static" && staticSecret.length > 0
          ? { secret_plaintext: staticSecret }
          : {}),
      };
      await onCreate(body);
    } finally {
      setSubmitting(false);
      // Best-effort: clear the wizard's own copy of the static secret.
      // The route's `reveal` state holds the plaintext for the reveal
      // window only; nothing else lingers.
      setStaticSecret("");
    }
  };

  return (
    <div
      data-testid="webhook-create-wizard"
      role="dialog"
      aria-label="Create webhook"
      style={{
        padding: 16,
        border: "1px solid var(--color-border-strong)",
        borderRadius: 12,
        display: "flex",
        flexDirection: "column",
        gap: 12,
      }}
    >
      {reveal === null ? (
        <form onSubmit={handleSubmit} style={{ display: "grid", gap: 8 }}>
          <label style={{ display: "grid", gap: 4 }}>
            <span style={{ fontSize: 12, fontWeight: 600 }}>Webhook URL</span>
            <input
              type="url"
              required
              data-testid="webhook-create-wizard-url"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="https://example.com/atlas-hook"
            />
          </label>
          <label style={{ display: "grid", gap: 4 }}>
            <span style={{ fontSize: 12, fontWeight: 600 }}>Secret</span>
            <select
              data-testid="webhook-create-wizard-strategy"
              value={strategy}
              onChange={(e) =>
                setStrategy(e.target.value as "rotating" | "static")
              }
            >
              <option value="rotating">Rotating (server-generated)</option>
              <option value="static">Static (you provide)</option>
            </select>
          </label>
          {strategy === "static" && (
            <label style={{ display: "grid", gap: 4 }}>
              <span style={{ fontSize: 12, fontWeight: 600 }}>
                Secret plaintext
              </span>
              <input
                type="password"
                data-testid="webhook-create-wizard-secret"
                value={staticSecret}
                onChange={(e) => setStaticSecret(e.target.value)}
              />
            </label>
          )}
          <div style={{ display: "flex", gap: 8, marginTop: 4 }}>
            <button
              type="submit"
              data-testid="webhook-create-wizard-submit"
              disabled={submitting || url.length === 0}
            >
              {submitting ? "Creating…" : "Create webhook"}
            </button>
            <button
              type="button"
              data-testid="webhook-create-wizard-cancel"
              onClick={onCancel}
              disabled={submitting}
            >
              Cancel
            </button>
          </div>
        </form>
      ) : (
        <div data-testid="webhook-create-wizard-reveal">
          <h3 style={{ marginTop: 0, fontSize: 14 }}>
            Save the secret now — it will not be shown again.
          </h3>
          <RevealOnce
            value={reveal.secret}
            maskedPlaceholder="••••••••"
            label="webhook secret"
            onCopy={handleCopy}
            onDismiss={onDismissReveal}
            testId="webhook-create-wizard-secret-reveal"
          />
          {reveal.graceSecret !== null && (
            <div style={{ marginTop: 12 }}>
              <h4 style={{ margin: 0, fontSize: 13 }}>
                Previous secret (grace window — 14 days)
              </h4>
              <RevealOnce
                value={reveal.graceSecret}
                maskedPlaceholder="••••••••"
                label="previous secret"
                onCopy={handleCopy}
                testId="webhook-create-wizard-grace-reveal"
              />
            </div>
          )}
          <div style={{ marginTop: 12 }}>
            <button
              type="button"
              data-testid="webhook-create-wizard-close"
              onClick={onCancel}
            >
              Close
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
