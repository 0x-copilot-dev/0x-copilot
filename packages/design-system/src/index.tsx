import type {
  ButtonHTMLAttributes,
  CSSProperties,
  HTMLAttributes,
  InputHTMLAttributes,
  LabelHTMLAttributes,
  ReactElement,
  ReactNode,
  RefObject,
  SelectHTMLAttributes,
} from "react";
import {
  createContext,
  useContext,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { createPortal } from "react-dom";

export {
  Popover,
  PopoverAnchor,
  PopoverClose,
  PopoverContent,
  PopoverTrigger,
} from "./popover";
export type { PopoverContentProps, PopoverProps } from "./popover";

export type ThemeScheme = "dark" | "light" | "slate";

export type AccentScheme =
  | "sky"
  | "atlas-orange"
  | "gold"
  | "amber"
  | "red"
  | "lime"
  | "teal"
  | "blue"
  | "violet";

export const ACCENT_SCHEMES: ReadonlyArray<{
  id: AccentScheme;
  label: string;
  swatch: string;
}> = [
  { id: "sky", label: "Sky", swatch: "#5fb2ec" },
  { id: "atlas-orange", label: "Atlas orange", swatch: "#d97757" },
  { id: "gold", label: "Gold", swatch: "#d8b46a" },
  { id: "amber", label: "Amber", swatch: "#f0b450" },
  { id: "red", label: "Red", swatch: "#e26a6a" },
  { id: "lime", label: "Lime", swatch: "#a4c878" },
  { id: "teal", label: "Teal", swatch: "#6cc5b3" },
  { id: "blue", label: "Blue", swatch: "#7bb7ff" },
  { id: "violet", label: "Violet", swatch: "#a78bd6" },
];

export interface ThemeContextValue {
  scheme: ThemeScheme;
  setScheme: (scheme: ThemeScheme) => void;
  accent: AccentScheme;
  setAccent: (accent: AccentScheme) => void;
}

const ThemeContext = createContext<ThemeContextValue | null>(null);

const STORAGE_KEY_THEME = "0x-copilot-theme";
const STORAGE_KEY_ACCENT = "0x-copilot-accent";
const DEFAULT_SCHEME: ThemeScheme = "dark";
const DEFAULT_ACCENT: AccentScheme = "sky";

export function ThemeProvider({
  children,
  defaultScheme = DEFAULT_SCHEME,
  defaultAccent = DEFAULT_ACCENT,
}: {
  children: ReactNode;
  defaultScheme?: ThemeScheme;
  defaultAccent?: AccentScheme;
}): ReactElement {
  const [scheme, setSchemeState] = useState<ThemeScheme>(() =>
    readPersisted(STORAGE_KEY_THEME, isThemeScheme, defaultScheme),
  );
  const [accent, setAccentState] = useState<AccentScheme>(() =>
    readPersisted(STORAGE_KEY_ACCENT, isAccentScheme, defaultAccent),
  );

  useEffect(() => {
    document.documentElement.dataset.theme = scheme;
    window.localStorage.setItem(STORAGE_KEY_THEME, scheme);
  }, [scheme]);

  useEffect(() => {
    document.documentElement.dataset.accent = accent;
    window.localStorage.setItem(STORAGE_KEY_ACCENT, accent);
  }, [accent]);

  const value = useMemo(
    () => ({
      scheme,
      setScheme: setSchemeState,
      accent,
      setAccent: setAccentState,
    }),
    [scheme, accent],
  );

  return (
    <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>
  );
}

export function useTheme(): ThemeContextValue {
  const context = useContext(ThemeContext);
  if (context === null) {
    throw new Error("useTheme must be used inside ThemeProvider");
  }
  return context;
}

function readPersisted<T>(
  key: string,
  guard: (value: unknown) => value is T,
  fallback: T,
): T {
  if (typeof window === "undefined") {
    return fallback;
  }
  const stored = window.localStorage.getItem(key);
  return guard(stored) ? stored : fallback;
}

function isThemeScheme(value: unknown): value is ThemeScheme {
  return value === "dark" || value === "light" || value === "slate";
}

function isAccentScheme(value: unknown): value is AccentScheme {
  return ACCENT_SCHEMES.some((scheme) => scheme.id === value);
}

export function Button({
  variant = "primary",
  size = "md",
  className,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "ghost" | "danger";
  size?: "sm" | "md" | "lg";
}): ReactElement {
  return (
    <button
      className={classNames(
        "ui-button",
        `ui-button--${variant}`,
        `ui-button--${size}`,
        className,
      )}
      {...props}
    />
  );
}

export function Card({
  tone = "default",
  className,
  ...props
}: HTMLAttributes<HTMLElement> & {
  tone?: "default" | "muted" | "accent" | "danger";
}): ReactElement {
  return (
    <section
      className={classNames("ui-card", `ui-card--${tone}`, className)}
      {...props}
    />
  );
}

/**
 * Badge — the design's `.chip`: a mono, outlined, no-fill status/metadata tag.
 * Tone recolours text + border only. `dot` renders the leading live-status dot
 * (`.ui-badge__dot`) — off by default, matching the design, which draws it only
 * for a live/running chip. This is the canonical status chip; the old filled
 * status pill was deleted in favour of it.
 */
export function Badge({
  tone = "neutral",
  dot = false,
  className,
  children,
  ...props
}: HTMLAttributes<HTMLSpanElement> & {
  tone?: "neutral" | "success" | "warning" | "danger" | "accent" | "muted";
  dot?: boolean;
}): ReactElement {
  return (
    <span
      className={classNames("ui-badge", `ui-badge--${tone}`, className)}
      {...props}
    >
      {dot ? <span className="ui-badge__dot" aria-hidden="true" /> : null}
      {children}
    </span>
  );
}

export function TextInput({
  className,
  ...props
}: InputHTMLAttributes<HTMLInputElement>): ReactElement {
  return <input className={classNames("ui-input", className)} {...props} />;
}

export function Select({
  className,
  ...props
}: SelectHTMLAttributes<HTMLSelectElement>): ReactElement {
  return <select className={classNames("ui-select", className)} {...props} />;
}

export function Switch({
  checked,
  label,
  className,
  ...props
}: Omit<InputHTMLAttributes<HTMLInputElement>, "type"> & {
  checked: boolean;
  label: string;
}): ReactElement {
  return (
    <label className={classNames("ui-switch", className)}>
      <input type="checkbox" checked={checked} {...props} />
      <span aria-hidden="true" />
      <strong>{label}</strong>
    </label>
  );
}

/**
 * PR 3.4.1 — icon-only switch. Same visual track + knob as ``<Switch>`` but
 * without the visible ``<strong>`` label, for cases where the surrounding
 * row already labels the toggle (e.g. connector popover rows). Pass an
 * ``aria-label`` for screen readers.
 */
export function Toggle({
  checked,
  className,
  ...props
}: Omit<InputHTMLAttributes<HTMLInputElement>, "type"> & {
  checked: boolean;
}): ReactElement {
  return (
    <label className={classNames("ui-switch", "ui-switch--bare", className)}>
      <input type="checkbox" role="switch" checked={checked} {...props} />
      <span aria-hidden="true" />
    </label>
  );
}

export function Field({
  label,
  hint,
  children,
  className,
  ...props
}: LabelHTMLAttributes<HTMLLabelElement> & {
  label: string;
  hint?: string;
  children: ReactNode;
}): ReactElement {
  return (
    <label className={classNames("ui-field", className)} {...props}>
      <span>{label}</span>
      {children}
      {hint ? <small>{hint}</small> : null}
    </label>
  );
}

export function classNames(
  ...values: Array<string | false | null | undefined>
): string {
  return values.filter(Boolean).join(" ");
}

export function IconButton({
  size = "md",
  variant = "default",
  className,
  type = "button",
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  size?: "sm" | "md";
  variant?: "default" | "ghost";
}): ReactElement {
  return (
    <button
      type={type}
      className={classNames(
        "ui-icon-button",
        size === "sm" && "ui-icon-button--sm",
        variant === "ghost" && "ui-icon-button--ghost",
        className,
      )}
      {...props}
    />
  );
}

/* ---------------------------------------------------------------------------
 * Typographic recipes — the COMPOSED text roles (see styles.css `.ui-*` +
 * SKILL.md). Reach for these instead of hand-composing font-size + weight +
 * tracking + transform, which is how the same role drifted across the app.
 * Each wrapper is a thin element + recipe class; `as` picks the tag so the
 * recipe never dictates document semantics.
 * ------------------------------------------------------------------------ */

type TextElementTag = "span" | "p" | "div" | "label" | "legend";

/** Eyebrow / kicker — uppercase label above a heading. */
export function Eyebrow({
  as: Tag = "span",
  className,
  ...props
}: HTMLAttributes<HTMLElement> & { as?: TextElementTag }): ReactElement {
  return <Tag className={classNames("ui-eyebrow", className)} {...props} />;
}

/** Section / group label — uppercase micro-label heading a group. */
export function SectionLabel({
  as: Tag = "div",
  className,
  ...props
}: HTMLAttributes<HTMLElement> & { as?: TextElementTag }): ReactElement {
  return (
    <Tag className={classNames("ui-section-label", className)} {...props} />
  );
}

/** Caption / meta — small secondary text. */
export function Caption({
  as: Tag = "span",
  className,
  ...props
}: HTMLAttributes<HTMLElement> & { as?: TextElementTag }): ReactElement {
  return <Tag className={classNames("ui-caption", className)} {...props} />;
}

/** Item / card / row title — md semibold, flat tracking. */
export function ItemTitle({
  as: Tag = "div",
  className,
  ...props
}: HTMLAttributes<HTMLElement> & { as?: TextElementTag }): ReactElement {
  return <Tag className={classNames("ui-item-title", className)} {...props} />;
}

/** Heading — display face, negative tracking; level sets size + tightness. */
export function Heading({
  level,
  className,
  ...props
}: HTMLAttributes<HTMLHeadingElement> & {
  level: 1 | 2 | 3;
}): ReactElement {
  const Tag = `h${level}` as "h1" | "h2" | "h3";
  return (
    <Tag
      className={classNames("ui-heading", `ui-heading--${level}`, className)}
      {...props}
    />
  );
}

/**
 * Pill — canonical rounded selection chip: optional leading dot + an `active`
 * accent-fill state. `tone` is advisory (muted default); pass `active` for the
 * selected/accent state. A FILLED selection chip, distinct from the outlined,
 * no-fill `<Badge>` status/metadata tag.
 */
export function Pill({
  active = false,
  dot = false,
  className,
  children,
  ...props
}: HTMLAttributes<HTMLSpanElement> & {
  active?: boolean;
  dot?: boolean;
}): ReactElement {
  return (
    <span
      className={classNames(
        "ui-pill",
        active ? "ui-pill--active" : undefined,
        className,
      )}
      {...props}
    >
      {dot ? <span className="ui-pill__dot" aria-hidden="true" /> : null}
      {children}
    </span>
  );
}

/**
 * Brand-aware connector glyph mapping. The set is small and curated:
 * the top connectors that have recognisable monograms or simple shapes
 * the user is used to seeing. Anything outside this map renders the
 * letter-circle fallback.
 *
 * Glyphs are intentionally simple (one-letter or one-symbol) and use
 * the connector's brand colour as the background — recognisable at the
 * 16px sidebar size without being ad-hoc per-component.
 */
interface BrandGlyph {
  label: string;
  bg: string;
  fg: string;
  symbol: string;
}

const BRAND_GLYPHS: Record<string, BrandGlyph> = {
  notion: { label: "Notion", bg: "#ffffff", fg: "#191919", symbol: "N" },
  drive: { label: "Drive", bg: "#ffffff", fg: "#1a73e8", symbol: "▲" },
  slack: { label: "Slack", bg: "#4a154b", fg: "#ffffff", symbol: "#" },
  salesforce: {
    label: "Salesforce",
    bg: "#00a1e0",
    fg: "#ffffff",
    symbol: "S",
  },
  confluence: {
    label: "Confluence",
    bg: "#172b4d",
    fg: "#2684ff",
    symbol: "C",
  },
  github: { label: "GitHub", bg: "#0d1117", fg: "#ffffff", symbol: "G" },
  linear: { label: "Linear", bg: "#5e6ad2", fg: "#ffffff", symbol: "L" },
  figma: { label: "Figma", bg: "#0d0d0d", fg: "#ffffff", symbol: "F" },
  snowflake: { label: "Snowflake", bg: "#29b5e8", fg: "#ffffff", symbol: "S" },
  datadog: { label: "Datadog", bg: "#632ca6", fg: "#ffffff", symbol: "D" },
  intercom: { label: "Intercom", bg: "#1f8ded", fg: "#ffffff", symbol: "I" },
  pagerduty: { label: "PagerDuty", bg: "#06ac38", fg: "#ffffff", symbol: "P" },
  // PR 4.4.6 — catalog vendors that the chat surface and Catalog tab
  // both need. Distinct symbols disambiguate same-initial vendors
  // (e.g. Cloudflare Bindings vs. Cloudflare Observability).
  asana: { label: "Asana", bg: "#f06a6a", fg: "#ffffff", symbol: "A" },
  atlassian: { label: "Atlassian", bg: "#2684ff", fg: "#ffffff", symbol: "A" },
  "cloudflare-bindings": {
    label: "Cloudflare Bindings",
    bg: "#f38020",
    fg: "#ffffff",
    symbol: "⚡",
  },
  "cloudflare-observability": {
    label: "Cloudflare Observability",
    bg: "#f38020",
    fg: "#ffffff",
    symbol: "◈",
  },
  paypal: { label: "PayPal", bg: "#003087", fg: "#ffffff", symbol: "P" },
  plaid: { label: "Plaid", bg: "#111111", fg: "#ffffff", symbol: "P" },
  sentry: { label: "Sentry", bg: "#362d59", fg: "#ffffff", symbol: "S" },
  square: { label: "Square", bg: "#000000", fg: "#ffffff", symbol: "□" },
  zapier: { label: "Zapier", bg: "#ff4a00", fg: "#ffffff", symbol: "Z" },
  custom: { label: "Custom", bg: "#1f1f1f", fg: "#facc15", symbol: "+" },
  web: { label: "Web", bg: "#0f172a", fg: "#facc15", symbol: "🌐" },
  web_search: {
    label: "Web search",
    bg: "#0f172a",
    fg: "#facc15",
    symbol: "🌐",
  },
};

export function AppIcon({
  name,
  color,
  logoUrl,
  size = "sm",
  tone = "brand",
  className,
  ...props
}: HTMLAttributes<HTMLSpanElement> & {
  name: string;
  color?: string;
  /**
   * PR 3.4.1 — server-supplied brand favicon URL. When present, renders an
   * ``<img>`` with ``onError`` fallback to the existing brand-glyph /
   * letter chain. Existing call-sites (no ``logoUrl``) are byte-identical.
   */
  logoUrl?: string | null;
  /**
   * `"sm"` (1.25rem circle, default) / `"lg"` (1.5rem) / `"tile"` (1.875rem =
   * 30px squircle, `--radius-md`). PRD-11 D3 — the design's `.lrow__logo`
   * connector identity tile. Existing call-sites (no `size`) are unchanged.
   */
  size?: "sm" | "lg" | "tile";
  /**
   * `"brand"` (default) paints the brand surface/ink inline; `"neutral"`
   * suppresses that so the chip resolves the design's neutral tile chrome
   * (`--color-surface-elevated` / `--color-text-strong`) via
   * `.ui-app-icon--neutral`. PRD-11 D3: the design's `!important` pair
   * neutralises the mock's inline brand colour, and a brand-saturated 30px
   * tile is the loudest object on an otherwise hairline page.
   */
  tone?: "brand" | "neutral";
}): ReactElement {
  // PR 8.0.1 — brand-aware mapping. Consumers still pass `name={connector.id}`
  // (or any short string for non-connector callers, e.g. avatar initials);
  // brand awareness is internal. Explicit `color` prop wins over the map
  // so existing call-sites that hard-code a tenant brand colour still work.
  const slug = name.toLowerCase();
  const brand = !color ? BRAND_GLYPHS[slug] : undefined;
  const neutral = tone === "neutral";
  const sizeClass =
    size === "lg"
      ? "ui-app-icon--lg"
      : size === "tile"
        ? "ui-app-icon--tile"
        : false;
  const neutralClass = neutral && "ui-app-icon--neutral";
  const [imgFailed, setImgFailed] = useState(false);

  if (logoUrl && !imgFailed) {
    // Brand surface uses the brand-color when known so the chip shape stays
    // consistent with the glyph fallback while the SVG itself loads — unless
    // the caller asked for a neutral tone, in which case the CSS neutral rule
    // owns the surface.
    const surface = brand?.bg ?? color ?? "var(--color-surface-muted)";
    return (
      <span
        className={classNames(
          "ui-app-icon",
          "ui-app-icon--img",
          sizeClass,
          neutralClass,
          className,
        )}
        style={neutral ? undefined : { background: surface }}
        aria-label={brand?.label ?? name}
        {...props}
      >
        <img
          src={logoUrl}
          alt=""
          loading="lazy"
          decoding="async"
          referrerPolicy="no-referrer"
          onError={() => setImgFailed(true)}
        />
      </span>
    );
  }
  if (brand) {
    return (
      <span
        className={classNames(
          "ui-app-icon",
          "ui-app-icon--brand",
          `ui-app-icon--${slug}`,
          sizeClass,
          neutralClass,
          className,
        )}
        style={neutral ? undefined : { background: brand.bg, color: brand.fg }}
        aria-label={brand.label}
        {...props}
      >
        {brand.symbol}
      </span>
    );
  }
  return (
    <span
      className={classNames("ui-app-icon", sizeClass, neutralClass, className)}
      style={
        color && !neutral
          ? { background: color, color: "var(--color-accent-contrast)" }
          : undefined
      }
      aria-label={name}
      {...props}
    >
      {name.charAt(0)}
    </span>
  );
}

export type HarnessRowStatus = "running" | "done" | "error";

/**
 * Inline harness row — the design's compressed tool-call vocabulary.
 *
 *     ✓ tool_name (args) | → result
 *
 * Lightweight by design: no card chrome, no fat success pill. Renders
 * as a single dim line. Multiple in a row read as a list.
 *
 * Consumers compose this for each `tool_call_*` envelope; the
 * `ActivityCard` collapse rule (≥ 4 consecutive harness rows) wraps
 * many of these in a single summary card.
 */
export function HarnessRow({
  status,
  tool,
  args,
  result,
  className,
  ...props
}: HTMLAttributes<HTMLDivElement> & {
  status: HarnessRowStatus;
  tool: string;
  args?: ReactNode;
  result?: ReactNode;
}): ReactElement {
  const glyph = status === "running" ? "·" : status === "error" ? "✕" : "✓";
  return (
    <div
      className={classNames("ui-harness-row", className)}
      data-status={status}
      role="status"
      {...props}
    >
      <span className="ui-harness-row__glyph" aria-hidden="true">
        {status === "running" ? (
          <span className="ui-harness-row__spinner" />
        ) : (
          glyph
        )}
      </span>
      <span className="ui-harness-row__tool">{tool}</span>
      {args !== undefined && args !== null && args !== "" ? (
        <span className="ui-harness-row__args">({args})</span>
      ) : null}
      {result !== undefined && result !== null && result !== "" ? (
        <>
          <span className="ui-harness-row__sep" aria-hidden="true">
            |
          </span>
          <span className="ui-harness-row__result">→ {result}</span>
        </>
      ) : null}
    </div>
  );
}

/**
 * One-line italic-dim acknowledgement.
 *
 *     • Got it. Drafting customer-led.
 *
 * Driven by the existing `observation` / `status` envelope kinds.
 */
export function StatusLine({
  children,
  className,
  ...props
}: HTMLAttributes<HTMLDivElement>): ReactElement {
  return (
    <div className={classNames("ui-status-line", className)} {...props}>
      <span className="ui-status-line__bullet" aria-hidden="true">
        •
      </span>
      <span className="ui-status-line__text">{children}</span>
    </div>
  );
}

export type ConnectorChipState =
  | "active"
  | "paused"
  | "disconnected"
  | "workspace-off";

export function ConnectorChip({
  name,
  color,
  state = "active",
  className,
  ...props
}: HTMLAttributes<HTMLSpanElement> & {
  name: string;
  color?: string;
  state?: ConnectorChipState;
}): ReactElement {
  return (
    <span
      className={classNames("ui-connector-chip", className)}
      data-state={state}
      {...props}
    >
      <AppIcon name={name} color={color} />
      {name}
    </span>
  );
}

export interface MenuProps extends HTMLAttributes<HTMLDivElement> {
  open: boolean;
  onClose: () => void;
  anchorRef?: RefObject<HTMLElement | null>;
  side?: "up" | "down";
  align?: "left" | "right";
  children: ReactNode;
}

/**
 * Headless anchored dropdown shell. Mounts only when open; dismisses on Escape
 * or pointerdown outside the menu (and outside anchorRef when provided).
 * Auto-flip placement is intentionally not handled here — consumers that need
 * it pull a placement library where it's actually used.
 */
export function Menu({
  open,
  onClose,
  anchorRef,
  side = "down",
  align = "left",
  className,
  children,
  ...props
}: MenuProps): ReactElement | null {
  const menuRef = useRef<HTMLDivElement>(null);
  const [anchorStyle, setAnchorStyle] = useState<CSSProperties>({});

  useEffect(() => {
    if (!open) {
      return;
    }
    function onPointerDown(event: PointerEvent): void {
      const target = event.target;
      if (!(target instanceof Node)) {
        return;
      }
      if (menuRef.current?.contains(target)) {
        return;
      }
      if (anchorRef?.current?.contains(target)) {
        return;
      }
      onClose();
    }
    function onKeyDown(event: KeyboardEvent): void {
      if (event.key === "Escape") {
        onClose();
      }
    }
    // Reposition / dismiss on viewport changes. Recomputing on scroll is
    // cheap and avoids "menu detached from anchor" jank when the page
    // moves underneath it (e.g. the chat thread auto-scrolling).
    function onResizeOrScroll(): void {
      computePosition();
    }
    window.addEventListener("pointerdown", onPointerDown);
    window.addEventListener("keydown", onKeyDown);
    window.addEventListener("resize", onResizeOrScroll);
    window.addEventListener("scroll", onResizeOrScroll, true);
    return () => {
      window.removeEventListener("pointerdown", onPointerDown);
      window.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("resize", onResizeOrScroll);
      window.removeEventListener("scroll", onResizeOrScroll, true);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, onClose, anchorRef]);

  // Compute fixed-viewport coordinates from the anchor's bounding rect.
  // Use ``position: fixed`` so the menu escapes any ancestor with
  // ``overflow: hidden`` (the chat composer card is the offender that
  // motivated this — it has fixed height + overflow:hidden, which
  // would otherwise clip an absolutely-positioned popup to inside the
  // card).
  function computePosition(): void {
    const anchor = anchorRef?.current;
    if (!anchor) {
      return;
    }
    const rect = anchor.getBoundingClientRect();
    const SPACE = 8; // matches --space-sm
    const next: CSSProperties = { position: "fixed", zIndex: 50 };
    if (side === "up") {
      next.bottom = window.innerHeight - rect.top + SPACE;
    } else {
      next.top = rect.bottom + SPACE;
    }
    if (align === "right") {
      next.right = window.innerWidth - rect.right;
    } else {
      next.left = rect.left;
    }
    // Anchor-width default — once portaled to <body>, percentage widths
    // (e.g. ``width: 100%`` on .aui-user-card__menu) would otherwise
    // resolve against the viewport, blowing the popup full-width. Pin
    // the popup's minimum width to the trigger's so dropdowns
    // naturally match their button. Consumers that want a wider popup
    // (model picker, plus menu) set their own ``min-width`` / ``width``
    // class which still wins via the cascade.
    next.minWidth = `${rect.width}px`;
    next.maxWidth = "min(32rem, calc(100vw - 2rem))";
    setAnchorStyle(next);
  }

  useLayoutEffect(() => {
    if (open) {
      computePosition();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, side, align]);

  if (!open) {
    return null;
  }
  if (typeof document === "undefined") {
    return null;
  }

  return createPortal(
    <div
      ref={menuRef}
      role="menu"
      style={anchorStyle}
      className={classNames(
        "ui-dropdown__menu",
        `ui-dropdown__menu--${side}`,
        `ui-dropdown__menu--align-${align}`,
        className,
      )}
      {...props}
    >
      {children}
    </div>,
    document.body,
  );
}
