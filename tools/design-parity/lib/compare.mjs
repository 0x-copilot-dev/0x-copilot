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
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";

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
  // Depth/emphasis carriers. A selection RING drawn as a box-shadow and a lost
  // backdrop blur are visual defects of the same weight as a padding change, so
  // they classify MEDIUM rather than falling through to the LOW default.
  "boxShadow",
  "backdropFilter",
  "textDecorationLine",
  // DECLARED size constraints and offsets — not the measured `width`/`height`
  // rows, which are container-dependent noise. A lane that declares
  // `max-height:352px` and a sticky header that declares `top:-10px` are stating
  // an intended geometry, and they compare cleanly across viewports.
  "maxHeight",
  "minHeight",
  "top",
]);
const LAYOUT = new Set([
  "display",
  "flexDirection",
  "justifyContent",
  "alignItems",
  "flexGrow",
  "flexWrap",
  // Grid track definition and scroll/positioning behaviour. These are the
  // properties a "lanes" or "field rows" layout is actually built from: losing
  // `position:sticky`, a `grid-auto-columns` track, or a contained overscroll
  // changes the rendered structure exactly as much as flipping `display` does.
  "position",
  "gridAutoFlow",
  "gridAutoColumns",
  "gridTemplateColumns",
  "overflowX",
  "overflowY",
  "overscrollBehaviorX",
  "overscrollBehaviorY",
]);

const px = (v) => {
  const m = /(-?\d*\.?\d+)px/.exec(v || "");
  return m ? parseFloat(m[1]) : null;
};
const fam = (v) => (v || "").toLowerCase();
const isMono = (v) => fam(v).includes("mono");

// --- color parsing ----------------------------------------------------------
// Everything below answers ONE question: do these two computed values paint the
// same pixel? It must never answer "yes" when they do not — a comparator that
// hides a difference is worse than one that cannot parse, because the first is
// silent and the second is at least visible as a row nobody can act on.

const clamp = (v, lo, hi) => (v < lo ? lo : v > hi ? hi : v);

/** How far past the sRGB cube still counts as rounding rather than a color
 * sRGB cannot show, in 0–255 units. Half an 8-bit step: a channel landing at
 * 255.2 is white that authoring/serialization rounding pushed a hair past the
 * wall, and there is no other color it could have been. Used ONLY by the gamut
 * test in `parsedOklch` — channel EQUALITY is decided by rounding to the byte
 * the browser paints (see `colorsMatch`), not by a tolerance window. */
const GAMUT_SLACK = 0.5;
/** Alpha is compared as a number, not as a byte.
 *
 * The asymmetry with the channels is deliberate. Chrome serializes alpha as the
 * authored decimal (`oklch(1 0 0 / 0.115)`, `rgba(…, 0.06)`), so there is no
 * byte to round to at this layer. 0.001 is finer than one 8-bit alpha step
 * (1/255 ≈ 0.0039), so relative to the compositor this can only ever
 * over-report — the safe direction — while still collapsing pure float noise.
 * The rows it must never merge are the hairlines: the design's 0.07 and 0.115
 * against our 0.06 and 0.10. */
const ALPHA_TOLERANCE = 0.001;

/** `<number>` | `<percentage>` | `none`, where 100% === `full`.
 * Returns null for anything else, so a caller bails instead of producing NaN.
 * `none` resolves to 0: CSS Color 4 §4.4 — a missing component acts as zero
 * when the color is rendered (`oklch(1 0 0 / none)` paints fully transparent).
 */
function numberOrPercent(token, full) {
  if (token === "none") return 0;
  const pct = /^[+-]?(?:\d+\.?\d*|\.\d+)%$/.exec(token);
  if (pct) return (Number.parseFloat(token) / 100) * full;
  return /^[+-]?(?:\d+\.?\d*|\.\d+)(?:e[+-]?\d+)?$/i.test(token)
    ? Number(token)
    : null;
}

/** `<hue>` → degrees. Computed values serialize as a bare number, but the
 * angle units are accepted so the parser is total over authored CSS too. */
