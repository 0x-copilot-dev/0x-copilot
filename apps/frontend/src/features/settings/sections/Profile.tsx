import type { UpdateUserProfileRequest } from "@enterprise-search/api-types";
import {
  Badge,
  Button,
  Card,
  Field,
  TextInput,
} from "@enterprise-search/design-system";
import type { ReactElement } from "react";
import { useEffect, useState } from "react";
import type { UserProfileState } from "../../me/useUserProfile";

/**
 * Settings → You → Profile.
 *
 * Form is uncontrolled-by-default and saved on the explicit Save button
 * (no debounced auto-save). The avatar URL is text-only in v1; the
 * file-upload pipeline is a follow-up PR.
 *
 * Validation lives server-side (timezone IANA-set membership, locale
 * BCP-47 shape, working-hours start<end) — the form surfaces the
 * server's 422 error message verbatim if the input is rejected.
 */
export function Profile({
  profile,
}: {
  profile: UserProfileState;
}): ReactElement {
  const data = profile.data;
  const [displayName, setDisplayName] = useState("");
  const [title, setTitle] = useState("");
  const [timezone, setTimezone] = useState("");
  const [locale, setLocale] = useState("");
  const [avatarUrl, setAvatarUrl] = useState("");
  const [saving, setSaving] = useState(false);
  const [savedAt, setSavedAt] = useState<string | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  useEffect(() => {
    if (!data) {
      return;
    }
    setDisplayName(data.display_name ?? "");
    setTitle(data.title ?? "");
    setTimezone(data.timezone ?? "");
    setLocale(data.locale ?? "");
    setAvatarUrl(data.avatar_url ?? "");
  }, [data]);

  async function onSubmit(
    event: React.FormEvent<HTMLFormElement>,
  ): Promise<void> {
    event.preventDefault();
    if (!data) {
      return;
    }
    const patch: UpdateUserProfileRequest = {};
    if (displayName.trim() && displayName !== (data.display_name ?? "")) {
      patch.display_name = displayName.trim();
    }
    patch.title = title.trim() === "" ? null : title.trim();
    patch.timezone = timezone.trim() === "" ? null : timezone.trim();
    patch.locale = locale.trim() === "" ? null : locale.trim();
    patch.avatar_url = avatarUrl.trim() === "" ? null : avatarUrl.trim();

    try {
      setErrorMessage(null);
      setSaving(true);
      await profile.save(patch);
      setSavedAt(new Date().toISOString());
    } catch (err) {
      setErrorMessage(
        err instanceof Error ? err.message : "Could not save profile.",
      );
    } finally {
      setSaving(false);
    }
  }

  if (profile.loading && data === null) {
    return (
      <div className="settings-section">
        <h2>Profile</h2>
        <Card>
          <p>Loading profile…</p>
        </Card>
      </div>
    );
  }

  if (data === null) {
    return (
      <div className="settings-section">
        <h2>Profile</h2>
        <Card>
          <p>{profile.error ?? "Profile is unavailable right now."}</p>
        </Card>
      </div>
    );
  }

  return (
    <div className="settings-section">
      <div className="settings-section__header">
        <div>
          <h2>Profile</h2>
          <p>
            Cross-device fields. They follow you to every browser you sign in
            to.
          </p>
        </div>
      </div>

      <Card>
        <form className="me-form" onSubmit={(e) => void onSubmit(e)}>
          <Field label="Email">
            <div className="me-form__email-row">
              <code>{data.email}</code>
              {data.email_verified_at ? (
                <Badge tone="success">verified</Badge>
              ) : (
                <Badge tone="warning">unverified</Badge>
              )}
            </div>
          </Field>

          <Field label="Display name">
            <TextInput
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              placeholder="Sarah Chen"
            />
          </Field>

          <Field
            label="Title"
            hint="Optional. Shown in the workspace member directory."
          >
            <TextInput
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="Marketing Ops"
            />
          </Field>

          <Field
            label="Timezone"
            hint="IANA tz id — e.g. America/Los_Angeles. Affects working hours + scheduled digests."
          >
            <TextInput
              value={timezone}
              onChange={(e) => setTimezone(e.target.value)}
              placeholder="America/Los_Angeles"
            />
          </Field>

          <Field label="Locale" hint="BCP-47 tag — e.g. en-US, fr-FR.">
            <TextInput
              value={locale}
              onChange={(e) => setLocale(e.target.value)}
              placeholder="en-US"
            />
          </Field>

          <Field
            label="Avatar URL"
            hint="Drag-drop upload coming soon. Paste a public image URL for now."
          >
            <TextInput
              value={avatarUrl}
              onChange={(e) => setAvatarUrl(e.target.value)}
              placeholder="https://cdn.example.com/avatar.png"
            />
          </Field>

          {errorMessage ? <p className="app-error">{errorMessage}</p> : null}

          <div className="me-form__actions">
            <Button type="submit" disabled={saving} title="Save profile">
              {saving ? "Saving…" : "Save changes"}
            </Button>
            {savedAt && !saving ? (
              <span className="settings-meta" data-testid="profile-saved-meta">
                Saved at {new Date(savedAt).toLocaleTimeString()}
              </span>
            ) : null}
          </div>
        </form>
      </Card>
    </div>
  );
}
