/* design-parity · comparator color-equality tests
 * =========================================================================
 * Run: node --test tools/design-parity/lib/compare-color.test.mjs
 * (node:test, matching tools/cli and tools/desktop-runtime. NOT vitest:
 * `vitest.config.mjs` globs `lib/render-live*.test.tsx` only, and that file is
 * a merge point between every in-flight surface — this suite must not need it.)
 *
 * The property under test is NOT "does the parser accept oklch". It is:
 *
 *   the comparator says EQUAL only when the two values paint the same pixel.
 *
 * Both directions are load-bearing, and the second is the one that protects the
 * report's honesty. A comparator that cannot parse a notation over-reports —
 * annoying, visible, actionable. A comparator that hides a real difference
 * under-reports — invisible, and it silently certifies drift as parity. So
 * every "now EQUAL" case below is paired with a near-miss that must STAY
 * different, and the two colour ladders this repo actually ships are used as
 * the fixtures rather than invented ones.
 * ========================================================================= */
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { classify, colorsMatch, GAMUT_SLACK, parsedColor } from "./compare.mjs";

const COMPARE = fileURLToPath(new URL("./compare.mjs", import.meta.url));

/* ---------------------------------------------------------------------------
 * An INDEPENDENT sRGB → oklch, so "known-equal" pairs are COMPUTED here rather
 * than copied out of the implementation. It uses Ottosson's forward matrices
 * (linear sRGB → LMS → oklab); compare.mjs uses their inverses. A test that
 * reused the implementation's own numbers would pass even if both were wrong.
 * ------------------------------------------------------------------------- */
function srgbToOklch(r, g, b) {
  const linear = (c) => {
    const v = c / 255;
    return v <= 0.04045 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
  };
  const R = linear(r);
  const G = linear(g);
  const B = linear(b);
  const l = Math.cbrt(0.4122214708 * R + 0.5363325363 * G + 0.0514459929 * B);
  const m = Math.cbrt(0.2119034982 * R + 0.6806995451 * G + 0.1073969566 * B);
  const s = Math.cbrt(0.0883024619 * R + 0.2817188376 * G + 0.6299787005 * B);
  const L = 0.2104542553 * l + 0.793617785 * m - 0.0040720468 * s;
  const A = 1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s;
  const Bb = 0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s;
  const hue = (Math.atan2(Bb, A) * 180) / Math.PI;
  return { L, C: Math.hypot(A, Bb), H: hue < 0 ? hue + 360 : hue };
}
const asOklch = (r, g, b) => {
  const { L, C, H } = srgbToOklch(r, g, b);
  return `oklch(${L} ${C} ${H})`;
};

/** Largest per-channel gap, in 0–255 units. Reported in assertion messages so a
 * failure says HOW FAR apart the two colours are, not just that they differ. */
function maxChannelGap(left, right) {
  const a = parsedColor(left);
  const b = parsedColor(right);
  if (!a || !b) return Number.POSITIVE_INFINITY;
  return Math.max(
    Math.abs(a[0] - b[0]),
    Math.abs(a[1] - b[1]),
    Math.abs(a[2] - b[2]),
  );
}

/* === DIRECTION 1 — the same colour, in two notations, now compares EQUAL === */

test("oklch computed from one of our own tokens equals that token's rgb", () => {
  // Every value is a token this repo ships (packages/design-system/styles.css),
  // converted to oklch by the independent inverse above. Same colour, other
  // notation: the comparator must not manufacture a finding out of that.
  for (const [name, rgb] of [
    ["--color-surface #111114", [17, 17, 20]],
    ["--color-surface-muted #16161a", [22, 22, 26]],
    ["--color-surface-elevated #1d1d23", [29, 29, 35]],
    ["--sky #5fb2ec", [95, 178, 236]],
    ["--jade #57c785", [87, 199, 133]],
    ["white", [255, 255, 255]],
    ["black", [0, 0, 0]],
  ]) {
    const live = `rgb(${rgb[0]}, ${rgb[1]}, ${rgb[2]})`;
    const design = asOklch(...rgb);
    assert.ok(
      colorsMatch(design, live),
      `${name}: ${design} should equal ${live} (gap ${maxChannelGap(design, live).toFixed(4)})`,
    );
  }
});