const HUE_UNIT = { deg: 1, grad: 0.9, rad: 180 / Math.PI, turn: 360 };
function hueDegrees(token) {
  if (token === "none") return 0;
  const m =
    /^([+-]?(?:\d+\.?\d*|\.\d+)(?:e[+-]?\d+)?)(deg|grad|rad|turn)?$/i.exec(
      token,
    );
  if (!m) return null;
  return Number(m[1]) * (m[2] ? HUE_UNIT[m[2].toLowerCase()] : 1);
}

/** sRGB transfer function, sign-preserving.
 * `Math.pow(negative, 1/2.4)` is NaN, and an out-of-gamut oklch routinely
 * produces a negative linear channel. Mirroring the curve through the origin
 * keeps the value finite and ORDERED, so the gamut test below can see how far
 * outside it actually is instead of being handed a NaN. */
function encodeSrgb(linear) {
  const c = Math.abs(linear);
  const encoded =
    c <= 0.0031308 ? 12.92 * c : 1.055 * Math.pow(c, 1 / 2.4) - 0.055;
  return linear < 0 ? -encoded : encoded;
}

const OKLCH =
  /^oklch\(\s*([^\s/()]+)\s+([^\s/()]+)\s+([^\s/()]+)\s*(?:\/\s*([^\s/()]+)\s*)?\)$/i;

/** `oklch()` → `[r, g, b, a]` in 0–255 (+ alpha 0–1), or null.
 *
 * oklch → oklab (polar → cartesian) → LMS → linear sRGB → gamma-encoded sRGB,
 * with Björn Ottosson's published matrices.
 *
 * OUT OF GAMUT ⇒ null (refuse to compare), NOT clamped. A browser renders an
 * out-of-sRGB color by gamut-mapping it (CSS Color 4 §13 chroma reduction),
 * which this file does not implement.
 *
 * Clamping is not a cheap approximation of that — it lands on a DIFFERENT
 * pixel. Measured against Chrome (canvas read-back, the pixel it actually
 * paints): `oklch(0.5 0.4 30)` paints `rgb(253, 0, 0)`, `oklch(0.7 0.4 250)`
 * paints `rgb(0, 133, 255)`, `oklch(0.9 0.3 140)` paints `rgb(75, 255, 0)` —
 * none of which is the face of the cube a clamp pins to. A clamping comparator
 * would therefore call the first EQUAL to a live `rgb(255, 0, 0)` that is two
 * whole steps away: an UNDER-report, the one failure this file forbids.
 *
 * Refusing costs the opposite error, and only in a case this harness does not
 * produce: Chrome maps both `oklch(0.5 0.5 30)` and `oklch(0.6 0.4 30)` to
 * `rgb(255, 0, 0)`, and we call them different. Returning null sends
 * `colorsMatch` back to exact string equality, so the row keeps reporting —
 * over-reporting rather than under-reporting, which is the only safe
 * direction. Every oklch value the surfaces actually emit is in gamut, so this
 * branch is a guard, not a working path.
 *
 * "Out of gamut" is measured with `GAMUT_SLACK` of give, so a channel at 255.2
 * is treated as white rather than as unrepresentable. */
