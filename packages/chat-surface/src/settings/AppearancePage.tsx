// <AppearancePage /> — Settings → Account → Appearance (DESIGN-SPEC §4, PR-5.3).
//
//   Theme tiles     Dark · Light · System ("Match macOS") — exactly 3.
//                   `slate` is a legacy value: round-tripped if already set,
//                   never surfaced as a fourth tile (§5.5).
//   Accent swatches sky · jade · ember · violet — the reconciled 4-accent
//                   single-accent set (§0). This is NOT the shipped 9-entry
//                   `ACCENT_SCHEMES` (sky/atlas-orange/gold/amber/red/lime/
//                   teal/blue/violet) — `jade`/`ember` are not in it yet; Phase
//                   0B/0C narrows `ACCENT_SCHEMES` + `UserProfileAccent` to this
//                   set, at which point APPEARANCE_ACCENTS collapses onto it.
//   Density         Comfortable · Compact · Spacious — `spacious` is not in the
//                   shipped `UserProfileDensity` union (FR-5.9a); see the
//                   persistence split below.
//   Reduce motion   a toggle (on → "always" / off → "auto", per the shipped
//                   `UserProfileReduceMotion` + design-system CSS contract).
//
// SUBSTRATE-AGNOSTIC. This is a *controlled, presentation-only* section: it
// reflects `value` and reports edits through `onChange`. It never touches
// `document`/`window`/`localStorage` and never persists — applying the choice
// live and persisting it are HOST concerns (FR-5.26, and the PR-5.3 constraint
// "appearance persistence is a host concern via a prop/port, not localStorage
// here"). The host drives the design-system attributes with
// {@link appearanceAttributes} (they are `:root`-scoped, so only the document
// root — which the host owns — can apply them) and persists with
// {@link splitAppearancePersistence} so no option silently fails to save.
//
// Colors resolve to design-system v2 tokens EXCEPT the accent-dot / theme-tile
// preview colors, which are representational runtime data (mirroring
// controls.tsx AccentSwatch's `swatch` prop) — a swatch must render its actual
// accent, so it is literal data, not a token.

import type { AppearancePreferences } from "@0x-copilot/api-types";
import { ACCENT_SCHEMES, Toggle } from "@0x-copilot/design-system";
import {
  useId,
  type CSSProperties,
  type ReactElement,
  type ReactNode,
} from "react";

import { AccentSwatch, SegmentedControl, ThemeTile } from "./controls";
import { Frow, SecHead, SetCard, SetNote } from "./SettingsChrome";

// ---------------------------------------------------------------------------
// Vocabulary. The component works in the DESIGN-SPEC §4 vocabulary; the host
// maps it to/from the shipped api-types unions when persisting (see the split).
// ---------------------------------------------------------------------------

/** The three surfaced themes. `slate` is legacy and only round-tripped. */
export type AppearanceTheme = "dark" | "light" | "system";
/** The reconciled single-accent set (§0/§5.5). */
export type AppearanceAccentId = "sky" | "jade" | "ember" | "violet";
export type AppearanceDensity = "comfortable" | "compact" | "spacious";

/**
 * Current appearance selection. Fields are typed as `string` (not the narrow
 * ids) so a legacy value the contract still carries — `slate` theme, a v1
 * accent like `atlas-orange` — round-trips through the surface instead of being
 * dropped. The surfaced options select whichever matches; an unmatched value
 * simply leaves its group with no selection (round-trip, no data loss).
 */
export interface AppearanceValue {
  readonly theme: string;
  readonly accent: string;
  readonly density: string;
  readonly reduceMotion: boolean;
}

/** A single edit. Only ever carries a surfaced (valid) id. */
export interface AppearancePatch {
  readonly theme?: AppearanceTheme;
  readonly accent?: AppearanceAccentId;
  readonly density?: AppearanceDensity;
  readonly reduceMotion?: boolean;
}

