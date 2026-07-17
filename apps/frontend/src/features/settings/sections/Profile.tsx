import type { UpdateUserProfileRequest } from "@0x-copilot/api-types";
import {
  Badge,
  Button,
  Card,
  Field,
  TextInput,
} from "@0x-copilot/design-system";
import type { ChangeEvent, DragEvent, FormEvent, ReactElement } from "react";
import { useEffect, useRef, useState } from "react";
import type { UserProfileState } from "../../me/useUserProfile";
import { AccountSessionsPanel } from "../AccountSessionsPanel";
import { useAuth } from "../../auth/AuthContext";
import { AvatarUploadError, fileToAvatarBlob } from "./avatarPipeline";
import { deleteMyAvatar, uploadMyAvatar } from "../../../api/avatarApi";
import { MfaPanel } from "./MfaPanel";
import { errorMessage } from "../../../utils/errors";

/** Mirrors the server-side cap in `me_profile.py::_BIO_MAX_LEN`. */
const BIO_MAX_CHARS = 600;

/**
 * Settings → Account → Profile.
 *
 * PR 8.1 — restructured into two cards: **Identity** (avatar / name /
 * email / job title / time zone) and **Sign-in & security** (sessions
 * + sign-out). Locale moved to Appearance → Region & language.
 *
 * Avatar upload is a follow-up; v1 keeps the URL paste field but with
 * a live circle preview so the surface looks like a real avatar
 * picker. Validation lives server-side and the form surfaces the
 * 422 message verbatim.
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
  const [avatarUrl, setAvatarUrl] = useState("");
  const [bio, setBio] = useState("");
  const [saving, setSaving] = useState(false);
  // Avatar UI state — kept local because it never persists separately
  // from the avatarUrl form field.
  const [avatarBusy, setAvatarBusy] = useState(false);
  const [avatarError, setAvatarError] = useState<string | null>(null);
  const [avatarDragOver, setAvatarDragOver] = useState(false);
  const [avatarUrlOpen, setAvatarUrlOpen] = useState(false);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const [savedAt, setSavedAt] = useState<string | null>(null);
  const [errorText, setErrorText] = useState<string | null>(null);
  const auth = useAuth();

  useEffect(() => {
    if (!data) {
      return;
    }
    setDisplayName(data.display_name ?? "");
    setTitle(data.title ?? "");
    setTimezone(data.timezone ?? "");
    setAvatarUrl(data.avatar_url ?? "");
    setBio(data.bio ?? "");
  }, [data]);

  async function onSubmit(event: FormEvent<HTMLFormElement>): Promise<void> {
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
    patch.avatar_url = avatarUrl.trim() === "" ? null : avatarUrl.trim();
    patch.bio = bio.trim() === "" ? null : bio.trim();

    try {
      setErrorText(null);
      setSaving(true);
      await profile.save(patch);
      setSavedAt(new Date().toISOString());
    } catch (err) {
      setErrorText(errorMessage(err, "Could not save profile."));
    } finally {
      setSaving(false);
    }
  }

  /**
   * Resize client-side → multipart-upload to ``/v1/me/avatar``. The
   * server stores the bytes and returns the cache-busted URL; we
   * preview from the data URL while the upload is in flight so the
   * new photo appears instantly. On success we re-fetch the profile
   * so ``avatar_url`` reflects the server-stored row.
   */
  async function handleFiles(files: FileList | null): Promise<void> {
    if (!files || files.length === 0) {
      return;
    }
    setAvatarError(null);
    setAvatarBusy(true);
    try {
      const { blob, previewDataUrl } = await fileToAvatarBlob(files[0]);
      setAvatarUrl(previewDataUrl);
      const result = await uploadMyAvatar(blob);
      setAvatarUrl(result.avatar_url);
      // The server also wrote ``avatar_url`` on the profile sidecar;
      // refresh so the rest of the form (and any cross-tab listeners)
      // see the canonical value.
      await profile.refresh().catch(() => undefined);
    } catch (err) {
      setAvatarError(
        err instanceof AvatarUploadError
          ? err.message
          : errorMessage(err, "Could not upload that image."),
      );
    } finally {
      setAvatarBusy(false);
      if (fileInputRef.current) {
        // Allow re-picking the same file after an error.
        fileInputRef.current.value = "";
      }
    }
  }

  function onDrop(event: DragEvent<HTMLDivElement>): void {
    event.preventDefault();
    setAvatarDragOver(false);
    void handleFiles(event.dataTransfer.files);
  }

  function onDragOver(event: DragEvent<HTMLDivElement>): void {
    event.preventDefault();
    setAvatarDragOver(true);
  }

  function onDragLeave(event: DragEvent<HTMLDivElement>): void {
    event.preventDefault();
    setAvatarDragOver(false);
  }

  function onPickFile(event: ChangeEvent<HTMLInputElement>): void {
    void handleFiles(event.target.files);
  }

  async function onRemoveAvatar(): Promise<void> {
    setAvatarError(null);
    setAvatarBusy(true);
    try {
      // Server-stored avatars: clear the row + null the URL atomically.
      // Legacy data: URLs predate this endpoint — fall back to clearing
      // the local form field; the next save will null the column.
      if (avatarUrl.startsWith("/v1/me/avatar/")) {
        await deleteMyAvatar();
      }
      setAvatarUrl("");
      await profile.refresh().catch(() => undefined);
    } catch (err) {
      setAvatarError(errorMessage(err, "Could not remove the photo."));
    } finally {
      setAvatarBusy(false);
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

  const initial = (
    data.display_name?.charAt(0) ??
    data.email.charAt(0) ??
    "·"
  ).toUpperCase();

  return (
    <div className="settings-section">
      <div className="settings-section__header">
        <div>
          <h2>Profile</h2>
          <p>How you appear across Atlas. Visible to your workspace.</p>
        </div>
      </div>

      <Card>
        <form className="me-form" onSubmit={(e) => void onSubmit(e)}>
          <h3 className="me-form__card-title">Identity</h3>

          <div
            className={
              avatarDragOver
                ? "me-form__avatar-row me-form__avatar-row--dropzone is-drag"
                : "me-form__avatar-row me-form__avatar-row--dropzone"
            }
            onDrop={onDrop}
            onDragOver={onDragOver}
            onDragLeave={onDragLeave}
          >
            <span
              className="me-form__avatar"
              style={
                avatarUrl
                  ? { backgroundImage: `url("${cssEscape(avatarUrl)}")` }
                  : undefined
              }
              aria-hidden="true"
            >
              {avatarUrl ? "" : initial}
            </span>
            <div className="me-form__avatar-meta">
              <div className="me-form__avatar-actions">
                <Button
                  type="button"
                  variant="secondary"
                  size="sm"
                  onClick={() => fileInputRef.current?.click()}
                  disabled={avatarBusy}
                  title="Choose a photo to upload"
                >
                  {avatarBusy ? "Resizing…" : "Upload photo"}
                </Button>
                {avatarUrl ? (
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    onClick={() => void onRemoveAvatar()}
                    disabled={avatarBusy}
                    title="Remove the current photo"
                  >
                    Remove
                  </Button>
                ) : null}
              </div>
              <p className="settings-meta">
                PNG, JPEG, or WEBP. Drop a file here or click upload.
              </p>
              <button
                type="button"
                className="me-form__inline-link"
                onClick={() => setAvatarUrlOpen((v) => !v)}
                title="Use an external URL instead of uploading"
              >
                {avatarUrlOpen ? "Hide URL field" : "Use a URL instead"}
              </button>
              {avatarUrlOpen ? (
                <Field
                  label="Avatar URL"
                  hint="Public image URL — overrides any uploaded photo."
                >
                  <TextInput
                    value={avatarUrl.startsWith("data:") ? "" : avatarUrl}
                    onChange={(e) => setAvatarUrl(e.target.value)}
                    placeholder="https://cdn.example.com/avatar.png"
                  />
                </Field>
              ) : null}
              {avatarError ? <p className="app-error">{avatarError}</p> : null}
            </div>
            <input
              ref={fileInputRef}
              type="file"
              accept="image/png,image/jpeg,image/webp"
              className="me-form__file-input"
              onChange={onPickFile}
            />
          </div>

          <Field label="Display name">
            <TextInput
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              placeholder="Sarah Chen"
            />
          </Field>

          <Field label="Email">
            <div className="me-form__email-row">
              <code>{data.email}</code>
              {data.email_verified_at ? (
                <Badge tone="success">verified</Badge>
              ) : (
                <Badge tone="warning">unverified</Badge>
              )}
              <button
                type="button"
                className="me-form__inline-link"
                onClick={() => void auth.logout()}
                title="Re-authenticate to refresh your session"
              >
                Re-authenticate
              </button>
            </div>
          </Field>

          <Field
            label="Job title"
            hint="Helps Atlas tailor responses to your role."
          >
            <TextInput
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="Marketing Ops"
            />
          </Field>

          <Field
            label="Time zone"
            hint="IANA tz id — e.g. America/Los_Angeles. Used for scheduling and digests."
          >
            <TextInput
              value={timezone}
              onChange={(e) => setTimezone(e.target.value)}
              placeholder="America/Los_Angeles"
            />
          </Field>

          <Field
            label="Bio"
            hint={`A few lines about how you work. Atlas can reference this. ${BIO_MAX_CHARS - bio.trim().length} characters remaining.`}
          >
            <textarea
              className="me-form__bio"
              value={bio}
              onChange={(e) => setBio(e.target.value)}
              maxLength={BIO_MAX_CHARS}
              rows={3}
              placeholder="Lead ops at Northwind. I run weekly cross-team standups and keep the shipping schedule honest."
            />
          </Field>

          {errorText ? <p className="app-error">{errorText}</p> : null}

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

      <MfaPanel />

      <Card>
        <h3 className="me-form__card-title">Sign-in &amp; security</h3>
        <AccountSessionsPanel />
        <div className="me-form__sign-out-row">
          <div>
            <strong>Sign out everywhere</strong>
            <p className="settings-meta">
              Ends every session on every device — including this one.
            </p>
          </div>
          <Button
            type="button"
            variant="danger"
            size="sm"
            onClick={() => void auth.logout()}
            data-testid="profile-sign-out-everywhere"
            title="Sign out of every device"
          >
            Sign out everywhere
          </Button>
        </div>
      </Card>
    </div>
  );
}

/**
 * Avatar URLs come from user input — escape characters that could break
 * out of the inline `url("…")` string. We're not rendering the value as
 * HTML; this just keeps the CSS valid. Browsers fetch with their normal
 * referer policy.
 */
function cssEscape(input: string): string {
  return input.replace(/["\\]/g, "\\$&");
}
