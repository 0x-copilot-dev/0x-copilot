// Desktop appearance host-paint (PRD-12 D9).
//
// `@0x-copilot/chat-surface` bans `document` (it is substrate-agnostic), so the
// HOST owns the one DOM write that stamps the design-system attributes onto the
// document root. This is the `onApply` the boot controller (`useAppearanceSettings`)
// calls on load and on every change. Lifted verbatim out of `SettingsMount.tsx`
// so it can be mounted at the renderer ROOT (attributes correct on every screen
// at launch, not only after Settings opens) — the load-bearing half of the G7 fix.
//
// Desktop mounts NO design-system `ThemeProvider` (`ast-allowlist.ts:18`), so
// these `:root[data-*]` attributes are the ONLY theming mechanism — which is why
// PRD-01's nine accents are unobservable on desktop until this write happens.

import type { AppearanceAttributes } from "@0x-copilot/chat-surface";

/** Stamp the resolved appearance attributes onto the document root. */
export function applyAppearance(attrs: AppearanceAttributes): void {
  if (typeof document === "undefined") return;
  const root = document.documentElement;
  root.setAttribute("data-theme", attrs["data-theme"]);
  root.setAttribute("data-accent", attrs["data-accent"]);
  root.setAttribute("data-density", attrs["data-density"]);
  root.setAttribute("data-reduce-motion", attrs["data-reduce-motion"]);
}