export interface AppearancePageProps {
  readonly value: AppearanceValue;
  /**
   * Report an edit. The host applies it live to the document root via
   * {@link appearanceAttributes} + the design-system theme provider, and
   * persists it via {@link splitAppearancePersistence}. Optimistic: appearance
   * has no dirty/SaveBar — it applies instantly (matching the web
   * AppearanceContext).
   */
  readonly onChange: (patch: AppearancePatch) => void;
  /** Preferences still loading — render a quiet skeleton, never a blank. */
  readonly loading?: boolean;
  /** Load/save error — surfaced as a role="alert" with a Retry affordance. */
  readonly error?: string | null;
  readonly onRetry?: () => void;
}

// ---------------------------------------------------------------------------
// Option sets (DESIGN-SPEC §4 order).
// ---------------------------------------------------------------------------

export const APPEARANCE_THEMES: ReadonlyArray<{
  readonly id: AppearanceTheme;
  readonly label: string;
  readonly caption?: string;
  /** Representational preview color(s) — see file header. */
  readonly preview: string;
}> = [
  { id: "dark", label: "Dark", preview: "#16161a" },
  { id: "light", label: "Light", preview: "#f4f4f6" },
  {
    id: "system",
    label: "System",
    caption: "Match macOS",
    preview: "linear-gradient(135deg, #16161a 0 50%, #f4f4f6 50% 100%)",
  },
];

// The reconciled 4-accent set with DESIGN-SPEC §0 hex. `sky`/`violet` also exist
// in the shipped `ACCENT_SCHEMES`; `jade`/`ember` do not yet (gap #10) — so this
// cannot be a literal `.filter()` of `ACCENT_SCHEMES` (that would drop
// jade/ember). It IS strictly narrower than `ACCENT_SCHEMES` (4 < 9), which the
// test pins to prove the single-accent discipline.
export const APPEARANCE_ACCENTS: ReadonlyArray<{
  readonly id: AppearanceAccentId;
  readonly label: string;
  readonly swatch: string;
}> = [
  { id: "sky", label: "Sky", swatch: "#5fb2ec" },
  { id: "jade", label: "Jade", swatch: "#57c785" },
  { id: "ember", label: "Ember", swatch: "#f0764f" },
  { id: "violet", label: "Violet", swatch: "#a98be0" },
];

export const APPEARANCE_DENSITIES: ReadonlyArray<{
  readonly value: AppearanceDensity;
  readonly label: string;
}> = [
  { value: "comfortable", label: "Comfortable" },
  { value: "compact", label: "Compact" },
  { value: "spacious", label: "Spacious" },
];

// ---------------------------------------------------------------------------
// appearanceAttributes — the design-system attribute contract (DESIGN-SPEC §0).
// The host spreads these onto the document root (`:root`), which is the ONLY
// element the design-system CSS keys off (`:root[data-theme]` / `[data-accent]`
// / `[data-density]` / `[data-reduce-motion]`). Pure: no globals, so it lives
// in the framework-agnostic package and is unit-testable in isolation.
// ---------------------------------------------------------------------------

export interface AppearanceAttributes {
  readonly "data-theme": string;
  readonly "data-accent": string;
  readonly "data-density": string;
  readonly "data-reduce-motion": string;
}

export function appearanceAttributes(
  value: AppearanceValue,
  options: { readonly systemPrefersDark?: boolean } = {},
): AppearanceAttributes {
  // The design-system has no `:root[data-theme="system"]` block — "System" is a
  // preference resolved against the OS. Resolve it here so the host can apply a
  // real scheme; default dark (this is a dark-first product, matching the web
  // AppearanceContext's system→dark mapping).
  const systemPrefersDark = options.systemPrefersDark ?? true;
  const theme =
    value.theme === "system"
      ? systemPrefersDark
        ? "dark"
        : "light"
      : value.theme;
  return {
    "data-theme": theme,
    "data-accent": value.accent,
    "data-density": value.density,
    // Design-system CSS zeroes motion on `:root[data-reduce-motion="always"]`
    // and honors `prefers-reduced-motion` under `="auto"`.
    "data-reduce-motion": value.reduceMotion ? "always" : "auto",
  };
}

