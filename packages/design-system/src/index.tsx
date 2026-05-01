import type {
  AnchorHTMLAttributes,
  ButtonHTMLAttributes,
  HTMLAttributes,
  InputHTMLAttributes,
  LabelHTMLAttributes,
  ReactElement,
  ReactNode,
  SelectHTMLAttributes,
  TextareaHTMLAttributes,
} from "react";
import { createContext, useContext, useEffect, useMemo, useState } from "react";

export type ThemeScheme = "dark" | "light" | "slate";

export interface ThemeContextValue {
  scheme: ThemeScheme;
  setScheme: (scheme: ThemeScheme) => void;
}

const ThemeContext = createContext<ThemeContextValue | null>(null);

const STORAGE_KEY = "enterprise-search-theme";
const DEFAULT_SCHEME: ThemeScheme = "dark";

export function ThemeProvider({
  children,
  defaultScheme = DEFAULT_SCHEME,
}: {
  children: ReactNode;
  defaultScheme?: ThemeScheme;
}): ReactElement {
  const [scheme, setSchemeState] = useState<ThemeScheme>(() => {
    if (typeof window === "undefined") {
      return defaultScheme;
    }
    const stored = window.localStorage.getItem(STORAGE_KEY);
    return isThemeScheme(stored) ? stored : defaultScheme;
  });

  useEffect(() => {
    document.documentElement.dataset.theme = scheme;
    window.localStorage.setItem(STORAGE_KEY, scheme);
  }, [scheme]);

  const value = useMemo(
    () => ({
      scheme,
      setScheme: setSchemeState,
    }),
    [scheme],
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

export function isThemeScheme(value: unknown): value is ThemeScheme {
  return value === "dark" || value === "light" || value === "slate";
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

export function IconButton({
  label,
  children,
  className,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  label: string;
}): ReactElement {
  return (
    <button
      className={classNames("ui-icon-button", className)}
      aria-label={label}
      {...props}
    >
      {children}
    </button>
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

export function Textarea({
  className,
  ...props
}: TextareaHTMLAttributes<HTMLTextAreaElement>): ReactElement {
  return (
    <textarea className={classNames("ui-textarea", className)} {...props} />
  );
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

export function Dialog({
  open,
  title,
  children,
  footer,
  onClose,
}: {
  open: boolean;
  title: string;
  children: ReactNode;
  footer?: ReactNode;
  onClose: () => void;
}): ReactElement | null {
  if (!open) {
    return null;
  }

  return (
    <div
      className="ui-dialog-backdrop"
      role="presentation"
      onMouseDown={onClose}
    >
      <section
        className="ui-dialog"
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header>
          <h2>{title}</h2>
          <IconButton label="Close dialog" onClick={onClose}>
            ×
          </IconButton>
        </header>
        <div className="ui-dialog__body">{children}</div>
        {footer ? <footer>{footer}</footer> : null}
      </section>
    </div>
  );
}

export function Tabs<TValue extends string>({
  tabs,
  value,
  onChange,
  className,
}: {
  tabs: Array<{ value: TValue; label: string }>;
  value: TValue;
  onChange: (value: TValue) => void;
  className?: string;
}): ReactElement {
  return (
    <div className={classNames("ui-tabs", className)} role="tablist">
      {tabs.map((tab) => (
        <button
          key={tab.value}
          className={tab.value === value ? "is-active" : undefined}
          type="button"
          role="tab"
          aria-selected={tab.value === value}
          onClick={() => onChange(tab.value)}
        >
          {tab.label}
        </button>
      ))}
    </div>
  );
}

export function DropdownMenu({
  trigger,
  open,
  children,
  className,
}: {
  trigger: ReactNode;
  open: boolean;
  children: ReactNode;
  className?: string;
}): ReactElement {
  return (
    <div className={classNames("ui-dropdown", className)}>
      {trigger}
      {open ? <div className="ui-dropdown__menu">{children}</div> : null}
    </div>
  );
}

export function Sidebar({
  children,
  className,
  ...props
}: HTMLAttributes<HTMLElement>): ReactElement {
  return (
    <aside className={classNames("ui-sidebar", className)} {...props}>
      {children}
    </aside>
  );
}

export function LinkButton({
  className,
  ...props
}: AnchorHTMLAttributes<HTMLAnchorElement>): ReactElement {
  return <a className={classNames("ui-link-button", className)} {...props} />;
}

export function classNames(
  ...values: Array<string | false | null | undefined>
): string {
  return values.filter(Boolean).join(" ");
}
