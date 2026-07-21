// SettingsSurface — the settings shell (DESIGN-SPEC §4, PRD PR-5.1).
//
//   216px nav (grouped items + collapsible Advanced + solo footer)
//   + content router (one section at a time, tablist/tab/tabpanel a11y)
//   + profile gate (team-admin sections hidden on solo)
//   + savebar / toast host (a dirty section docks a SaveBar; a one-shot
//     action fires a Toast — the two are never conflated, FR-5.7).
//
// The section BODIES (Profile, Appearance, Provider keys, …) are NOT built
// here — they land in PR-5.3…PR-5.9 and are injected through the `renderSection`
// slot. Until a section is provided the surface shows a titled placeholder, so
// the shell is complete and testable on its own.
//
// Substrate-agnostic (chat-surface boundary): no bare `window`/`document`/
// `fetch`/`localStorage`. Focus moves are done through this component's own
// container ref (a member access, not a banned global), mirroring Modal.tsx.
// Colors resolve ONLY to design-system v2 tokens; the dims DESIGN-SPEC §0 pins
// (nav 216px, content max 620px) are numeric literals.

import {
  createContext,
  useCallback,
  useContext,
  useId,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactElement,
  type ReactNode,
} from "react";

import { useDeploymentProfile } from "../providers/DeploymentProfileProvider";
import { SaveBar, Toast, type ToastTone } from "./SaveBar";
import { SecHead, SetCard, SetNote, SettingsNavItem } from "./SettingsChrome";
import {
  SOLO_FOOTER_COPY,
  resolveSettingsSlug,
  settingsNavForProfile,
  settingsNavItem,
  showSoloFooter,
  type SettingsNavGroupView,
  type SettingsNavIcon,
  type SettingsSectionSlug,
} from "./settingsNav";

// DESIGN-SPEC §0 dims.
export const SETTINGS_NAV_WIDTH = 216;
export const SETTINGS_CONTENT_MAX_WIDTH = 620;

// ---------------------------------------------------------------------------
// Surface controller — the seam a section uses to drive the docked SaveBar /
// Toast without re-implementing them. Sections receive it via `renderSection`
// and (for nested pieces) via the `useSettingsSurface` context hook.
// ---------------------------------------------------------------------------

export interface SettingsDirtyState {
  readonly onSave: () => void;
  readonly onDiscard: () => void;
  /** Disables Save + shows the saving label while a write is in flight. */
  readonly saving?: boolean;
  readonly message?: ReactNode;
  readonly saveLabel?: string;
  readonly discardLabel?: string;
  readonly savingLabel?: string;
}

export interface SettingsSurfaceToast {
  readonly message: ReactNode;
  readonly tone?: ToastTone;
}

export interface SettingsSurfaceController {
  /** Register (or, with `null`, clear) the active section's unsaved-edits bar. */
  setDirty: (state: SettingsDirtyState | null) => void;
  /** Fire a one-shot confirmation toast (export queued, key rotated, …). */
  showToast: (toast: SettingsSurfaceToast) => void;
  /** Programmatically switch sections. */
  navigate: (slug: SettingsSectionSlug) => void;
}

const SettingsSurfaceContext = createContext<SettingsSurfaceController | null>(
  null,
);
SettingsSurfaceContext.displayName = "SettingsSurfaceContext";

/** Access the surface controller from inside a section body. */
export function useSettingsSurface(): SettingsSurfaceController {
  const value = useContext(SettingsSurfaceContext);
  if (value === null) {
    throw new Error(
      "useSettingsSurface: must be rendered inside <SettingsSurface>",
    );
  }
  return value;
}

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface SettingsSurfaceProps {
  /** Controlled active section (host owns routing / deep-linking). */
  readonly activeSlug?: string | null;
  /** Initial section for uncontrolled use. Defaults to the profile default. */
  readonly initialSlug?: string | null;
  /** Fires when the user (or a section) selects a section — reflect to URL. */
  readonly onNavigate?: (slug: SettingsSectionSlug) => void;
  /**
   * Section-body slot filled by PR-5.3…PR-5.9. Receives the active slug and the
   * surface controller; returning `undefined` falls back to the placeholder.
   * Only the active section is rendered (it is the single live tabpanel).
   */
  readonly renderSection?: (
    slug: SettingsSectionSlug,
    controller: SettingsSurfaceController,
  ) => ReactNode | undefined;
  /** Host-supplied icon renderer (the DESIGN-SPEC §7 stroke-icon set). */
  readonly renderNavIcon?: (icon: SettingsNavIcon) => ReactNode;
  /** Advanced group starts expanded when true (default: collapsed). */
  readonly defaultAdvancedExpanded?: boolean;
}

