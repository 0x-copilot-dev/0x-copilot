/* design-parity · comparator (node, no deps)
 * =========================================================================
 * Diffs two computed-style profiles (design baseline vs live app) produced by
 * lib/extract-computed.js, aligned label-for-label. Classifies each mismatch by
 * property type + magnitude, annotates colors with their design-token name, and
 * honors "expected divergences" declared in anchors.json (e.g. a deliberately
 * shelved element). Emits a Markdown report + a JSON summary.
 *
 * `expectDivergence` (per anchor) takes two forms:
 *   "reason"                       — a PRESENCE divergence in either direction
 *                                    (element missing in live, or live-only).
 *   { absent, extra, text, <prop> } — scoped. `absent`/`extra` are the two
 *                                    presence directions; `text` is a copy
 *                                    difference; ANY OTHER key is a computed
 *                                    style property (`color`, `width`, …) whose
 *                                    diff is expected. Only the declared keys
 *                                    are downgraded to INFO — every other
 *                                    property on that element still scores
 *                                    normally, so "one intended delta" can
 *                                    never launder a whole element's drift.
 *
 * Usage:
 *   node lib/compare.mjs \
 *     surfaces/first-run/out/design-<state>.json \
 *     surfaces/first-run/out/live-<state>.json \
 *     --anchors surfaces/first-run/anchors.json \
 *     --out surfaces/first-run/out/report.md
 * The design side is the SOURCE OF TRUTH: the live app should match it.
 * ========================================================================= */
import { readFileSync, writeFileSync } from "node:fs";

// --- design-token reverse map (value -> name), for actionable color diffs ----
// Values from the design baseline copilot.css :root (== design-system styles.css
// token values). Both sides resolve to these, so it annotates either side.
const TOKENS = {
  "rgb(236, 236, 241)": "--tx",
  "rgb(212, 212, 219)": "--tx2",
  "rgb(152, 152, 159)": "--mut",
  "rgb(100, 100, 109)": "--mut2",
  "rgb(95, 178, 236)": "--accent/--sky",
  "rgb(87, 199, 133)": "--jade",
  "rgb(17, 17, 20)": "--panel",
  "rgb(22, 22, 26)": "--panel2",
  "rgb(29, 29, 35)": "--panel3",
  "rgb(11, 10, 14)": "#0b0a0e (literal near-black)",
  "rgb(8, 19, 29)": "--accent-ink",
  "rgba(0, 0, 0, 0)": "transparent",
  "rgba(255, 255, 255, 0.06)": "--line",
  "rgba(255, 255, 255, 0.1)": "--line2",
  "rgba(255, 255, 255, 0.18)": "--line3",
};
const tok = (v) => (TOKENS[v] ? `${v} (${TOKENS[v]})` : v);

// --- property taxonomy -------------------------------------------------------
const TYPO = new Set([
  "fontFamily",
  "fontSize",
  "fontWeight",
  "fontStyle",
  "lineHeight",
  "letterSpacing",
  "textTransform",
  "textAlign",
]);
const COLOR = new Set(["color", "backgroundColor", "borderColor"]);
const BOX = new Set([
  "padding",
  "margin",
  "gap",
  "borderWidth",
  "borderRadius",
]);
const LAYOUT = new Set([
  "display",
  "flexDirection",
  "justifyContent",
  "alignItems",
  "flexGrow",
  "flexWrap",
]);

const px = (v) => {
  const m = /(-?\d*\.?\d+)px/.exec(v || "");
  return m ? parseFloat(m[1]) : null;
};
const fam = (v) => (v || "").toLowerCase();
const isMono = (v) => fam(v).includes("mono");

// Classify one property mismatch → { severity, note } or null (no material diff).
function classify(prop, d, l) {
  if (d === l) return null;

  if (prop === "fontFamily") {
    // Only flag a *typeface class* change (mono<->sans), not vendor-string noise.
    if (isMono(d) !== isMono(l))
      return {
        severity: "high",
        note: `typeface class changed (${isMono(d) ? "mono" : "sans"} → ${isMono(l) ? "mono" : "sans"})`,
      };
    return null;
  }
  if (prop === "fontSize") {
    const dd = px(d),
      ll = px(l);
    if (dd == null || ll == null)
      return { severity: "medium", note: `${d} → ${l}` };
    const delta = Math.abs(dd - ll);
    if (delta < 0.4) return null;
    return {
      severity: delta >= 2 ? "high" : "medium",
      note: `${d} → ${l} (${(ll - dd >= 0 ? "+" : "") + (ll - dd).toFixed(1)}px)`,
    };
  }
  if (prop === "fontWeight") return { severity: "medium", note: `${d} → ${l}` };
  if (prop === "lineHeight" || prop === "letterSpacing") {
    const dd = px(d),
      ll = px(l);
    if (dd != null && ll != null && Math.abs(dd - ll) < 0.5) return null;
    return { severity: "low", note: `${d} → ${l}` };
  }
  if (COLOR.has(prop)) {
    // transparent<->transparent variants are noise; real hue/token swaps matter.
    return { severity: "high", note: `${tok(d)} → ${tok(l)}` };
  }
  if (BOX.has(prop)) {
    return { severity: "medium", note: `${d} → ${l}` };
  }
  if (LAYOUT.has(prop)) {
    if (prop === "flexGrow")
      return {
        severity: "medium",
        note: `flex-grow ${d} → ${l} (affects vertical fill / button placement)`,
      };
    return { severity: "medium", note: `${d} → ${l}` };
  }
  return { severity: "low", note: `${d} → ${l}` };
}

