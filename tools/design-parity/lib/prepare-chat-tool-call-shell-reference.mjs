#!/usr/bin/env node
/**
 * Materialise the supplied Design Compiler walkthrough for repeatable parity
 * extraction. `reference.dc.html` remains the byte-for-byte supplied baseline;
 * this script makes the one non-visual harness adjustment needed for stable
 * capture: it disables the mock's animated auto-advance and loads the
 * query-state driver.
 *
 * Do not hand-edit `design/index.html`. Re-run this command after updating the
 * vendored reference instead:
 *
 *   node tools/design-parity/lib/prepare-chat-tool-call-shell-reference.mjs
 */
import { readFileSync, writeFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const DESIGN = resolve(HERE, "../surfaces/chat-tool-call-shell/design");
const sourcePath = resolve(DESIGN, "reference.dc.html");
const outPath = resolve(DESIGN, "index.html");
const source = readFileSync(sourcePath, "utf8");

// The source contains a JSON-ish `data-props` HTML attribute, so match the
// autoAdvance declaration narrowly rather than replacing every `true` token.
const autoAdvance =
  /(&quot;autoAdvance&quot;:\s*\{[^}]*&quot;default&quot;:\s*)true/;
if (!autoAdvance.test(source)) {
  throw new Error(
    "The supplied Chat & Tool Calls reference no longer exposes autoAdvance=true; update this transform deliberately.",
  );
}

const controlled = source.replace(autoAdvance, "$1false");
// The supplied mock keeps walkthrough state inside its DCLogic instance. A
// browser click is not a reliable state-control API (the compiler deliberately
// treats handler expressions as source-owned), so the generated capture copy
// reads the *requested* state at construction time. This changes no visual rule
// or fixture data: it selects the source mock's own mode/step before first
// render, exactly as a human pressing its walkthrough button would.
const initialState =
  /state = \{ mode: "focus", step: 0, reveal: STEP_ITEMS\[0\], activeIdx: -1, open: \{\}, think: \{\}, cmp: "" \};/;
if (!initialState.test(controlled)) {
  throw new Error(
    "The supplied reference no longer exposes the known walkthrough state initializer; update the state transform deliberately.",
  );
}
const stateControlled = controlled.replace(
  initialState,
  `state = (() => {
    const selected = new URLSearchParams(window.location.search).get("state");
    const states = {
      "focus-thinking": { mode: "focus", step: 0 },
      "studio-third-party-read": { mode: "studio", step: 1 },
      "studio-web-chat-only": { mode: "studio", step: 2 },
      "studio-csv-chat-only": { mode: "studio", step: 3 },
      "studio-write-held": { mode: "studio", step: 4 },
      "studio-wrap-file": { mode: "studio", step: 5 },
    };
    const value = states[selected] || states["focus-thinking"];
    return { mode: value.mode, step: value.step, reveal: STEP_ITEMS[value.step], activeIdx: -1, open: {}, think: {}, cmp: "" };
  })();`,
);
const driver =
  "\n<!-- parity harness: state control only; no visual design rules are changed -->\n" +
  '<script src="./state-driver.js"></script>\n';
if (!controlled.includes("</body>")) {
  throw new Error("The supplied reference has no </body> insertion point.");
}
const output = stateControlled.replace("</body>", `${driver}</body>`);
writeFileSync(outPath, output);

const digest = createHash("sha256").update(source).digest("hex");
console.log(`prepared ${outPath}`);
console.log(`reference sha256 ${digest}`);