function parsedOklch(value) {
  const m = OKLCH.exec(value);
  if (!m) return null;
  const L = numberOrPercent(m[1], 1);
  // CSS Color 4: chroma 100% === 0.4.
  const C = numberOrPercent(m[2], 0.4);
  const H = hueDegrees(m[3]);
  const alpha = m[4] === undefined ? 1 : numberOrPercent(m[4], 1);
  if (L === null || C === null || H === null || alpha === null) return null;

  const lightness = clamp(L, 0, 1);
  const chroma = Math.max(C, 0);
  const hue = (H * Math.PI) / 180;
  const a = chroma * Math.cos(hue);
  const b = chroma * Math.sin(hue);

  // oklab → LMS (cube of the non-linear response).
  const l_ = lightness + 0.3963377774 * a + 0.2158037573 * b;
  const m_ = lightness - 0.1055613458 * a - 0.0638541728 * b;
  const s_ = lightness - 0.0894841775 * a - 1.291485548 * b;
  const lms = [l_ * l_ * l_, m_ * m_ * m_, s_ * s_ * s_];

  // LMS → linear sRGB.
  const linear = [
    4.0767416621 * lms[0] - 3.3077115913 * lms[1] + 0.2309699292 * lms[2],
    -1.2684380046 * lms[0] + 2.6097574011 * lms[1] - 0.3413193965 * lms[2],
    -0.0041960863 * lms[0] - 0.7034186147 * lms[1] + 1.707614701 * lms[2],
  ];

  const channels = linear.map((c) => encodeSrgb(c) * 255);
  const representable = channels.every(
    (c) => Number.isFinite(c) && c >= -GAMUT_SLACK && c <= 255 + GAMUT_SLACK,
  );
  if (!representable) return null;
  return [...channels.map((c) => clamp(c, 0, 255)), clamp(alpha, 0, 1)];
}

/** Browsers serialize an equivalent token color in more than one computed
 * form: older style rules commonly become `rgba(95, 178, 236, 0.35)`,
 * `color-mix()` becomes `color(srgb 0.372549 0.698039 0.92549 / 0.35)`, and a
 * page authored in oklch keeps `oklch(0.212 0.01 276)` as its computed value.
 * Resolve all of them to one sRGB tuple instead of manufacturing a HIGH delta
 * out of a difference in notation.
 *
 * Not handled (deliberately): `lab()`/`lch()`/`oklab()`/`hwb()` and non-sRGB
 * `color()` spaces, none of which any surface in this harness currently emits,
 * and multi-value lists such as a four-side `borderColor`. Each falls through
 * to null → exact string comparison → the row still reports. Adding an
 * untested space would risk the one failure this file may not have. */