// ---------------------------------------------------------------------------
// splitAppearancePersistence — route each edited field to the store that can
// persist it (FR-5.9a: "no option in the UI silently fails to persist"). The
// shipped profile/appearance contract carries theme (incl. slate),
// reduce_motion, the 9 v1 accents, and comfortable/compact density. The spec's
// `jade`/`ember` accents and `spacious` density are NOT in it yet, so they go to
// the host's KeyValueStore fallback (which still sets the live `:root` attribute
// via appearanceAttributes) until Phase 0B/0C widens the contract.
//
// Pure classifier only — it decides WHERE a field goes; the host executes the
// Transport write and the KeyValueStore write. The contract-accent set is read
// from `ACCENT_SCHEMES` (the runtime SSOT of `UserProfileAccent`).
// ---------------------------------------------------------------------------

const CONTRACT_ACCENT_IDS: ReadonlySet<string> = new Set(
  ACCENT_SCHEMES.map((scheme) => scheme.id),
);
// Mirrors `UserProfileAccent` (which has no runtime export). `spacious` is
// intentionally absent — it is the FR-5.9a fallback case.
const CONTRACT_DENSITY_IDS: ReadonlySet<string> = new Set([
  "comfortable",
  "compact",
]);

export interface AppearancePersistenceSplit {
  /** Fields the shipped contract accepts — persist via `Transport`. */
  readonly profile: Partial<AppearancePreferences>;
  /** Off-contract fields — persist via `KeyValueStore` (FR-5.9a). */
  readonly local: Partial<Pick<AppearanceValue, "accent" | "density">>;
}

export function splitAppearancePersistence(
  patch: AppearancePatch,
): AppearancePersistenceSplit {
  const profile: {
    -readonly [K in keyof AppearancePreferences]?: AppearancePreferences[K];
  } = {};
  const local: { -readonly [K in "accent" | "density"]?: string } = {};

  if (patch.theme !== undefined) {
    // dark | light | system are all valid `UserProfileTheme`.
    profile.theme = patch.theme;
  }
  if (patch.reduceMotion !== undefined) {
    profile.reduce_motion = patch.reduceMotion ? "always" : "auto";
  }
  if (patch.accent !== undefined) {
    if (CONTRACT_ACCENT_IDS.has(patch.accent)) {
      profile.accent = patch.accent as AppearancePreferences["accent"];
    } else {
      local.accent = patch.accent;
    }
  }
  if (patch.density !== undefined) {
    if (CONTRACT_DENSITY_IDS.has(patch.density)) {
      profile.density = patch.density as AppearancePreferences["density"];
    } else {
      local.density = patch.density;
    }
  }
  return { profile, local };
}

// ---------------------------------------------------------------------------
// Styles (token-only chrome).
// ---------------------------------------------------------------------------

const tileRowStyle: CSSProperties = {
  display: "flex",
  flexWrap: "wrap",
  gap: "var(--space-sm)",
};

