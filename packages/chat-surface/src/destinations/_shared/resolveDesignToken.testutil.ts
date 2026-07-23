// Test-only helper: resolve a design-system CSS custom-property value to the
// literal a browser would COMPUTE.
//
// Why this exists: jsdom's `getComputedStyle` does not substitute `var(--x)` —
// it returns the literal string `"var(--font-weight-regular)"`, so a jsdom test
// cannot read the resolved `400`/`500`/`10px` off a tokenised inline style. The
// design-parity harness measures those in real Chromium, but the unit DoD also
// wants the RESOLVED number pinned here. This resolver reads the token chain
// from `packages/design-system/src/styles.css` — the single source of truth the
// browser itself consumes — and substitutes it, so `resolveDesignToken(el.style
// .fontWeight)` returns `"400"` exactly as the browser computes it. That is a
// STRONGER assertion than the token-string check (it fails if the token is ever
// redefined off-value), not a weaker one.

/// <reference types="node" />
import { existsSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

// Resolve `packages/design-system/src/styles.css` from this test file's own
// location. Under vitest+jsdom `import.meta.url` is a `file://` URL to this
// module; convert it to a path and walk up to the monorepo `packages/` dir. A
// cwd-based candidate is kept as a fallback so the helper survives being run
// from a different working directory (CI, --root variations).
const REL = "packages/design-system/src/styles.css";

function locateStylesCss(): string {
  const candidates: string[] = [];
  try {
    const here = dirname(fileURLToPath(import.meta.url));
    // here == …/packages/chat-surface/src/destinations/_shared
    candidates.push(
      resolve(here, "../../../..", "design-system/src/styles.css"),
    );
  } catch {
    /* import.meta.url not a file URL in this runner — fall through to cwd */
  }
  let dir = process.cwd();
  for (let i = 0; i < 8; i++) {
    candidates.push(resolve(dir, REL));
    dir = dirname(dir);
  }
  const found = candidates.find((p) => existsSync(p));
  if (!found) {
    throw new Error(
      `resolveDesignToken: could not locate ${REL}. Tried:\n${candidates.join("\n")}`,
    );
  }
  return found;
}

let tokenMap: Map<string, string> | null = null;

function loadTokenMap(): Map<string, string> {
  if (tokenMap) return tokenMap;
  const css = readFileSync(locateStylesCss(), "utf8");
  const map = new Map<string, string>();
  // Match every `--name: value;` declaration in the file. These tokens are
  // uniquely named; the ones this helper resolves (font weights, mono-10) are
  // each declared exactly once at :root, so first-wins is unambiguous.
  const re = /(--[a-z0-9-]+)\s*:\s*([^;}]+)[;}]/gi;
  let m: RegExpExecArray | null;
  while ((m = re.exec(css)) !== null) {
    const name = m[1]!.trim();
    if (!map.has(name)) map.set(name, m[2]!.trim());
  }
  tokenMap = map;
  return map;
}

/**
 * Resolve a CSS value that may contain `var(--token)` references to the literal
 * a browser computes, converting `rem` to `px` at the 16px root default so the
 * result matches `getComputedStyle` output (e.g. `0.625rem` → `"10px"`).
 */
export function resolveDesignToken(value: string, depth = 0): string {
  if (depth > 10) return value;
  const map = loadTokenMap();
  const varMatch = /^var\(\s*(--[a-z0-9-]+)\s*(?:,[^)]*)?\)$/i.exec(
    value.trim(),
  );
  if (varMatch) {
    const resolved = map.get(varMatch[1]!);
    if (resolved === undefined) return value;
    return resolveDesignToken(resolved, depth + 1);
  }
  // rem → px (16px root). Leave other units untouched.
  const rem = /^(-?[\d.]+)rem$/i.exec(value.trim());
  if (rem) return `${parseFloat(rem[1]!) * 16}px`;
  return value.trim();
}