test("every oklch the design surfaces emit resolves to the pixel CHROME paints", () => {
  // GROUND TRUTH, not a round trip. Each right-hand value was measured by
  // painting the left-hand colour on a 1×1 canvas in the same headless Chromium
  // the extractor drives and reading the pixel back:
  //
  //   ctx.fillStyle = <oklch>; ctx.fillRect(0,0,1,1);
  //   ctx.getImageData(0,0,1,1).data
  //
  // The helper above and the implementation are inverses of each other and
  // could in principle agree while both drifted from the browser. These cannot.
  // The first five are every distinct oklch value in
  // surfaces/surface-language/out/design-*.json.
  for (const [design, painted] of [
    ["oklch(0.188 0.009 276)", "rgb(18, 19, 23)"],
    ["oklch(0.212 0.01 276)", "rgb(23, 24, 29)"],
    ["oklch(0.243 0.011 276)", "rgb(30, 32, 37)"],
    ["oklch(0.69738 0.12513 299.414)", "rgb(169, 139, 224)"],
    ["oklch(0.76 0.1 288)", "rgb(174, 167, 237)"],
    ["oklch(0.5 0.09 180)", "rgb(3, 116, 101)"],
    ["oklch(1 0 0)", "rgb(255, 255, 255)"],
    ["oklch(0 0 0)", "rgb(0, 0, 0)"],
  ]) {
    const mine = parsedColor(design).slice(0, 3).map(Math.round);
    assert.equal(
      `rgb(${mine.join(", ")})`,
      painted,
      `${design} must resolve to the pixel Chrome paints`,
    );
    assert.ok(colorsMatch(design, painted));
  }
});

test("the design page's own serialized oklch equals the rgb it was authored as", () => {
  // Taken verbatim from surfaces/surface-language/out/design-board.json. The
  // SAME element carries the violet as `rgb(169, 139, 224)` on backgroundColor
  // and as `oklch(0.69738 0.12513 299.414)` inside its box-shadow — Chrome
  // serializes the one colour two ways depending on how it reached the
  // property. This is the pair byte-rounding exists for: the oklch form was
  // rounded to 5 decimals by the serializer, which lands 0.017/255 away — the
  // same painted pixel, reachable only if the comparator quantises like the
  // browser instead of demanding float equality.
  const design = "oklch(0.69738 0.12513 299.414)";
  const live = "rgb(169, 139, 224)";
  const gap = maxChannelGap(design, live);
  assert.ok(gap > 0, "the two notations are not byte-identical floats");
  assert.ok(
    gap < 0.02,
    `expected serializer-rounding noise only, got ${gap.toFixed(4)}`,
  );
  assert.ok(colorsMatch(design, live), `${design} should equal ${live}`);
  // …and it would NOT have matched under the old 0.01 bound, which is why that
  // bound had to go: it was finer than the resolution of `rgb()` itself.
  assert.ok(gap > 0.01);
});

test("alpha survives every spelling the slash syntax allows", () => {
  assert.ok(colorsMatch("oklch(1 0 0 / 0.07)", "rgba(255, 255, 255, 0.07)"));
  assert.ok(colorsMatch("oklch(1 0 0 / 7%)", "rgba(255, 255, 255, 0.07)"));
  assert.ok(colorsMatch("oklch(1 0 0/0.07)", "rgba(255, 255, 255, 0.07)"));
  // No slash at all ⇒ opaque.
  assert.ok(colorsMatch("oklch(1 0 0)", "rgb(255, 255, 255)"));
  // CSS Color 4 §4.4: a `none` component renders as 0 — here, fully clear.
  assert.ok(colorsMatch("oklch(1 0 0 / none)", "rgba(255, 255, 255, 0)"));
  assert.ok(colorsMatch("oklch(none none none)", "rgb(0, 0, 0)"));
});

