#!/usr/bin/env node
/**
 * Vendor and materialise the user-supplied Generative Surfaces v3 Design
 * Compiler walkthrough for deterministic computed-style extraction.
 *
 * The three `reference.*` files remain byte-for-byte copies of the supplied
 * source. `index.html` changes only state selection and autoplay; it does not
 * change visual rules or copy.
 */
import { createHash } from "node:crypto";
import { copyFileSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const DESIGN = resolve(HERE, "../surfaces/generative-surfaces-v3/design");
const DEFAULT_SOURCE = resolve(
  process.env.HOME,
  "Downloads/Copilot project folder copy",
);
const SOURCE = resolve(
  process.env.GENERATIVE_SURFACES_V3_SOURCE_DIR || DEFAULT_SOURCE,
);
const files = {
  html: resolve(SOURCE, "Generative Surfaces v3.dc.html"),
  support: resolve(SOURCE, "support.js"),
  css: resolve(SOURCE, "copilot-v3.css"),
  baseCss: resolve(SOURCE, "copilot.css"),
};

mkdirSync(DESIGN, { recursive: true });
copyFileSync(files.html, resolve(DESIGN, "reference.dc.html"));
copyFileSync(files.support, resolve(DESIGN, "support.js"));
copyFileSync(files.css, resolve(DESIGN, "copilot-v3.css"));
copyFileSync(files.baseCss, resolve(DESIGN, "copilot.css"));

const source = readFileSync(files.html, "utf8");
const autoAdvance =
  /(&quot;autoAdvance&quot;:\s*\{[^}]*&quot;default&quot;:\s*)true/;
if (!autoAdvance.test(source)) {
  throw new Error(
    "The supplied v3 reference no longer exposes autoAdvance=true; update the transform deliberately.",
  );
}

const initialState = /state = this\.snap\(0\);/;
if (!initialState.test(source)) {
  throw new Error(
    "The supplied v3 reference no longer exposes `state = this.snap(0)`; update the state transform deliberately.",
  );
}

const controlled = source
  .replace(autoAdvance, "$1false")
  .replace(
    initialState,
    `state = (() => {
    const selected = new URLSearchParams(window.location.search).get("state") || "draft-held";
    if (selected === "draft-held" || selected === "draft-edit") {
      const s = this.snap(2);
      s.tools.gm = this.tool("ok", "ask");
      s.dPhase = selected === "draft-edit" ? "edit" : "ready";
      s.dSegs = 3;
      s.tab = "draft";
      s.msgs.push(connectedEv("gm", noteFor("gm", "ask")), ...POST.gm);
      return s;
    }
    if (selected === "bulk-review" || selected === "bulk-partial") {
      const s = this.snap(3);
      s.tools.sf = this.tool("ok", "ask");
      s.bulk = selected === "bulk-partial" ? "partial" : "review";
      s.tab = "bulk";
      s.msgs.push(connectedEv("sf", noteFor("sf", "ask")), ...POST.sf);
      if (selected === "bulk-partial") {
        s.rows = s.rows.map((row, index) => ({
          ...row,
          stale: false,
          run: FAIL_IDX.includes(index)
            ? "failed"
            : row.dec === "yes"
              ? "done"
              : "idle",
        }));
      }
      return s;
    }
    if (selected === "sources") {
      const s = this.snap(5);
      s.tools.li = this.tool("ok", "ask");
      s.tools.gm = this.tool("ok", "ask");
      s.tools.sf = this.tool("ok", "ask");
      s.tools.fa = this.tool("ok", "ask");
      s.sdTab = "sources";
      return s;
    }
    throw new Error("Unknown Generative Surfaces v3 parity state: " + selected);
  })();`,
  )
  // The supplied component always starts the compose typing timer even when a
  // deterministic capture selects a later phase. Keep that timer for compose,
  // but do not let it re-render and erase runtime-only anchors in review states.
  .replace(
    'this.enterPhase("compose");',
    'if (this.state.phase === "compose") this.enterPhase("compose");',
  );

if (!controlled.includes("</body>")) {
  throw new Error("The supplied v3 reference has no </body> insertion point.");
}
const output = controlled.replace(
  "</body>",
  '\n<!-- parity harness: state/anchors only; visual rules unchanged -->\n<script src="./state-driver.js"></script>\n</body>',
);
writeFileSync(resolve(DESIGN, "index.html"), output);

const sha256 = (path) =>
  createHash("sha256").update(readFileSync(path)).digest("hex");
const provenance = {
  source: "user-supplied Downloads/Copilot project folder copy",
  generated_by: "prepare-generative-surfaces-v3-reference.mjs",
  files: {
    "reference.dc.html": sha256(files.html),
    "support.js": sha256(files.support),
    "copilot-v3.css": sha256(files.css),
    "copilot.css": sha256(files.baseCss),
  },
};
writeFileSync(
  resolve(DESIGN, "PROVENANCE.json"),
  `${JSON.stringify(provenance, null, 2)}\n`,
);
console.log(`prepared ${resolve(DESIGN, "index.html")}`);
console.log(`reference sha256 ${provenance.files["reference.dc.html"]}`);
