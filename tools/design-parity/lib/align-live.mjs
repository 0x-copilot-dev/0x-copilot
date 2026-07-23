/* design-parity · live-profile re-keyer (node, no deps)
 * =========================================================================
 * `lib/compare.mjs` aligns the design profile and the live profile **by
 * label**. That works when both sides were extracted with the SAME
 * `{label, selector}` list. When a surface's two sides were extracted with
 * their own native label vocabularies (design `cmp.model.pill` vs live
 * `model-pill`), this script re-keys the live profile onto the DESIGN labels
 * using `anchors.json`'s `liveLabel` field, so the comparator can do its job.
 *
 * Rules
 *   - anchor with `liveLabel` → out[anchor.label] = raw[liveLabel]
 *     (one live node may serve several design labels — e.g. the live menu is
 *      both `pop.frame` and `pop.list` — so entries are copied, not moved).
 *   - anchor with `liveLabel: null` → key OMITTED, so the comparator reports
 *     `missing-in-live` (HIGH). Deliberately no `note`: compare.mjs downgrades
 *     any noted absence to INFO, and on this surface every absence is a defect.
 *   - live labels that no anchor claims → passed through under their own key,
 *     so the comparator reports them as `extra-in-live` (INFO).
 *
 * Usage:
 *   node lib/align-live.mjs surfaces/composer/anchors.json \
 *     surfaces/composer/out/live-closed.raw.json \
 *     surfaces/composer/out/live-closed.json
 * ========================================================================= */
import { readFileSync, writeFileSync } from "node:fs";

const [anchorsPath, rawPath, outPath] = process.argv.slice(2);
if (!anchorsPath || !rawPath || !outPath) {
  console.error(
    "usage: node lib/align-live.mjs <anchors.json> <live-raw.json> <live-out.json>",
  );
  process.exit(2);
}

const anchors = JSON.parse(readFileSync(anchorsPath, "utf8"));
const raw = JSON.parse(readFileSync(rawPath, "utf8"));

const out = {};
const claimed = new Set();
const dropped = [];

for (const anchor of anchors.elements || []) {
  const liveLabel = anchor.liveLabel ?? null;
  if (liveLabel === null) {
    dropped.push(anchor.label);
    continue;
  }
  if (!(liveLabel in raw)) {
    console.error(
      `! anchor "${anchor.label}" points at live label "${liveLabel}" which is not in ${rawPath}`,
    );
    process.exitCode = 1;
    continue;
  }
  claimed.add(liveLabel);
  out[anchor.label] = raw[liveLabel];
}

const passthrough = [];
for (const [label, profile] of Object.entries(raw)) {
  if (claimed.has(label)) continue;
  if (label in out) continue;
  out[label] = profile;
  passthrough.push(label);
}

writeFileSync(outPath, JSON.stringify(out, null, 2));
console.log(
  `aligned: ${outPath} — ${Object.keys(out).length} keys ` +
    `(${claimed.size} re-keyed, ${passthrough.length} live-only passthrough, ` +
    `${dropped.length} design-only omitted: ${dropped.join(", ") || "none"})`,
);