test("percentages and every angle unit name the same colour as the bare numbers", () => {
  const canonical = "oklch(0.5 0.09 180)";
  for (const equivalent of [
    "oklch(50% 22.5% 180)", // L 100% = 1, C 100% = 0.4
    "oklch(0.5 0.09 180deg)",
    "oklch(0.5 0.09 0.5turn)",
    "oklch(0.5 0.09 200grad)",
    "oklch(0.5 0.09 3.14159265358979rad)",
  ]) {
    assert.ok(
      colorsMatch(canonical, equivalent),
      `${equivalent} should equal ${canonical}`,
    );
  }
});

/* === DIRECTION 2 — a real difference must SURVIVE the fix ================== */

test("the design page's lighter neutral ladder keeps reporting against ours", () => {
  // surface-lang.css declares its own `--panel`/`--panel2`/`--ink2`, and they
  // are genuinely lighter than the tokens the app ships. Being able to READ
  // oklch must not turn that into parity. These are the rows the parity report
  // is for; if this test ever goes green-by-matching, the tool has started
  // lying.
  for (const [design, live, ours] of [
    ["oklch(0.212 0.01 276)", "rgb(17, 17, 20)", "--panel vs --color-surface"],
    [
      "oklch(0.243 0.011 276)",
      "rgb(22, 22, 26)",
      "--panel2 vs --color-surface-muted",
    ],
    ["oklch(0.188 0.009 276)", "rgb(13, 13, 16)", "--ink2 vs app ink"],
    ["rgb(169, 139, 224)", "oklch(0.76 0.1 288)", "design violet vs live dot"],
  ]) {
    const gap = maxChannelGap(design, live);
    assert.ok(
      !colorsMatch(design, live),
      `${ours}: ${design} vs ${live} must stay a finding`,
    );
    assert.ok(
      gap > 1,
      `${ours}: expected a visible gap, measured ${gap.toFixed(3)}/255`,
    );
  }
});

test("identical hue with a different alpha is still a difference", () => {
  // The trap this fix could have sprung. Once oklch resolves, `oklch(1 0 0)`
  // and `rgba(255,255,255,…)` agree on all three channels — the ONLY thing left
  // holding these rows open is alpha. The design's hairlines are 0.07/0.115;
  // ours are 0.06/0.10.
  assert.ok(!colorsMatch("oklch(1 0 0 / 0.07)", "rgba(255, 255, 255, 0.06)"));
  assert.ok(!colorsMatch("oklch(1 0 0 / 0.115)", "rgba(255, 255, 255, 0.1)"));
  // …while the same alpha does match, so the channels really did resolve.
  assert.ok(colorsMatch("oklch(1 0 0 / 0.06)", "rgba(255, 255, 255, 0.06)"));
});

test("two adjacent 8-bit colours can never be merged", () => {
  // Channels compare as BYTES, so distinct bytes are distinct colours —
  // whatever notation the two sides arrive in.
  assert.ok(!colorsMatch("rgb(17, 17, 20)", "rgb(18, 17, 20)"));
  assert.ok(!colorsMatch("rgb(17, 17, 20)", "rgb(17, 17, 21)"));
  assert.ok(!colorsMatch(asOklch(17, 17, 20), "rgb(18, 17, 20)"));
  assert.ok(!colorsMatch(asOklch(255, 255, 255), "rgb(255, 255, 254)"));
});

test("a value on a byte boundary matches only the byte the browser paints", () => {
  // THE REGRESSION GUARD for choosing rounding over a ±½-step window.
  //
  // A window is symmetric about the wrong thing. `color(srgb 0.5 …)` resolves
  // to exactly 127.5 — half a step from `rgb(127)` AND half a step from
  // `rgb(128)` — so a ±0.5 comparator called it equal to BOTH, including the
  // one the browser does not paint. That is a hidden one-step difference, the
  // exact failure this file forbids. Rounding picks the painted byte and only
  // that. (0.5 is chosen because it is exact in binary floating point, so the
  // fixture really does sit ON the boundary rather than near it.)
  const half = "color(srgb 0.5 0.5 0.5 / 1)";
  assert.deepEqual(parsedColor(half).slice(0, 3), [127.5, 127.5, 127.5]);
  // Chrome paints rgb(128, 128, 128) for this — measured by canvas read-back,
  // half rounding away from zero, which is what Math.round does too.
  assert.ok(
    colorsMatch(half, "rgb(128, 128, 128)"),
    "128 is what gets painted",
  );
  assert.ok(
    !colorsMatch(half, "rgb(127, 127, 127)"),
    "127 is a real difference",
  );
});

