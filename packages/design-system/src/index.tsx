import type {
  ButtonHTMLAttributes,
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
  useMemo,
  useRef,
  useState,
} from "react";

export type ThemeScheme = "dark" | "light" | "slate";

export type AccentScheme =
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

const STORAGE_KEY_THEME = "enterprise-search-theme";
const STORAGE_KEY_ACCENT = "enterprise-search-accent";
const DEFAULT_SCHEME: ThemeScheme = "dark";
const DEFAULT_ACCENT: AccentScheme = "atlas-orange";

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

export function Badge({
  tone = "neutral",
  className,
  ...props
}: HTMLAttributes<HTMLSpanElement> & {
  tone?: "neutral" | "success" | "warning" | "danger" | "accent";
}): ReactElement {
  return (
    <span
      className={classNames("ui-badge", `ui-badge--${tone}`, className)}
      {...props}
    />
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

export type StatusTone = "running" | "ready" | "idle";

export function StatusPill({
  tone,
  label,
  className,
  ...props
}: HTMLAttributes<HTMLSpanElement> & {
  tone: StatusTone;
  label: string;
}): ReactElement {
  return (
    <span
      className={classNames(
        "ui-status-pill",
        `ui-status-pill--${tone}`,
        className,
      )}
      {...props}
    >
      <span className="ui-status-pill__dot" aria-hidden="true" />
      {label}
    </span>
  );
}

export function AppIcon({
  name,
  color,
  size = "sm",
  className,
  ...props
}: HTMLAttributes<HTMLSpanElement> & {
  name: string;
  color?: string;
  size?: "sm" | "lg";
}): ReactElement {
  return (
    <span
      className={classNames(
        "ui-app-icon",
        size === "lg" && "ui-app-icon--lg",
        className,
      )}
      style={
        color
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
    window.addEventListener("pointerdown", onPointerDown);
    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("pointerdown", onPointerDown);
      window.removeEventListener("keydown", onKeyDown);
    };
  }, [open, onClose, anchorRef]);

  if (!open) {
    return null;
  }

  return (
    <div
      ref={menuRef}
      role="menu"
      className={classNames(
        "ui-dropdown__menu",
        `ui-dropdown__menu--${side}`,
        `ui-dropdown__menu--align-${align}`,
        className,
      )}
      {...props}
    >
      {children}
    </div>
  );
}
