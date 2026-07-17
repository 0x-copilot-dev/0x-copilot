import type {
  AppearancePreferences,
  UpdateUserProfileRequest,
  UserProfileAccent,
  UserProfileDensity,
  UserProfileReduceMotion,
  UserProfileTheme,
} from "@0x-copilot/api-types";
import {
  ACCENT_SCHEMES,
  Card,
  Field,
  TextInput,
  classNames,
} from "@0x-copilot/design-system";
import type { ReactElement } from "react";
import { useEffect, useState } from "react";

import { useAppearance } from "../../appearance/AppearanceContext";
import type { UserProfileState } from "../../me/useUserProfile";
import { errorMessage } from "../../../utils/errors";

const THEME_OPTIONS: ReadonlyArray<{ id: UserProfileTheme; label: string }> = [
  { id: "system", label: "System" },
  { id: "light", label: "Light" },
  { id: "dark", label: "Dark" },
  { id: "slate", label: "Slate" },
];

const DENSITY_OPTIONS: ReadonlyArray<{
  id: UserProfileDensity;
  label: string;
}> = [
  { id: "comfortable", label: "Comfortable" },
  { id: "compact", label: "Compact" },
];

const REDUCE_MOTION_OPTIONS: ReadonlyArray<{
  id: UserProfileReduceMotion;
  label: string;
  hint: string;
}> = [
  { id: "auto", label: "Auto", hint: "Follow my OS preference." },
  { id: "always", label: "Always reduce", hint: "Force minimal motion." },
  { id: "off", label: "Always animate", hint: "Override OS reduce-motion." },
];

/**
 * Settings → You → Appearance.
 *
 * Theme + accent + density + reduce-motion. Clicks call
 * `useAppearance().set(...)` which:
 *   1. updates the design-system ThemeProvider + `<html>` data attrs
 *      synchronously (instant repaint),
 *   2. queues a 300 ms debounced server save.
 *
 * No local state, no duplicate "what does theme mean visually" logic —
 * the AppearanceProvider (PRD 04) owns all writes.
 */
export function Appearance({
  profile,
}: {
  /**
   * PR 8.1 — Locale ("Region & language") sits in this panel because
   * it controls display formatting. Persisted via the user-profile
   * endpoint, not the appearance preferences endpoint.
   */
  profile?: UserProfileState;
}): ReactElement {
  const appearance = useAppearance();

  if (appearance.loading && appearance.appearance === null) {
    return (
      <div className="settings-section">
        <h2>Appearance</h2>
        <Card>
          <p>Loading preferences…</p>
        </Card>
      </div>
    );
  }

  if (appearance.appearance === null) {
    return (
      <div className="settings-section">
        <h2>Appearance</h2>
        <Card>
          <p>{appearance.error ?? "Preferences are unavailable right now."}</p>
        </Card>
      </div>
    );
  }

  const current = appearance.appearance;

  return (
    <div className="settings-section">
      <div className="settings-section__header">
        <div>
          <h2>Appearance</h2>
          <p>Theme + accent + density. Persisted across devices.</p>
        </div>
      </div>

      <Card>
        <Field label="Theme" hint="System follows your OS color scheme.">
          <div className="me-radio-group" role="radiogroup">
            {THEME_OPTIONS.map((option) => (
              <button
                key={option.id}
                type="button"
                role="radio"
                aria-checked={current.theme === option.id}
                className={classNames(
                  "me-radio-pill",
                  current.theme === option.id && "me-radio-pill--active",
                )}
                onClick={() => appearance.set({ theme: option.id })}
                title={`Use ${option.label} theme`}
              >
                {option.label}
              </button>
            ))}
          </div>
        </Field>

        <Field
          label="Accent"
          hint="Used for primary buttons + chips + active states."
        >
          <div
            className="me-accent-grid"
            role="radiogroup"
            aria-label="Accent color"
          >
            {ACCENT_SCHEMES.map((scheme) => (
              <AccentSwatch
                key={scheme.id}
                id={scheme.id as UserProfileAccent}
                label={scheme.label}
                swatch={scheme.swatch}
                active={current.accent === scheme.id}
                onPick={() =>
                  appearance.set({ accent: scheme.id as UserProfileAccent })
                }
              />
            ))}
          </div>
        </Field>

        <Field label="Density">
          <div className="me-radio-group" role="radiogroup">
            {DENSITY_OPTIONS.map((option) => (
              <button
                key={option.id}
                type="button"
                role="radio"
                aria-checked={current.density === option.id}
                className={classNames(
                  "me-radio-pill",
                  current.density === option.id && "me-radio-pill--active",
                )}
                onClick={() => appearance.set({ density: option.id })}
                title={`Use ${option.label.toLowerCase()} spacing`}
              >
                {option.label}
              </button>
            ))}
          </div>
        </Field>

        <Field
          label="Reduce motion"
          hint="Animations honour this on every page."
        >
          <div className="me-radio-group" role="radiogroup">
            {REDUCE_MOTION_OPTIONS.map((option) => (
              <button
                key={option.id}
                type="button"
                role="radio"
                aria-checked={current.reduce_motion === option.id}
                className={classNames(
                  "me-radio-pill",
                  current.reduce_motion === option.id &&
                    "me-radio-pill--active",
                )}
                onClick={() => appearance.set({ reduce_motion: option.id })}
                title={option.hint}
              >
                {option.label}
              </button>
            ))}
          </div>
        </Field>

        {appearance.error ? (
          <p className="app-error">{appearance.error}</p>
        ) : null}
      </Card>

      {profile && profile.data ? <RegionAndLanguage profile={profile} /> : null}
    </div>
  );
}

function RegionAndLanguage({
  profile,
}: {
  profile: UserProfileState;
}): ReactElement {
  const data = profile.data!;
  const [locale, setLocale] = useState(data.locale ?? "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLocale(profile.data?.locale ?? "");
  }, [profile.data]);

  const dirty = locale.trim() !== (data.locale ?? "");

  async function onSave(): Promise<void> {
    if (!dirty) return;
    const patch: UpdateUserProfileRequest = {
      locale: locale.trim() === "" ? null : locale.trim(),
    };
    try {
      setError(null);
      setSaving(true);
      await profile.save(patch);
    } catch (err) {
      setError(errorMessage(err, "Could not save locale."));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Card>
      <Field
        label="Locale"
        hint="BCP-47 tag — e.g. en-US, fr-FR. Affects date and number formatting."
      >
        <div className="me-form__inline-field">
          <TextInput
            value={locale}
            onChange={(e) => setLocale(e.target.value)}
            placeholder="en-US"
          />
          <button
            type="button"
            className="me-form__inline-save"
            onClick={() => void onSave()}
            disabled={!dirty || saving}
            title="Save locale"
          >
            {saving ? "Saving…" : "Save"}
          </button>
        </div>
      </Field>
      {error ? <p className="app-error">{error}</p> : null}
    </Card>
  );
}

function AccentSwatch({
  id,
  label,
  swatch,
  active,
  onPick,
}: {
  id: AppearancePreferences["accent"];
  label: string;
  swatch: string;
  active: boolean;
  onPick: () => void;
}): ReactElement {
  return (
    <button
      type="button"
      role="radio"
      aria-checked={active}
      aria-label={label}
      title={label}
      data-accent={id}
      className={classNames(
        "me-accent-swatch",
        active && "me-accent-swatch--active",
      )}
      onClick={onPick}
    >
      <span className="me-accent-swatch__chip" style={{ background: swatch }} />
      <span className="me-accent-swatch__label">{label}</span>
    </button>
  );
}
