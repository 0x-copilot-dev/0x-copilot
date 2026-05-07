import type {
  AppearancePreferences,
  UpdateUserPreferencesRequest,
  UpdateUserProfileRequest,
  UserProfileAccent,
  UserProfileDensity,
  UserProfileReduceMotion,
  UserProfileTheme,
} from "@enterprise-search/api-types";
import {
  ACCENT_SCHEMES,
  type AccentScheme,
  Card,
  Field,
  TextInput,
  type ThemeScheme,
  classNames,
  useTheme,
} from "@enterprise-search/design-system";
import type { ReactElement } from "react";
import { useCallback, useEffect, useRef, useState } from "react";
import type { UserPreferencesState } from "../../me/useUserPreferences";
import type { UserProfileState } from "../../me/useUserProfile";

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

const SAVE_DEBOUNCE_MS = 300;

/**
 * Settings → You → Appearance.
 *
 * Theme + accent + density + reduce-motion. Local clicks recolor the
 * page live (via ``useThemeSync`` + ``data-density`` / ``data-reduce-
 * motion`` attributes), then a debounced PUT persists. The 300 ms debounce
 * coalesces rapid swatch clicks into one network round-trip.
 */
export function Appearance({
  preferences,
  profile,
}: {
  preferences: UserPreferencesState;
  /**
   * PR 8.1 — Locale moved from Profile → Appearance ("Region & language").
   * Optional so legacy callers without a profile hook still render the
   * theme/accent/density block above. The card is omitted when absent.
   */
  profile?: UserProfileState;
}): ReactElement {
  const data = preferences.data;
  const debounceRef = useRef<number | null>(null);
  // Optimistic theme update — the previous flow waited for a 300 ms
  // debounce + server round-trip before any visual change, which made
  // swatch clicks feel broken (and silently failed if the network was
  // down). Pull setScheme/setAccent from the provider so the click
  // re-themes instantly; the debounced save then persists. If the save
  // fails, useThemeSync will reconcile back to the persisted value on
  // the next preferences refresh.
  const { setScheme, setAccent } = useTheme();

  // Cancel any pending debounced save on unmount so a stray timeout
  // doesn't fire after the section is closed.
  useEffect(() => {
    return () => {
      if (debounceRef.current !== null) {
        window.clearTimeout(debounceRef.current);
      }
    };
  }, []);

  const applyAppearanceLocally = useCallback(
    (patch: Partial<AppearancePreferences>): void => {
      if (typeof document === "undefined") {
        return;
      }
      const root = document.documentElement;
      if (patch.theme !== undefined) {
        // Mirror useThemeSync's "system" → "dark" mapping.
        const scheme: ThemeScheme =
          patch.theme === "light" || patch.theme === "slate"
            ? patch.theme
            : "dark";
        setScheme(scheme);
      }
      if (patch.accent !== undefined) {
        setAccent(patch.accent as AccentScheme);
      }
      if (patch.density !== undefined) {
        root.dataset.density = patch.density;
      }
      if (patch.reduce_motion !== undefined) {
        root.dataset.reduceMotion = patch.reduce_motion;
      }
    },
    [setScheme, setAccent],
  );

  const scheduleSave = useCallback(
    (patch: UpdateUserPreferencesRequest) => {
      // Apply the visual change immediately. Server save is debounced.
      if (patch.appearance !== undefined) {
        applyAppearanceLocally(patch.appearance);
      }
      if (debounceRef.current !== null) {
        window.clearTimeout(debounceRef.current);
      }
      debounceRef.current = window.setTimeout(() => {
        debounceRef.current = null;
        void preferences.save(patch).catch(() => {
          // The hook already surfaces the error; nothing to do here.
        });
      }, SAVE_DEBOUNCE_MS);
    },
    [preferences, applyAppearanceLocally],
  );

  if (preferences.loading && data === null) {
    return (
      <div className="settings-section">
        <h2>Appearance</h2>
        <Card>
          <p>Loading preferences…</p>
        </Card>
      </div>
    );
  }

  if (data === null) {
    return (
      <div className="settings-section">
        <h2>Appearance</h2>
        <Card>
          <p>{preferences.error ?? "Preferences are unavailable right now."}</p>
        </Card>
      </div>
    );
  }

  const appearance = data.appearance;

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
                aria-checked={appearance.theme === option.id}
                className={classNames(
                  "me-radio-pill",
                  appearance.theme === option.id && "me-radio-pill--active",
                )}
                onClick={() =>
                  scheduleSave({ appearance: { theme: option.id } })
                }
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
                active={appearance.accent === scheme.id}
                onPick={() =>
                  scheduleSave({
                    appearance: { accent: scheme.id as UserProfileAccent },
                  })
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
                aria-checked={appearance.density === option.id}
                className={classNames(
                  "me-radio-pill",
                  appearance.density === option.id && "me-radio-pill--active",
                )}
                onClick={() =>
                  scheduleSave({ appearance: { density: option.id } })
                }
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
                aria-checked={appearance.reduce_motion === option.id}
                className={classNames(
                  "me-radio-pill",
                  appearance.reduce_motion === option.id &&
                    "me-radio-pill--active",
                )}
                onClick={() =>
                  scheduleSave({ appearance: { reduce_motion: option.id } })
                }
                title={option.hint}
              >
                {option.label}
              </button>
            ))}
          </div>
        </Field>

        {preferences.error ? (
          <p className="app-error">{preferences.error}</p>
        ) : null}
      </Card>

      {profile && profile.data ? <RegionAndLanguage profile={profile} /> : null}
    </div>
  );
}

/**
 * PR 8.1 — Locale ("Region & language") moved from Profile → Appearance
 * because it controls display formatting (date / number / list) rather
 * than identity. Persists via the user-profile endpoint, separate from
 * the theme/accent preferences hook used above.
 */
function RegionAndLanguage({
  profile,
}: {
  profile: UserProfileState;
}): ReactElement {
  const data = profile.data!;
  const [locale, setLocale] = useState(data.locale ?? "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Re-sync from the server snapshot when it changes underneath the
  // form (e.g. another tab saved). Same pattern as the Profile section.
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
      setError(err instanceof Error ? err.message : "Could not save locale.");
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