const swatchRowStyle: CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: "var(--space-sm)",
};

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export function AppearancePage({
  value,
  onChange,
  loading = false,
  error = null,
  onRetry,
}: AppearancePageProps): ReactElement {
  const reactId = useId();
  const reduceMotionId = `${reactId}-reduce-motion`;
  const attrs = appearanceAttributes(value);

  if (loading) {
    return (
      <SetCard
        title="Appearance"
        meta="Theme, accent, density, and motion."
        data-testid="appearance-page"
      >
        <SetNote data-testid="appearance-loading">Loading preferences…</SetNote>
      </SetCard>
    );
  }

  if (error !== null) {
    return (
      <SetCard
        title="Appearance"
        meta="Theme, accent, density, and motion."
        data-testid="appearance-page"
      >
        <div
          role="alert"
          data-testid="appearance-error"
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            gap: "var(--space-md)",
            padding: "10px 12px",
            borderRadius: "var(--radius-md)",
            border: "1px solid var(--color-danger)",
            backgroundColor: "var(--color-danger-bg)",
            color: "var(--color-text)",
            fontSize: "var(--font-size-xs)",
          }}
        >
          <span>{error}</span>
          {onRetry !== undefined ? (
            <button
              type="button"
              onClick={onRetry}
              data-testid="appearance-retry"
              style={retryButtonStyle}
            >
              Retry
            </button>
          ) : null}
        </div>
      </SetCard>
    );
  }

  return (
    <SetCard
      title="Appearance"
      meta="Theme, accent, density, and motion. Applied instantly."
      data-testid="appearance-page"
      // Mirrors the current selection as the design-system attribute set. Note
      // this is on the SECTION, not `:root` — the host applies the same set to
      // the document root (where the CSS keys off) to theme the whole app.
      data-theme={attrs["data-theme"]}
      data-accent={attrs["data-accent"]}
      data-density={attrs["data-density"]}
      data-reduce-motion={attrs["data-reduce-motion"]}
    >
      {/* Theme */}
      <Field label="Theme" hint="System follows your OS color scheme.">
        <div
          role="radiogroup"
          aria-label="Theme"
          data-testid="appearance-theme"
          style={tileRowStyle}
        >
          {APPEARANCE_THEMES.map((theme) => (
            <ThemeTile
              key={theme.id}
              label={theme.label}
              caption={theme.caption}
              aria-label={theme.label}
              data-value={theme.id}
              preview={
                <span
                  style={{
                    display: "block",
                    width: "100%",
                    height: "100%",
                    background: theme.preview,
                  }}
                />
              }
              selected={value.theme === theme.id}
              onSelect={() => onChange({ theme: theme.id })}
            />
          ))}
        </div>
      </Field>

      {/* Accent */}
      <Field
        label="Accent"
        hint="Used for primary buttons, chips, and active states."
      >
        <div
          role="radiogroup"
          aria-label="Accent color"
          data-testid="appearance-accent"
          style={swatchRowStyle}
        >
          {APPEARANCE_ACCENTS.map((accent) => (
            <AccentSwatch
              key={accent.id}
              swatch={accent.swatch}
              label={accent.label}
              data-value={accent.id}
              selected={value.accent === accent.id}
              onSelect={() => onChange({ accent: accent.id })}
            />
          ))}
        </div>
      </Field>

      {/* Density */}
      <Field label="Density">
        <SegmentedControl<AppearanceDensity>
          ariaLabel="Density"
          options={APPEARANCE_DENSITIES.map((d) => ({
            value: d.value,
            label: d.label,
          }))}
          value={
            (APPEARANCE_DENSITIES.find((d) => d.value === value.density)
              ?.value ?? "comfortable") as AppearanceDensity
          }
          onChange={(density) => onChange({ density })}
        />
      </Field>

      {/* Reduce motion */}
      <Frow
        label="Reduce motion"
        hint="Minimize animations across the app."
        htmlFor={reduceMotionId}
      >
        <Toggle
          id={reduceMotionId}
          checked={value.reduceMotion}
          aria-label="Reduce motion"
          data-testid="appearance-reduce-motion"
          onChange={(event) =>
            onChange({ reduceMotion: event.currentTarget.checked })
          }
        />
      </Frow>
    </SetCard>
  );
}

// ---------------------------------------------------------------------------
// Field — local label+hint wrapper for the wider controls (theme tiles, accent
// row, density). `Frow` is a single-row layout (control on the right); the tile
// and swatch rows are full-width block controls, so they get a stacked label
// instead. Non-interactive label (the radiogroup labels itself via aria-label).
// ---------------------------------------------------------------------------

function Field({
  label,
  hint,
  children,
}: {
  readonly label: ReactNode;
  readonly hint?: ReactNode;
  readonly children: ReactNode;
}): ReactElement {
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: "var(--space-sm)",
        padding: "var(--space-sm) 0",
        borderBottom: "1px solid var(--color-border)",
      }}
    >
      <div>
        <SecHead>{label}</SecHead>
        {hint !== undefined ? (
          <p
            style={{
              margin: "3px 0 0",
              fontSize: "var(--font-size-xs)",
              lineHeight: "var(--line-height-base)",
              color: "var(--color-text-muted)",
            }}
          >
            {hint}
          </p>
        ) : null}
      </div>
      {children}
    </div>
  );
}

const retryButtonStyle: CSSProperties = {
  flex: "0 0 auto",
  padding: "4px 10px",
  borderRadius: "var(--radius-sm)",
  border: "1px solid var(--color-border-strong)",
  background: "transparent",
  color: "var(--color-text)",
  font: "inherit",
  fontSize: "var(--font-size-xs)",
  fontWeight: "var(--font-weight-medium)",
  cursor: "pointer",
};
