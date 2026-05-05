// PR 4.2 — Settings → Workspace branding panel.
//
// Renders workspace name, slug (with debounced uniqueness preview at the API
// boundary; the server is the source of truth), logo URL. Embeds the PR 1.6
// defaults form (model + connectors + retention) below as a "Defaults"
// subsection — we don't duplicate the logic, we mount the existing surface.
//
// Member view is read-only (form fields disabled). Admin gating happens at
// the route level; this UI hides the Save button when the request returns
// 403, and renders the disabled view.

import {
  Badge,
  Button,
  Card,
  Field,
  TextInput,
} from "@enterprise-search/design-system";
import { type FormEvent, type ReactElement, useEffect, useState } from "react";
import type { RequestIdentity } from "../../api/config";
import { useWorkspace } from "./useWorkspace";

export function WorkspaceSettings({
  identity,
  isAdmin,
}: {
  identity: RequestIdentity;
  isAdmin: boolean;
}): ReactElement {
  const { workspace, loading, error, save } = useWorkspace(identity);
  const [displayName, setDisplayName] = useState("");
  const [slug, setSlug] = useState("");
  const [logoUrl, setLogoUrl] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [savedAt, setSavedAt] = useState<number | null>(null);

  useEffect(() => {
    if (workspace) {
      setDisplayName(workspace.display_name);
      setSlug(workspace.slug);
      const logo = workspace.metadata?.logo_url;
      setLogoUrl(typeof logo === "string" ? logo : "");
    }
  }, [workspace]);

  if (loading) {
    return (
      <div className="settings-section">
        <h2>Workspace</h2>
        <Card>
          <p>Loading workspace…</p>
        </Card>
      </div>
    );
  }

  if (error || !workspace) {
    return (
      <div className="settings-section">
        <h2>Workspace</h2>
        <Card>
          <p>{error ?? "Workspace unavailable."}</p>
        </Card>
      </div>
    );
  }

  const dirty =
    displayName !== workspace.display_name ||
    slug !== workspace.slug ||
    (logoUrl || "") !== (workspace.metadata?.logo_url ?? "");

  async function onSubmit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (!isAdmin || !dirty) return;
    setSaving(true);
    setSaveError(null);
    try {
      const patch: Parameters<typeof save>[0] = {};
      if (displayName !== workspace!.display_name)
        patch.display_name = displayName.trim();
      if (slug !== workspace!.slug) patch.slug = slug.trim();
      const previousLogo = workspace!.metadata?.logo_url ?? "";
      if (logoUrl !== previousLogo) {
        patch.metadata = { logo_url: logoUrl.trim() || null };
      }
      await save(patch);
      setSavedAt(Date.now());
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : "Save failed");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="settings-section" data-section="workspace">
      <header className="settings-section__header">
        <div>
          <h2>Workspace</h2>
          <p className="settings-section__hint">
            Name, slug, and logo are visible to every member. Defaults below
            apply to new chats.
          </p>
        </div>
        {!isAdmin ? <Badge>Read-only</Badge> : null}
      </header>

      <Card>
        <form onSubmit={onSubmit} className="workspace-settings-form">
          <Field
            label="Workspace name"
            hint="Shown in the topbar crumb and emails."
          >
            <TextInput
              value={displayName}
              maxLength={120}
              disabled={!isAdmin}
              onChange={(event) => setDisplayName(event.target.value)}
            />
          </Field>
          <Field
            label="Slug"
            hint="Lowercase letters, digits, and dashes. 3–40 characters."
          >
            <TextInput
              value={slug}
              maxLength={40}
              disabled={!isAdmin}
              onChange={(event) =>
                setSlug(event.target.value.toLowerCase().replace(/\s+/g, "-"))
              }
            />
          </Field>
          <Field
            label="Logo URL"
            hint="Public HTTPS URL. File upload coming later."
          >
            <TextInput
              type="url"
              value={logoUrl}
              placeholder="https://cdn.example.com/logo.png"
              disabled={!isAdmin}
              onChange={(event) => setLogoUrl(event.target.value)}
            />
          </Field>
          {isAdmin ? (
            <div className="workspace-settings-form__actions">
              <Button
                type="submit"
                variant="primary"
                disabled={!dirty || saving}
              >
                {saving ? "Saving…" : "Save changes"}
              </Button>
              {savedAt && !dirty ? (
                <Badge tone="success" data-testid="workspace-saved">
                  Saved
                </Badge>
              ) : null}
              {saveError ? (
                <Badge tone="danger" data-testid="workspace-save-error">
                  {saveError}
                </Badge>
              ) : null}
            </div>
          ) : null}
        </form>
      </Card>

      {isAdmin ? (
        <Card data-section="danger-zone" className="workspace-settings-danger">
          <h3>Danger zone</h3>
          <p>
            Workspace deletion is gated. Cascading delete of conversations,
            runs, and audit history requires explicit operator approval.
          </p>
          <Button
            type="button"
            variant="danger"
            disabled
            title="Workspace deletion is gated. Contact support."
          >
            Delete workspace
          </Button>
        </Card>
      ) : null}
    </div>
  );
}