// --- CLI --------------------------------------------------------------------
const argv = process.argv.slice(2);
const positionals = argv.filter((a) => !a.startsWith("--"));
const flag = (name) => {
  const i = argv.indexOf(`--${name}`);
  return i >= 0 ? argv[i + 1] : null;
};
const [designPath, livePath] = positionals;
const anchorsPath = flag("anchors");
const outPath = flag("out") || "report.md";
const state = flag("state") || "gate";

const design = JSON.parse(readFileSync(designPath, "utf8"));
const live = JSON.parse(readFileSync(livePath, "utf8"));
const anchors = anchorsPath
  ? JSON.parse(readFileSync(anchorsPath, "utf8"))
  : null;
const anchorByLabel = new Map(
  (anchors?.elements || []).map((e) => [e.label, e]),
);

// Preserve design (source-of-truth) ordering; append any live-only labels.
const labels = [
  ...Object.keys(design),
  ...Object.keys(live).filter((l) => !(l in design)),
];

const findings = [];
const RANK = { high: 0, medium: 1, low: 2, info: 3 };

/**
 * Normalize an anchor's `expectDivergence` into a `{scope -> reason}` map. A
 * bare string is the historical form and declares a PRESENCE divergence in
 * either direction, so the surfaces already using it keep scoring identically.
 */
function expectations(anchor) {
  const declared = anchor?.expectDivergence;
  if (!declared) return {};
  if (typeof declared === "string")
    return { absent: declared, extra: declared };
  return declared;
}

for (const label of labels) {
  const a = anchorByLabel.get(label);
  const group = a?.group || "—";
  const expect = expectations(a);
  const d = design[label];
  const l = live[label];

  // presence divergences
  const dMatched = d && d.matched !== false;
  const lMatched = l && l.matched !== false;
  if (dMatched && !lMatched) {
    const expected = expect.absent || l?.note;
    findings.push({
      label,
      group,
      severity: expected ? "info" : "high",
      kind: "missing-in-live",
      detail: expected
        ? `expected: ${expected}`
        : "present in design, ABSENT in live",
    });
    continue;
  }
  if (!dMatched && lMatched) {
    findings.push({
      label,
      group,
      severity: "info",
      kind: "extra-in-live",
      detail: expect.extra
        ? `expected: ${expect.extra}`
        : "present in live, not in design map",
    });
    continue;
  }
  if (!dMatched && !lMatched) continue;

  // text (copy) divergence — informational unless it changes meaning
  if (d.text != null && l.text != null && d.text !== l.text) {
    findings.push({
      label,
      group,
      severity: "info",
      kind: "copy",
      prop: "text",
      detail: `${expect.text ? `expected: ${expect.text} — ` : ""}“${d.text}” → “${l.text}”`,
    });
  }

  // style diffs (design is source of truth)
  const ds = d.styles || {};
  const ls = l.styles || {};
  for (const prop of Object.keys(ds)) {
    if (!(prop in ls)) continue;
    const c = classify(prop, ds[prop], ls[prop]);
    if (!c) continue;
    // A declared, property-scoped divergence is intent, not a defect — file it
    // as INFO but keep the measured delta in the detail so it stays auditable.
    const reason = expect[prop];
    findings.push({
      label,
      group,
      severity: reason ? "info" : c.severity,
      kind: "style",
      prop,
      detail: reason ? `expected: ${reason} — ${c.note}` : c.note,
    });
  }
  // tag change (b -> h2 etc.)
  if (d.tag && l.tag && d.tag !== l.tag) {
    findings.push({
      label,
      group,
      severity: "low",
      kind: "tag",
      detail: `<${d.tag}> → <${l.tag}> (semantic/default-style change)`,
    });
  }
}

findings.sort((x, y) => RANK[x.severity] - RANK[y.severity]);
const counts = findings.reduce(
  (m, f) => ((m[f.severity] = (m[f.severity] || 0) + 1), m),
  {},
);

// --- render markdown --------------------------------------------------------
const SEV_LABEL = {
  high: "🔴 HIGH",
  medium: "🟠 MEDIUM",
  low: "🟡 LOW",
  info: "⚪ INFO",
};
const surface = flag("surface");
let md = `# Design-parity report — ${surface ? surface + " · " : ""}\`${state}\`\n\n`;
md += `Design baseline (source of truth) vs live app, by computed style.\n\n`;
md += `- Design: \`${designPath}\`\n- Live: \`${livePath}\`\n\n`;
md += `**Summary:** `;
md += ["high", "medium", "low", "info"]
  .map((s) => `${SEV_LABEL[s]} ${counts[s] || 0}`)
  .join(" · ");
md += `\n\n`;

for (const sev of ["high", "medium", "low", "info"]) {
  const group = findings.filter((f) => f.severity === sev);
  if (!group.length) continue;
  md += `## ${SEV_LABEL[sev]} (${group.length})\n\n`;
  md += `| Element | Group | Property | Design → Live |\n|---|---|---|---|\n`;
  for (const f of group) {
    md += `| \`${f.label}\` | ${f.group} | ${f.prop || f.kind} | ${f.detail.replace(/\|/g, "\\|")} |\n`;
  }
  md += `\n`;
}

writeFileSync(outPath, md);
writeFileSync(
  outPath.replace(/\.md$/, ".json"),
  JSON.stringify({ state, counts, findings }, null, 2),
);
console.log(`report: ${outPath}`);
console.log(
  `findings: ${["high", "medium", "low", "info"].map((s) => `${s}=${counts[s] || 0}`).join(" ")}`,
);