// Dirty/toast state is tagged with the slug that owns it, so switching sections
// never leaks one section's savebar/toast onto another (and there is no
// effect-ordering hazard around a "reset on slug change" effect).
interface SlugTagged<T> {
  readonly slug: SettingsSectionSlug;
  readonly value: T;
}

// ---------------------------------------------------------------------------
// Styles (token-only)
// ---------------------------------------------------------------------------

const rootStyle: CSSProperties = {
  display: "flex",
  height: "100%",
  minHeight: 0,
  backgroundColor: "var(--color-bg)",
  color: "var(--color-text)",
  fontSize: "var(--font-size-sm)",
  lineHeight: "var(--line-height-base)",
};

const navStyle: CSSProperties = {
  flex: `0 0 ${SETTINGS_NAV_WIDTH}px`,
  width: SETTINGS_NAV_WIDTH,
  display: "flex",
  flexDirection: "column",
  gap: "var(--space-md)",
  padding: "var(--space-lg) var(--space-md)",
  borderRight: "1px solid var(--color-border)",
  // Design .set-nav sits on --ink2 (= --color-bg-elevated), a step below the
  // content surface (PRD-E) — was --color-surface (too light).
  backgroundColor: "var(--color-bg-elevated)",
  overflowY: "auto",
};

// Nav header — design .set-nav__title + .set-nav__hint (PRD-E).
const navTitleStyle: CSSProperties = {
  fontSize: "var(--font-size-sm)",
  fontWeight: "var(--font-weight-semibold)",
  color: "var(--color-text)",
  padding: "0 8px 2px",
};
const navHintStyle: CSSProperties = {
  fontSize: "var(--font-size-2xs)",
  color: "var(--color-text-subtle)",
  padding: "0 8px",
};

const navGroupStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 2,
};

const groupHeadRowStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  gap: "var(--space-sm)",
  width: "100%",
  padding: "4px 10px",
  border: "none",
  background: "transparent",
  cursor: "pointer",
  font: "inherit",
  textAlign: "left",
};

const footerStyle: CSSProperties = {
  marginTop: "auto",
  paddingTop: "var(--space-md)",
  borderTop: "1px solid var(--color-border)",
  fontSize: "var(--font-size-2xs)",
  lineHeight: "var(--line-height-base)",
  color: "var(--color-text-subtle)",
};

const contentOuterStyle: CSSProperties = {
  flex: 1,
  minWidth: 0,
  display: "flex",
  flexDirection: "column",
  minHeight: 0,
};

const contentScrollStyle: CSSProperties = {
  flex: 1,
  minHeight: 0,
  overflowY: "auto",
  padding: "var(--space-lg)",
};

const contentInnerStyle: CSSProperties = {
  width: "100%",
  maxWidth: SETTINGS_CONTENT_MAX_WIDTH,
  margin: "0 auto",
  display: "flex",
  flexDirection: "column",
  gap: "var(--space-lg)",
  outline: "none",
};

const dockStyle: CSSProperties = {
  flex: "0 0 auto",
  borderTop: "1px solid var(--color-border)",
  backgroundColor: "var(--color-bg)",
  padding: "var(--space-md) var(--space-lg)",
};

const dockInnerStyle: CSSProperties = {
  width: "100%",
  maxWidth: SETTINGS_CONTENT_MAX_WIDTH,
  margin: "0 auto",
  display: "flex",
  flexDirection: "column",
  gap: "var(--space-sm)",
};

// Keys a section body can trigger a focus-move on within the nav.
const ARROW_KEYS = new Set(["ArrowDown", "ArrowUp", "Home", "End"]);

// ---------------------------------------------------------------------------
// SettingsSurface
// ---------------------------------------------------------------------------

