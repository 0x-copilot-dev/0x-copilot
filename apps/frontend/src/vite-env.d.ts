/// <reference types="vite/client" />

declare const __BUILD_SHA__: string;
declare const __DEPLOY_ENV__: string;

// `tinykeys` ships a `.d.ts` but its `package.json#exports` map omits the
// `types` condition, so TypeScript can't pick the upstream typings up via
// node-style resolution. Mirror the small subset of the API we actually use
// here. See node_modules/tinykeys/dist/tinykeys.d.ts for the full surface.
declare module "tinykeys" {
  export interface KeyBindingMap {
    [keybinding: string]: (event: KeyboardEvent) => void;
  }
  export interface KeyBindingOptions {
    event?: "keydown" | "keyup";
    capture?: boolean;
    timeout?: number;
    /** v4: predicate deciding which keyboard events to drop before matching. */
    ignore?: (event: KeyboardEvent) => boolean;
  }
  export function tinykeys(
    target: Window | HTMLElement,
    keyBindingMap: KeyBindingMap,
    options?: KeyBindingOptions,
  ): () => void;
}