/* === OUT OF GAMUT — refuse, never guess =================================== */

test("an out-of-sRGB oklch is refused, not clamped and not NaN", () => {
  for (const wild of [
    "oklch(0.5 0.4 30)", // raw red 253.2, green −112.6, blue −63.0
    "oklch(0.7 0.4 250)",
    "oklch(0.9 0.3 140)",
    "oklch(1 0.004 90)", // 256.07 — just past the top wall
    "oklch(0.05 0.08 90)", // −1.07 — just past the bottom wall
  ]) {
    assert.equal(parsedColor(wild), null, `${wild} is outside sRGB`);
    // Refusing sends colorsMatch back to exact string equality, so the row
    // keeps reporting rather than being certified equal to a guess.
    assert.ok(!colorsMatch(wild, "rgb(255, 0, 0)"));
    assert.ok(colorsMatch(wild, wild), "identical strings still agree");
  }
});

test("clamping would have LIED about an out-of-gamut colour, so we refuse", () => {
  // The real reason clamping is unacceptable, measured against Chrome by canvas
  // read-back rather than reasoned about. Chrome gamut-maps (CSS Color 4 §13
  // chroma reduction) and lands somewhere a clamp does not:
  //
  //   oklch(0.5 0.4 30)  → rgb(253, 0, 0)     clamp would say rgb(255, 0, 0)
  //   oklch(0.7 0.4 250) → rgb(0, 133, 255)   clamp would say rgb(0, 0, 255)
  //   oklch(0.9 0.3 140) → rgb(75, 255, 0)    clamp would say rgb(0, 255, 0)
  //
  // So a clamping comparator would certify the first as EQUAL to a live
  // rgb(255, 0, 0) two whole steps from the painted pixel — an UNDER-report.
  // Refusing keeps the row.
  assert.equal(parsedColor("oklch(0.5 0.4 30)"), null);
  assert.ok(!colorsMatch("oklch(0.5 0.4 30)", "rgb(255, 0, 0)"));
  assert.ok(!colorsMatch("oklch(0.5 0.4 30)", "rgb(253, 0, 0)"));

  // And the price we pay for refusing, stated honestly rather than hidden:
  // Chrome maps BOTH of these to rgb(255, 0, 0), so they do paint the same
  // pixel and we still report them as different. That is an over-report — the
  // safe direction, and no surface in this harness emits an out-of-gamut value.
  assert.ok(!colorsMatch("oklch(0.5 0.5 30)", "oklch(0.6 0.4 30)"));
});

test("a colour less than half a step outside the cube is rounding, not a new colour", () => {
  // Authoring/serialization rounding routinely pushes a channel a hair past the
  // wall. Within half an 8-bit step there is no other colour it could be, so it
  // clamps and stays comparable — the slack is exactly GAMUT_SLACK wide, never
  // wider. Both right-hand sides are the pixel Chrome measurably paints.
  assert.equal(GAMUT_SLACK, 0.5);
  assert.ok(colorsMatch("oklch(1 0.001 90)", "rgb(255, 255, 254)")); // raw 255.27 / 255.00 / 254.27
  assert.ok(colorsMatch("oklch(0.05 0.02 90)", "rgb(1, 0, 0)")); // raw 0.90 / 0.31 / −0.19
});

/* === TOTALITY — never throw, never NaN ==================================== */

