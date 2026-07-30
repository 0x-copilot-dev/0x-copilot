/* HARNESS — not design source. Bundle entry for the DESIGN side.
 * =========================================================================
 * Mounts exactly ONE archetype from the vendored `surface-archetypes2.jsx`
 * into the design's own surface-pane chain, driven by `?state=`:
 *
 *   ?state=board          BoardSurface, st="current"   — PRD-01, lanes at rest
 *   ?state=board-changed  BoardSurface, st="proposed"  — PRD-01, changed card
 *   ?state=no-spec        GenericSurface              — PRD-02, the honest view
 *   ?state=table          TableSurface, st="current"   — reference only
 *
 * `table` is not one of the two renders under review. It is here because the
 * design mock has NO board cap line, and `.sft-cap` — the table's "this view is
 * truncated, and says so" footer — is the only place the design states a cap.
 * `anchors/board-capped.json` measures the live board cap line against it as a
 * REGISTER comparison and says so on the tin.
 *
 * What it deliberately does NOT render: the `.mw` window chrome, the rail, the
 * topbar, the `.sl-tabs` strip, the 356px `.sl-side` pane, and the Tweaks
 * overlay. All of those are mock scaffolding around the surface; anchoring
 * inside `.sf` keeps them out of the diff (SKILL.md, "the mock's window chrome
 * is mock-only").
 *
 * What it faithfully KEEPS is the ancestor chain the surface CSS depends on:
 *   [data-theme] → .sl[data-color][data-density][data-lang] → .sl-cv → .sf
 * `.sl` is where `--sf-lab` (9.5px), `--sf-tr` (.11em) and `--sf-cx` (12px)
 * are declared, and `[data-theme="dark"] .sl[data-color="functional"]` is where
 * the panel ladder is re-declared in oklch. Drop either attribute and the lane
 * header silently measures as a 16px sans row on the wrong ground.
 */
import "./_globals.js";
import "../../../design-kit/stubs.js";
import "./surface-kit.jsx";
import "./surface-specs.jsx";
import "./surface-archetypes2.jsx";

const STATES = {
  board: { surface: "board", st: "current" },
  "board-changed": { surface: "board", st: "proposed" },
  "no-spec": { surface: "generic", st: "current" },
  table: { surface: "payouts", st: "current" },
};

const params = new URLSearchParams(globalThis.location.search);
const stateKey = params.get("state") || "board";
const config = STATES[stateKey];
if (!config) {
  throw new Error(
    `unknown ?state=${stateKey} — expected one of ${Object.keys(STATES).join(", ")}`,
  );
}

/**
 * `?color=functional` (the mock's own default) or `?color=quiet`.
 *
 * This is not a cosmetic switch and the choice changes what the report is
 * ABOUT. Functional mode is the one that carries identity — it is what turns
 * the kicker dot into a source hue and the numeric header into a tinted one —
 * but `[data-theme="dark"] .sl[data-color="functional"]` ALSO re-declares the
 * whole neutral ladder in oklch (`--panel` becomes oklch(0.212 0.010 276)
 * rather than #111114). The live app took the hues and not the ladder, so a
 * functional run reports that one systemic difference on every anchor that has
 * a ground.
 *
 * Run quiet when you want the ladder held constant and only type/spacing/border
 * measured; run functional (the default, and what the runner uses) when you
 * want identity in the picture. Quiet cannot answer the identity question at
 * all — the dot's hue rules are scoped to `[data-color="functional"]`.
 */
const colorMode = params.get("color") === "quiet" ? "quiet" : "functional";

const React = globalThis.React;
const surface = globalThis.SURFACES.find(
  (entry) => entry.id === config.surface,
);
const Renderer = globalThis.SURFACE_RENDERERS[config.surface];

function Harness() {
  React.useEffect(() => {
    // The extractor blocks on [data-parity-ready="<state>"], so this must be
    // set only once the tree is committed — not at module scope.
    document
      .getElementById("parity-frame")
      .setAttribute("data-parity-ready", stateKey);
  }, []);
  return (
    <div
      className="sl"
      data-density="default"
      data-lang="v1"
      data-color={colorMode}
    >
      <section className="sl-cv">
        <div className="sl-scroll">
          <div className="sf">
            <Renderer
              s={surface}
              st={config.st}
              gutter={true}
              preparing={false}
              merge={false}
            />
          </div>
        </div>
      </section>
    </div>
  );
}

globalThis.ReactDOM.createRoot(document.getElementById("app-root")).render(
  <Harness />,
);
