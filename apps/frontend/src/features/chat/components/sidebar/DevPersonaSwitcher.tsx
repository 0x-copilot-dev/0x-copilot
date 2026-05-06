/**
 * W0.1 — Dev-only persona switcher.
 *
 * Renders a dropdown of personas from the dev IdP (``GET /v1/dev/personas``)
 * and mints a fresh bearer for the selected persona on change. After mint,
 * the bearer is written to ``localStorage`` and the page soft-reloads —
 * every subsequent request runs as the new persona.
 *
 * Production builds tree-shake this component because every invocation is
 * gated by ``import.meta.env.DEV``.
 */

import { useEffect, useState, type ReactElement } from "react";

import {
  listDevPersonas,
  loadActivePersonaSlug,
  mintDevBearer,
  persistActivePersonaSlug,
  type DevPersonaSummary,
} from "../../../auth/devIdp";

const BEARER_STORAGE_KEY = "enterprise.auth.bearer";

export function DevPersonaSwitcher(): ReactElement | null {
  if (!import.meta.env.DEV) return null;

  const [personas, setPersonas] = useState<DevPersonaSummary[]>([]);
  const [active, setActive] = useState<string>(loadActivePersonaSlug());
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void listDevPersonas()
      .then((rows) => {
        if (!cancelled) setPersonas(rows);
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "failed to load");
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const onPick = async (slug: string) => {
    if (slug === active) return;
    try {
      const result = await mintDevBearer(slug);
      window.localStorage.setItem(BEARER_STORAGE_KEY, result.bearer);
      persistActivePersonaSlug(slug);
      setActive(slug);
      window.location.reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "mint failed");
    }
  };

  if (error) {
    return (
      <p className="aui-user-card__menu-error">Dev IdP unreachable — {error}</p>
    );
  }
  if (personas.length === 0) {
    return null;
  }

  return (
    <div className="aui-user-card__menu-section aui-user-card__dev-persona">
      <p className="aui-user-card__menu-heading">Dev persona</p>
      <select
        aria-label="Dev persona"
        className="aui-user-card__dev-persona-select"
        value={active}
        onChange={(e) => void onPick(e.target.value)}
      >
        {personas.map((p) => (
          <option key={p.slug} value={p.slug}>
            {p.display_name} · {p.org_slug} · {p.roles.join(",")}
          </option>
        ))}
      </select>
    </div>
  );
}