test("malformed or unknown colour syntax returns null instead of throwing", () => {
  for (const junk of [
    "oklch(0.5 0.1)", // too few components
    "oklch(0.5 0.1 30 / 0.5 0.2)", // too many
    "oklch(bogus 0 0)",
    "oklch(0.5 0.1 30deg2)",
    "oklch()",
    "oklch(0.5 0.1 30", // unterminated
    "lab(50% 40 59.5)", // a space this file deliberately does not implement
    "not-a-color",
    "",
    undefined,
    null,
  ]) {
    assert.equal(parsedColor(junk), null, `${String(junk)} should not parse`);
    assert.equal(colorsMatch(junk, "rgb(0, 0, 0)"), false);
  }
  // Every finite input yields finite channels — no NaN reaches a comparison.
  for (const value of ["oklch(0 0 0)", "oklch(1 0 0)", "oklch(1.4 -0.2 720)"]) {
    for (const channel of parsedColor(value))
      assert.ok(Number.isFinite(channel));
  }
});

test("the rgb / rgba / color(srgb) forms behave exactly as before", () => {
  assert.ok(colorsMatch("rgb(95, 178, 236)", "rgb(95, 178, 236)"));
  assert.ok(
    colorsMatch(
      "rgba(95, 178, 236, 0.35)",
      "color(srgb 0.372549 0.698039 0.92549 / 0.35)",
    ),
  );
  assert.ok(!colorsMatch("rgb(95, 178, 236)", "rgb(87, 199, 133)"));
  assert.ok(!colorsMatch("rgba(0, 0, 0, 0)", "rgb(0, 0, 0)"));
});

test("a multi-value borderColor list is left unparsed, so it still reports", () => {
  // KNOWN, DELIBERATE LIMITATION. A four-side `borderColor` is a LIST of
  // colours; this comparator resolves one colour at a time. Rather than guess,
  // the whole string falls through to exact equality — which over-reports
  // (a list whose four sides are merely spelled differently still shows up) and
  // never under-reports. Pinned here so the behaviour is a decision, not a
  // surprise.
  const design = "rgb(236, 236, 241) rgb(236, 236, 241) oklch(1 0 0 / 0.115)";
  assert.equal(parsedColor(design), null);
  assert.ok(!colorsMatch(design, design.replace("oklch(1 0 0 / 0.115)", "x")));
});

/* === THE REPORT — the deliverable, not the internals ====================== */

test("classify() files the equal pair as no finding and the lighter panel as HIGH", () => {
  assert.equal(
    classify("backgroundColor", asOklch(17, 17, 20), "rgb(17, 17, 20)"),
    null,
  );
  const finding = classify(
    "backgroundColor",
    "oklch(0.212 0.01 276)",
    "rgb(17, 17, 20)",
  );
  assert.equal(finding?.severity, "high");
  assert.match(finding.note, /--panel/); // still token-annotated
});

test("end to end: the emitted report keeps the real row and drops the notation-only one", () => {
  // The report is the product. Everything above tests the ruler; this spawns
  // the actual CLI the parity runners spawn and asserts what lands in the JSON.
  const dir = mkdtempSync(join(tmpdir(), "design-parity-color-"));
  const profile = (values) => {
    const out = {};
    for (const [label, backgroundColor] of Object.entries(values)) {
      out[label] = { matched: true, tag: "div", styles: { backgroundColor } };
    }
    return out;
  };
  const design = join(dir, "design.json");
  const live = join(dir, "live.json");
  const report = join(dir, "report.md");
  writeFileSync(
    design,
    JSON.stringify(
      profile({
        "same-colour-other-notation": asOklch(17, 17, 20),
        "design-panel-is-lighter": "oklch(0.212 0.01 276)",
        "same-white-thinner-line": "oklch(1 0 0 / 0.07)",
      }),
    ),
  );
  writeFileSync(
    live,
    JSON.stringify(
      profile({
        "same-colour-other-notation": "rgb(17, 17, 20)",
        "design-panel-is-lighter": "rgb(17, 17, 20)",
        "same-white-thinner-line": "rgba(255, 255, 255, 0.06)",
      }),
    ),
  );
  execFileSync(process.execPath, [COMPARE, design, live, "--out", report], {
    stdio: "pipe",
  });
  const emitted = JSON.parse(readFileSync(report.replace(/\.md$/, ".json")));
  assert.deepEqual(
    emitted.findings.map((f) => `${f.label}:${f.severity}`).sort(),
    ["design-panel-is-lighter:high", "same-white-thinner-line:high"],
  );
  assert.equal(emitted.counts.high, 2);
});