function parsedColor(value) {
  // Every other branch here is a regex, which stringifies whatever it is given.
  // The oklch branch calls `.trim()`, so without this guard a non-string —
  // `["oklch(1 0 0)"]` reaches `.trim` and throws `TypeError` — turned a
  // comparison into a crash. This walks untrusted computed-style values across
  // every surface, so "not a string" has to be an answer, not an exception.
  if (typeof value !== "string") return null;
  const rgb = /^rgb\((\d+),\s*(\d+),\s*(\d+)\)$/.exec(value);
  if (rgb) return [Number(rgb[1]), Number(rgb[2]), Number(rgb[3]), 1];
  const rgba = /^rgba\((\d+),\s*(\d+),\s*(\d+),\s*([\d.]+)\)$/.exec(value);
  if (rgba) {
    return [Number(rgba[1]), Number(rgba[2]), Number(rgba[3]), Number(rgba[4])];
  }
  const srgb =
    /^color\(srgb\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s*\/\s*([\d.]+)\)$/.exec(
      value,
    );
  if (srgb) {
    return [
      Number(srgb[1]) * 255,
      Number(srgb[2]) * 255,
      Number(srgb[3]) * 255,
      Number(srgb[4]),
    ];
  }
  if (/^oklch\(/i.test(value ?? "")) return parsedOklch(value.trim());
  return null;
}

/** Do these two computed values paint the same pixel?
 *
 * The channels are compared as BYTES, because that is the comparison the screen
 * makes: `rgb()` already is a byte, and everything else is quantised to one on
 * the way to the framebuffer. So resolve both sides to sRGB and round, exactly
 * as the browser does. Verified against Chrome by canvas read-back over every
 * oklch value these surfaces emit plus the notation edge cases — 19/19 round to
 * the byte Chrome paints, `oklch(0.69738 0.12513 299.414)` → 169.017/138.990/
 * 223.986 → `rgb(169, 139, 224)` among them.
 *
 * Rounding rather than a ±½-step window is deliberate. A window is symmetric
 * about the wrong thing: a design value resolving to 23.5 sits within half a
 * step of BOTH `rgb(23)` and `rgb(24)`, so it would be certified equal to the
 * one the browser does not paint — a hidden one-step difference. Rounding can
 * only err the other way (two unquantised values either side of a .5 boundary
 * report a sub-pixel difference), and an over-report is a visible row somebody
 * can dismiss. This file may never take the first error. */
function colorsMatch(left, right) {
  if (left === right) return true;
  const a = parsedColor(left);
  const b = parsedColor(right);
  if (!a || !b) return false;
  return (
    Math.round(a[0]) === Math.round(b[0]) &&
    Math.round(a[1]) === Math.round(b[1]) &&
    Math.round(a[2]) === Math.round(b[2]) &&
    Math.abs(a[3] - b[3]) <= ALPHA_TOLERANCE
  );
}

// Classify one property mismatch → { severity, note } or null (no material diff).
function classify(prop, d, l) {
  if (d === l || (COLOR.has(prop) && colorsMatch(d, l))) return null;

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
  // A numeric register is a typographic decision of the same weight as weight
  // itself: `tabular-nums` is what makes a column of figures line up, and losing
  // it is visible in every row at once.
  if (prop === "fontVariantNumeric")
    return { severity: "medium", note: `${d} → ${l}` };
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

/* --- CLI --------------------------------------------------------------------
 * Wrapped in `main()` so that IMPORTING this module is side-effect free: the
 * unit test needs `parsedColor`/`colorsMatch`/`classify` without the module
 * reading argv and writing a report. Spawning it (`node lib/compare.mjs …`,
 * which is how every run-*-parity.mjs calls it) is unchanged.
 * ------------------------------------------------------------------------- */
function main() {
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
    // An element with no border still reports a borderColor — CSS resolves it to
    // `currentColor`, so it merely restates the `color` row that is already being
    // reported. Left in, it manufactures a phantom HIGH (colour diffs are HIGH) for
    // every borderless element: 11 of Projects' 48 HIGH rows and 4 of rail-badge's 7
    // were this single artifact. Suppress it only when NEITHER side draws a border —
    // if one side does, `borderWidth`/`borderStyle` report the real difference.
    const noBorder = (s) =>
      (s.borderStyle ?? "").split(" ").every((v) => v === "none") ||
      (s.borderWidth ?? "").split(" ").every((v) => v === "0px");
    const borderColorIsNoise = noBorder(ds) && noBorder(ls);

    for (const prop of Object.keys(ds)) {
      if (!(prop in ls)) continue;
      if (prop === "borderColor" && borderColorIsNoise) continue;
      const c = classify(prop, ds[prop], ls[prop]);
      if (!c) continue;
      // A declared, property-scoped divergence is intent, not a defect — file it
      // as INFO but keep the measured delta in the detail so it stays auditable.
      // Width is intrinsically copy-dependent for inline/flex content. When the
      // two fixtures intentionally carry different runtime text, report the copy
      // and its resulting width together as INFO instead of double-counting the
      // same dynamic-data difference as a visual defect. Fixed-size/layout width
      // comparisons remain fully scored whenever the copy matches.
      const dynamicCopyWidth =
        prop === "width" &&
        d.text != null &&
        l.text != null &&
        d.text !== l.text;
      const reason =
        expect[prop] ||
        (dynamicCopyWidth
          ? "intrinsic width follows dynamic runtime copy"
          : null);
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

  writeFileSync(outPath, `${md.trimEnd()}\n`);
  writeFileSync(
    outPath.replace(/\.md$/, ".json"),
    JSON.stringify({ state, counts, findings }, null, 2),
  );
  console.log(`report: ${outPath}`);
  console.log(
    `findings: ${["high", "medium", "low", "info"].map((s) => `${s}=${counts[s] || 0}`).join(" ")}`,
  );
}

const invokedDirectly =
  process.argv[1] !== undefined &&
  pathToFileURL(resolve(process.argv[1])).href === import.meta.url;
if (invokedDirectly) main();

export { ALPHA_TOLERANCE, classify, colorsMatch, GAMUT_SLACK, parsedColor };
