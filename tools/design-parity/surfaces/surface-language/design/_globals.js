/* HARNESS — not design source.
 *
 * The vendored Claude Design files (`surface-kit.jsx`, `surface-specs.jsx`,
 * `surface-archetypes2.jsx`) are CLASSIC SCRIPTS: they read `React` / `Icon` as
 * free globals and publish via `Object.assign(window, …)`. This module puts the
 * globals in place BEFORE any of them evaluate.
 *
 * Import order is the contract. ESM evaluates a module's dependencies in source
 * order and each dependency's body runs before the importer's, so `_mount.jsx`
 * listing this file first is what guarantees `window.React` exists by the time
 * `const { useState: useA } = React;` runs at the top of an archetype file.
 * Reorder those imports and the design side dies with a ReferenceError.
 */
import React from "react";
import { createRoot } from "react-dom/client";

globalThis.React = React;
globalThis.ReactDOM = { createRoot };
