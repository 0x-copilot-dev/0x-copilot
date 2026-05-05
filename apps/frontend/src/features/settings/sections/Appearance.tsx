import type {
  AppearancePreferences,
  UpdateUserPreferencesRequest,
  UserProfileAccent,
  UserProfileDensity,
  UserProfileReduceMotion,
  UserProfileTheme,
} from "@enterprise-search/api-types";
import {
  ACCENT_SCHEMES,
  Card,
  Field,
  classNames,
} from "@enterprise-search/design-system";
import type { ReactElement } from "react";
import { useCallback, useEffect, useRef } from "react";
import type { UserPreferencesState } from "../../me/useUserPreferences";

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
}: {
  preferences: UserPreferencesState;
}): ReactElement {
  const data = preferences.data;
  const debounceRef = useRef<number | null>(null);

  // Cancel any pending debounced save on unmount so a stray timeout
  // doesn't fire after the section is closed.
  useEffect(() => {
    return () => {
      if (debounceRef.current !== null) {
        window.clearTimeout(debounceRef.current);
      }
    };
  }, []);

  const scheduleSave = useCallback(
    (patch: UpdateUserPreferencesRequest) => {
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
    [preferences],
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
    </div>
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
