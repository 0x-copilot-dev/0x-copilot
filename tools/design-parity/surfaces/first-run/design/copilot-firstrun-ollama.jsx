/* global React, ReactDOM, Icon, Mark */
/* VENDORED from Claude Design project ceb081f6-94dd-4c36-abc1-5543ea33cd34,
 * file `copilot-firstrun-ollama.jsx` (mock "0xCopilot First Run - Ollama States").
 * Do not hand-edit — refresh via DesignSync get_file (see design-kit/REFRESH.md).
 *
 * This is the design BASELINE for PRD-P8's four runtime states. The mock renders
 * five cards side by side as a comparison catalog; `?state=` (below) renders ONE
 * card so the parity extractor can read a single state's computed styles.
 * Note the mock still ships the dropped "Download failed" (`fail`) state — it is
 * kept here verbatim because this file is a vendored copy, not our UI. */
const { useState: useOL } = React;

const FRKeyS = {
  width: "1em",
  height: "1em",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.7,
  strokeLinecap: "round",
  strokeLinejoin: "round",
  viewBox: "0 0 24 24",
};
const OLKey = () => (
  <svg {...FRKeyS}>
    <circle cx="7.5" cy="15.5" r="4.2" />
    <path d="M10.8 12.2L21 2m-3.5 1.5l3 3m-6.5.5l2.5 2.5" />
  </svg>
);

function LocalCard({ mode }) {
  const [rt, setRt] = useOL("idle");
  return (
    <div className="fr-gcard">
      <span className="ic">
        <Icon.chip />
      </span>
      <b>Download the local model</b>
      <span className="meta">Qwen 3 4B · 5.6 GB · free forever</span>
      <p>Runs on this machine. Nothing you send ever leaves it.</p>
      {mode === "pre" ? (
        <div className="fr-dep">
          <span className="dling">
            <span className="spin" /> Ollama detected — downloading now
          </span>
          <div className="ol-prog">
            <span style={{ width: "42%" }} />
          </div>
          <span className="note">
            Qwen 3 4B · 2.4 / 5.6 GB · type your first prompt while it lands
          </span>
        </div>
      ) : mode === "fail" ? (
        <div className="fr-dep">
          <span className="dling err">
            <Icon.warn /> Download failed — connection lost
          </span>
          <div className="ol-prog">
            <span className="err" style={{ width: "42%" }} />
          </div>
          <div className="acts">
            <button className="gbtn gbtn--pri">
              <Icon.download /> Resume download
            </button>
            <span className="watch">picks up at 2.4 GB — nothing lost</span>
          </div>
        </div>
      ) : mode === "stopped" ? (
        <div className="fr-dep">
          <span className="dling warn">
            <Icon.warn /> Ollama stopped responding
          </span>
          <div className="acts">
            <button className="gbtn gbtn--pri">Restart Ollama</button>
            <span className="watch">download resumes on its own</span>
          </div>
        </div>
      ) : mode === "installed" ? (
        <>
          <button className="gbtn gbtn--pri">
            <Icon.download /> Start download
          </button>
          <span className="note">
            type your first prompt while it downloads
          </span>
        </>
      ) : rt === "found" ? (
        <div className="fr-dep">
          <span className="ok">
            <Icon.check /> Ollama detected — starting your download
          </span>
        </div>
      ) : (
        <div className="fr-dep">
          <div className="acts">
            <button className="gbtn gbtn--pri" onClick={() => setRt("found")}>
              Get Ollama ↗
            </button>
            <span className="watch">
              <span className="d" /> download starts once it's detected
            </span>
          </div>
        </div>
      )}
    </div>
  );
}

/* --- harness: `?state=` renders ONE card, else the full catalog -------------
 * Parity states map to the mock's LocalCard modes:
 *   not-installed → "none" · installed → "installed" · downloading → "pre"
 *   stopped       → "stopped"
 * (`fail` is reachable via ?state=fail but is NOT one of our four — the
 * "Download failed" state was dropped; see PRD-P8 D1.) */
const OL_STATES = {
  "not-installed": "none",
  installed: "installed",
  downloading: "pre",
  stopped: "stopped",
  fail: "fail",
};

const OL_TAGS = [
  { key: "not-installed", dot: "off", label: "Ollama not installed" },
  { key: "installed", dot: "on", label: "Ollama installed" },
  { key: "downloading", dot: "on", label: "Model downloading" },
  { key: "fail", dot: "fail", label: "Download failed" },
  { key: "stopped", dot: "warn", label: "Runtime stopped" },
];

function OllamaStates() {
  const requested = new URLSearchParams(location.search).get("state");
  const single = requested && OL_STATES[requested] ? requested : null;
  const shown = single ? OL_TAGS.filter((t) => t.key === single) : OL_TAGS;
  return (
    <div className="stage">
      <div
        className="mw"
        data-theme="dark"
        style={{
          width: single ? 420 : 1160,
          height: "auto",
          maxHeight: "none",
        }}
      >
        <div className="mw-bar">
          <div className="mw-dots">
            <span className="mw-dot r" />
            <span className="mw-dot y" />
            <span className="mw-dot g" />
          </div>
          <div className="mw-title">
            <Mark size={14} />{" "}
            <b>
              <span className="zx">0x</span>Copilot
            </b>{" "}
            — local model states
          </div>
        </div>
        <div className="fr" data-screen-label="Ollama states">
          <div className="fr-top">
            <span className="brand">
              <Mark size={18} />{" "}
              <span>
                <span className="zx">0x</span>Copilot
              </span>
            </span>
            <span className="sp" />
            <span className="fr-skiplink">
              every runtime state, side by side
            </span>
          </div>
          <div className="ol-main">
            {!single && (
              <div className="fr-hero">
                <h1>One card, every state.</h1>
                <p className="sub">
                  The action adapts — runtime missing, installed, downloading,
                  and what happens when things go wrong.
                </p>
              </div>
            )}
            <div
              className="ol-grid"
              style={single ? { gridTemplateColumns: "1fr" } : null}
            >
              {shown.map((t) => (
                <div className="ol-col" key={t.key}>
                  <div className="ol-tag">
                    <span className={`d ${t.dot}`} /> {t.label}
                  </div>
                  <LocalCard mode={OL_STATES[t.key]} />
                </div>
              ))}
            </div>
          </div>
          <div className="fr-foot">
            <span>v2.1.0 · local build</span>
            <span>nothing leaves this machine</span>
          </div>
        </div>
      </div>
    </div>
  );
}

ReactDOM.createRoot(document.getElementById("app-root")).render(
  <OllamaStates />,
);