export function SettingsSurface({
  activeSlug,
  initialSlug,
  onNavigate,
  renderSection,
  renderNavIcon,
  defaultAdvancedExpanded = false,
}: SettingsSurfaceProps): ReactElement {
  const profile = useDeploymentProfile();
  const groups = settingsNavForProfile(profile);

  const controlled = activeSlug !== undefined;
  const [internalSlug, setInternalSlug] = useState<SettingsSectionSlug>(() =>
    resolveSettingsSlug(initialSlug, profile),
  );
  // Always resolve against the current profile so a now-gated slug self-heals
  // to the default (FR-5.5 / integration deep-link case).
  const active = resolveSettingsSlug(
    controlled ? activeSlug : internalSlug,
    profile,
  );

  const [dirty, setDirty] = useState<SlugTagged<SettingsDirtyState> | null>(
    null,
  );
  const [toast, setToast] = useState<SlugTagged<SettingsSurfaceToast> | null>(
    null,
  );
  const [openGroups, setOpenGroups] = useState<
    Partial<Record<string, boolean>>
  >({});

  const reactId = useId();
  const panelId = `${reactId}-panel`;
  const tabId = (slug: SettingsSectionSlug): string => `${reactId}-tab-${slug}`;
  const navRef = useRef<HTMLDivElement>(null);

  // Latest active slug for the controller's slug-tagging (avoids stale closure).
  const activeRef = useRef(active);
  activeRef.current = active;

  const navigate = useCallback(
    (slug: SettingsSectionSlug) => {
      // Dropping the old section's savebar/toast on navigation is implicit:
      // both are slug-tagged, so they simply stop matching the new active slug.
      if (!controlled) setInternalSlug(slug);
      onNavigate?.(slug);
    },
    [controlled, onNavigate],
  );
  const navigateRef = useRef(navigate);
  navigateRef.current = navigate;

  const controller = useMemo<SettingsSurfaceController>(
    () => ({
      setDirty: (state) =>
        setDirty(
          state === null ? null : { slug: activeRef.current, value: state },
        ),
      showToast: (next) => setToast({ slug: activeRef.current, value: next }),
      navigate: (slug) => navigateRef.current(slug),
    }),
    [],
  );

  const activeDirty =
    dirty !== null && dirty.slug === active ? dirty.value : null;
  const activeToast =
    toast !== null && toast.slug === active ? toast.value : null;

  const isGroupExpanded = (group: SettingsNavGroupView): boolean => {
    if (!group.collapsible) return true;
    // The active section is always reachable — force its group open.
    if (group.items.some((item) => item.id === active)) return true;
    return openGroups[group.id] ?? defaultAdvancedExpanded;
  };

  const handleNavKeyDown = useCallback(
    (event: ReactKeyboardEvent<HTMLDivElement>) => {
      if (!ARROW_KEYS.has(event.key)) return;
      const nav = navRef.current;
      if (nav === null) return;
      const tabs = Array.from(
        nav.querySelectorAll<HTMLButtonElement>('[role="tab"]'),
      );
      if (tabs.length === 0) return;
      event.preventDefault();
      const owner = nav.ownerDocument;
      const currentIndex = tabs.findIndex((tab) => tab === owner.activeElement);
      const from = currentIndex < 0 ? 0 : currentIndex;
      let nextIndex = from;
      switch (event.key) {
        case "ArrowDown":
          nextIndex = (from + 1) % tabs.length;
          break;
        case "ArrowUp":
          nextIndex = (from - 1 + tabs.length) % tabs.length;
          break;
        case "Home":
          nextIndex = 0;
          break;
        case "End":
          nextIndex = tabs.length - 1;
          break;
      }
      // Manual activation: arrows move focus only; Enter/Space (native button)
      // selects. This avoids blowing away a dirty section on arrow-through.
      tabs[nextIndex]?.focus();
    },
    [],
  );

  const toggleGroup = (groupId: string): void => {
    setOpenGroups((prev) => ({
      ...prev,
      [groupId]: !(prev[groupId] ?? defaultAdvancedExpanded),
    }));
  };

  const sectionBody = renderSection?.(active, controller) ?? (
    <SectionPlaceholder slug={active} />
  );

  return (
    <SettingsSurfaceContext.Provider value={controller}>
      <section
        role="region"
        aria-label="Settings"
        data-testid="settings-surface"
        data-surface="settings"
        style={rootStyle}
      >
        <div
          ref={navRef}
          role="tablist"
          aria-label="Settings sections"
          aria-orientation="vertical"
          onKeyDown={handleNavKeyDown}
          style={navStyle}
        >
          <div aria-hidden="true">
            <div style={navTitleStyle}>Settings</div>
            <div style={navHintStyle}>
              {profile === "team" ? "Team workspace" : "Solo desktop"}
            </div>
          </div>
          {groups.map((group) => {
            const expanded = isGroupExpanded(group);
            const groupItemsId = `${reactId}-group-${group.id}`;
            return (
              <div key={group.id} style={navGroupStyle}>
                {group.collapsible ? (
                  <button
                    type="button"
                    aria-expanded={expanded}
                    aria-controls={groupItemsId}
                    onClick={() => toggleGroup(group.id)}
                    style={groupHeadRowStyle}
                    data-testid={`settings-group-toggle-${group.id}`}
                  >
                    <SecHead>{group.label}</SecHead>
                    <span
                      aria-hidden="true"
                      style={{
                        color: "var(--color-text-subtle)",
                        transition:
                          "transform var(--duration-fast) var(--ease-standard)",
                        transform: expanded ? "rotate(90deg)" : "none",
                      }}
                    >
                      ›
                    </span>
                  </button>
                ) : (
                  <div style={{ padding: "4px 10px" }}>
                    <SecHead>{group.label}</SecHead>
                  </div>
                )}
                {expanded ? (
                  <div id={groupItemsId} style={navGroupStyle}>
                    {group.items.map((item) => {
                      const isActive = item.id === active;
                      return (
                        <SettingsNavItem
                          key={item.id}
                          label={item.label}
                          tag={item.tag}
                          icon={
                            renderNavIcon !== undefined
                              ? renderNavIcon(item.icon)
                              : undefined
                          }
                          active={isActive}
                          role="tab"
                          id={tabId(item.id)}
                          aria-selected={isActive}
                          aria-controls={panelId}
                          tabIndex={isActive ? 0 : -1}
                          data-slug={item.id}
                          onClick={() => navigate(item.id)}
                        />
                      );
                    })}
                  </div>
                ) : null}
              </div>
            );
          })}

          {showSoloFooter(profile) ? (
            <p style={footerStyle} data-testid="settings-solo-footer">
              {SOLO_FOOTER_COPY}
            </p>
          ) : null}
        </div>

        <div style={contentOuterStyle}>
          <div style={contentScrollStyle}>
            <div
              role="tabpanel"
              id={panelId}
              aria-labelledby={tabId(active)}
              tabIndex={0}
              data-testid="settings-content"
              data-active-slug={active}
              style={contentInnerStyle}
            >
              {sectionBody}
            </div>
          </div>
          {activeDirty !== null || activeToast !== null ? (
            <div style={dockStyle}>
              <div style={dockInnerStyle}>
                {activeToast !== null ? (
                  <Toast
                    open
                    message={activeToast.message}
                    tone={activeToast.tone}
                    onDismiss={() => setToast(null)}
                  />
                ) : null}
                {activeDirty !== null ? (
                  <SaveBar
                    dirty
                    onSave={activeDirty.onSave}
                    onDiscard={activeDirty.onDiscard}
                    saving={activeDirty.saving}
                    message={activeDirty.message}
                    saveLabel={activeDirty.saveLabel}
                    discardLabel={activeDirty.discardLabel}
                    savingLabel={activeDirty.savingLabel}
                  />
                ) : null}
              </div>
            </div>
          ) : null}
        </div>
      </section>
    </SettingsSurfaceContext.Provider>
  );
}

// ---------------------------------------------------------------------------
// SectionPlaceholder — shown until a section PR (5.3…5.9) supplies its body via
// `renderSection`. Titled from the nav SSOT so the shell reads correctly today.
// ---------------------------------------------------------------------------

function SectionPlaceholder({
  slug,
}: {
  slug: SettingsSectionSlug;
}): ReactElement {
  const item = settingsNavItem(slug);
  const label = item?.label ?? slug;
  return (
    <SetCard
      title={label}
      // User-voice copy — internal PR numbering stays in the code comment
      // above (design review: dev jargon was leaking into user-facing text).
      meta="Coming soon"
      data-testid={`settings-placeholder-${slug}`}
    >
      <SetNote>
        {label} settings aren&apos;t available yet. They&apos;re on the way.
      </SetNote>
    </SetCard>
  );
}
