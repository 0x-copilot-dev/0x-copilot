/* global React, ReactDOM, Icon, Mark, useTweaks, TweaksPanel, TweakSection, TweakRadio, TweakToggle */
/* Design source of truth: Claude Design project 73f810d9 (copilot-firstrun.jsx).
   Vendored byte-for-byte as the design-parity baseline — do not edit to match
   the app; edit the app to match this. Refresh via DesignSync get_file. */
const { useState: useFR, useEffect: useFRE, useRef: useFRRef } = React;

const FR_ADDR = "0x7f3C…a92C";
const FRS = {
  width: "1em",
  height: "1em",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.7,
  strokeLinecap: "round",
  strokeLinejoin: "round",
  viewBox: "0 0 24 24",
};
const FRClip = () => (
  <svg {...FRS}>
    <path d="M21 11.5l-8.5 8.5a5.4 5.4 0 0 1-7.6-7.6L13.5 3.8a3.6 3.6 0 0 1 5.1 5.1l-8.6 8.6a1.8 1.8 0 0 1-2.5-2.5l7.9-7.9" />
  </svg>
);
const FRKey = () => (
  <svg {...FRS}>
    <circle cx="7.5" cy="15.5" r="4.2" />
    <path d="M10.8 12.2L21 2m-3.5 1.5l3 3m-6.5.5l2.5 2.5" />
  </svg>
);

const FR_BYO = [
  {
    id: "anthropic",
    ini: "A",
    pv: "Anthropic",
    name: "Claude Sonnet 4.5",
    sub: "needs your key",
    color: "#d97757",
  },
  {
    id: "openai",
    ini: "O",
    pv: "OpenAI",
    name: "GPT-5.2",
    sub: "needs your key",
    color: "#6aa88f",
  },
  {
    id: "router",
    ini: "R",
    pv: "OpenRouter",
    name: "200+ models",
    sub: "needs your key",
    color: "#9a7fd6",
  },
];
const FR_CATALOG = [
  {
    id: "safe",
    ini: "◇",
    name: "Safe{Wallet}",
    sub: "propose & sign transactions",
  },
  {
    id: "sheets",
    ini: "S",
    name: "Google Sheets",
    sub: "read & write workbooks",
  },
  { id: "github", ini: "G", name: "GitHub", sub: "repos, issues, PRs" },
];
const FR_STARTERS = [
  {
    icon: "eye",
    t: "Watch a wallet",
    prompt:
      "Watch " +
      FR_ADDR +
      " and alert me on any transfer over $500. Keep running in the background.",
  },
  {
    icon: "doc",
    t: "Draft a launch thread",
    prompt:
      "Draft a 6-post launch thread for my project. Ask me 3 questions first, then write it.",
  },
  {
    icon: "download",
    t: "Explain a CSV",
    prompt:
      "Explain this CSV — what changed, what's weird, chart the top movers.",
    att: "airdrop-claims.csv",
  },
];

function FRKeyForm({ onDone }) {
  const [prov, setProv] = useFR("anthropic");
  const [val, setVal] = useFR("");
  const p = FR_BYO.find((x) => x.id === prov);
  return (
    <div className="fr-kf">
      <div className="prov">
        {FR_BYO.map((x) => (
          <button
            key={x.id}
            className="pv"
            data-on={prov === x.id || undefined}
            onClick={() => setProv(x.id)}
          >
            {x.pv}
            <span className="s">{x.name}</span>
          </button>
        ))}
      </div>
      <input
        type="password"
        value={val}
        placeholder="sk-…  paste your API key"
        autoFocus
        onChange={(e) => setVal(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && val.trim()) onDone(p);
        }}
      />
      <div className="row">
        <span className="knote">
          stored in your OS keychain — never uploaded
        </span>
        <button
          className="gbtn gbtn--pri"
          disabled={!val.trim()}
          style={!val.trim() ? { opacity: 0.45, cursor: "default" } : null}
          onClick={() => onDone(p)}
        >
          Connect
        </button>
      </div>
    </div>
  );
}

function FirstRunApp() {
  const [tw, setTweak] = useTweaks({
    stage: "choice",
    trial: true,
    chips: true,
  });
  const [stage, setStage] = useFR("choice");
  const [engine, setEngine] = useFR(null);
  const [pct, setPct] = useFR(0);
  const [keyOpen, setKeyOpen] = useFR(false);
  const [draft, setDraft] = useFR("");
  const [atts, setAtts] = useFR([]);
  const [open, setOpen] = useFR(null);
  const [webOn, setWebOn] = useFR(true);
  const [conn, setConn] = useFR([]);
  const [sent, setSent] = useFR(null);
  const taRef = useFRRef(null);

  useFRE(() => {
    setSent(null);
    setOpen(null);
    setKeyOpen(false);
    if (tw.stage === "choice") {
      setStage("choice");
      setEngine(null);
      setPct(0);
    } else if (tw.stage === "dl") {
      setStage("dl");
      setEngine({ kind: "local" });
      setPct((p) => (p > 0 && p < 100 ? p : 9));
    } else if (tw.stage === "local") {
      setStage("ready");
      setEngine({ kind: "local" });
      setPct(100);
    } else {
      setStage("ready");
      setEngine({ kind: "key", name: "Claude Sonnet 4.5", color: "#d97757" });
    }
  }, [tw.stage]);

  useFRE(() => {
    if (stage !== "dl") return;
    const t = setInterval(
      () => setPct((p) => Math.min(100, p + 1.1 + Math.random() * 1.6)),
      240,
    );
    return () => clearInterval(t);
  }, [stage]);
  useFRE(() => {
    if (stage === "dl" && pct >= 100) {
      setStage("ready");
      setEngine({ kind: "local" });
    }
  }, [pct, stage]);
  useFRE(() => {
    if (!sent || stage !== "ready") return;
    const t = setTimeout(() => {
      location.href = "0xCopilot App v3.html";
    }, 1500);
    return () => clearTimeout(t);
  }, [sent, stage]);

  const dl = stage === "dl";
  const model = dl
    ? { name: "Qwen 3 4B", local: true }
    : engine &&
      (engine.kind === "local"
        ? { name: "Qwen 3 4B", local: true }
        : engine.kind === "trial"
          ? { name: "Haiku starter", color: "#d97757" }
          : { name: engine.name, color: engine.color });
  const toolCount = (webOn ? 1 : 0) + conn.length;
  const grow = () => {
    const el = taRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 130) + "px";
  };
  const useStarter = (s) => {
    setDraft(s.prompt);
    setAtts(s.att ? [{ n: s.att }] : []);
    requestAnimationFrame(() => {
      grow();
      if (taRef.current) taRef.current.focus();
    });
  };
  const connect = (c) =>
    setConn((x) => (x.find((y) => y.id === c.id) ? x : [...x, c]));
  const keyDone = (p) => {
    setEngine({ kind: "key", name: p.name, color: p.color });
    setStage("ready");
    setKeyOpen(false);
    setOpen(null);
  };
  const startDl = () => {
    setEngine({ kind: "local" });
    setPct(2);
    setStage("dl");
  };
  const send = () => {
    if (!draft.trim() && !atts.length) return;
    setSent({ t: draft });
  };

  const ack = sent && (
    <div className="fr-ack" data-screen-label="First run started">
      <span
        className="spin"
        style={{ width: 22, height: 22, borderWidth: 2.5 }}
      />
      <h2>
        {dl
          ? "Queued — starts when the model lands"
          : "Starting your first run"}
      </h2>
      <div className="ln">
        {dl ? (
          <span
            className="spin"
            style={{ width: 10, height: 10, borderWidth: 1.5 }}
          />
        ) : (
          <Icon.check />
        )}{" "}
        model — {model && model.name}
        {dl
          ? " · downloading " + Math.round(pct) + "%"
          : engine && engine.kind === "local"
            ? " · on-device"
            : ""}
      </div>
      <div className="ln">
        <Icon.check /> tools — {webOn ? "web search" : "none"}
        {conn.map((c) => " · " + c.name).join("")}
      </div>
      <div className="ln">
        <Icon.check />{" "}
        {engine && engine.kind === "key"
          ? "key in your OS keychain"
          : "nothing leaves this machine"}
      </div>
    </div>
  );

  const gate = (
    <div className="fr-main" data-screen-label="Engine choice">
      <div className="fr-hero">
        <h1>First, give it a model.</h1>
        <p className="sub">The only required choice — switch anytime.</p>
      </div>
      <div className="fr-gate">
        <div className="fr-gcard">
          <span className="ic">
            <Icon.chip />
          </span>
          <b>Download the local model</b>
          <span className="meta">Qwen 3 4B · 5.6 GB · free forever</span>
          <p>Runs on this machine. Nothing you send ever leaves it.</p>
          <button className="gbtn gbtn--pri" onClick={startDl}>
            <Icon.download /> Start download
          </button>
          <span className="note">
            type your first prompt while it downloads
          </span>
        </div>
        <div className="fr-gcard">
          <span className="ic">
            <FRKey />
          </span>
          <b>Bring your own key</b>
          <span className="meta">Anthropic · OpenAI · OpenRouter</span>
          {keyOpen ? (
            <FRKeyForm onDone={keyDone} />
          ) : (
            <>
              <p>
                Frontier models, ready in ~30 seconds. Keys stay in your OS
                keychain.
              </p>
              <button className="gbtn" onClick={() => setKeyOpen(true)}>
                <FRKey /> Add a key
              </button>
            </>
          )}
        </div>
      </div>
      {tw.trial && (
        <button
          className="fr-try"
          onClick={() => {
            setEngine({ kind: "trial" });
            setStage("ready");
          }}
        >
          just exploring? hosted starter — 25 free runs, no key →
        </button>
      )}
    </div>
  );

  const main = (
    <div className="fr-main">
      <div className="fr-hero">
        <h1>What should we run first?</h1>
      </div>
      <div className="cmp">
        {atts.length > 0 && (
          <div className="cmp-att">
            {atts.map((a) => (
              <span className="cmp-chip" key={a.n}>
                <Icon.doc />
                <span className="fn">{a.n}</span>
                <button
                  onClick={() => setAtts((x) => x.filter((y) => y.n !== a.n))}
                  aria-label="Remove"
                >
                  <Icon.x />
                </button>
              </span>
            ))}
          </div>
        )}
        <textarea
          ref={taRef}
          rows={2}
          value={draft}
          placeholder={
            "Tell it what you want in plain words — “watch my wallet”, “draft the thread”…"
          }
          onChange={(e) => {
            setDraft(e.target.value);
            grow();
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              send();
            }
          }}
        ></textarea>
        <div className="cmp-row">
          <button
            className="cmp-ic"
            title="Attach files"
            data-open={open === "attach" || undefined}
            onClick={() => setOpen((o) => (o === "attach" ? null : "attach"))}
          >
            <FRClip />
          </button>
          <button
            className="cmp-pill"
            title="Choose model"
            data-open={open === "model" || undefined}
            onClick={() => setOpen((o) => (o === "model" ? null : "model"))}
          >
            {dl ? (
              <span
                className="spin"
                style={{ width: 10, height: 10, borderWidth: 1.5 }}
              />
            ) : model && model.local ? (
              <Icon.chip />
            ) : (
              <span
                className="pd"
                style={{ background: model ? model.color : "var(--mut2)" }}
              />
            )}
            <span className="lb">
              {model ? model.name : "No model"}
              {dl ? " · " + Math.round(pct) + "%" : ""}
            </span>
            <Icon.chevD />
          </button>
          <button
            className="cmp-pill"
            title="Tools"
            data-open={open === "tools" || undefined}
            onClick={() => setOpen((o) => (o === "tools" ? null : "tools"))}
          >
            <Icon.plug />
            <span className="lb">Tools</span>
            <span className="n">{toolCount}</span>
          </button>
          <span className="cmp-hint">⏎ send · ⇧⏎ line</span>
          <button
            className="cmp-send"
            disabled={!draft.trim() && !atts.length}
            onClick={send}
            title="Send"
          >
            <Icon.send />
          </button>
        </div>
      </div>
      {tw.chips && (
        <div className="fr-chips">
          {FR_STARTERS.map((s) => {
            const I = Icon[s.icon];
            return (
              <button
                key={s.t}
                className="fr-chip"
                onClick={() => useStarter(s)}
              >
                <I />
                {s.t}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );

  return (
    <div className="stage">
      <div
        className="mw"
        data-theme="dark"
        style={{ height: 720, width: 1040 }}
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
            </b>
          </div>
        </div>
        <div className="fr" data-screen-label="First run">
          <div className="fr-top">
            <span className="brand">
              <Mark size={18} />{" "}
              <span>
                <span className="zx">0x</span>Copilot
              </span>
            </span>
            <span className="sp" />
            <span className="fr-wchip">
              <span className="dot" />
              {FR_ADDR}
            </span>
            <button
              className="fr-skiplink"
              onClick={() => {
                location.href = "0xCopilot App v3.html";
              }}
            >
              skip — open the workspace →
            </button>
          </div>
          {sent ? ack : stage === "choice" ? gate : main}
          <div className="fr-foot">
            <span>v2.1.0 · local build</span>
            <span>
              {engine && engine.kind === "key"
                ? "keys in OS keychain · runs via your provider"
                : engine && engine.kind === "trial"
                  ? "hosted starter · 25 free runs"
                  : "nothing leaves this machine"}
            </span>
          </div>
        </div>
      </div>
      <TweaksPanel title="Tweaks">
        <TweakSection label="First-run" />
        <TweakRadio
          label="Stage"
          value={tw.stage}
          options={[
            { value: "choice", label: "Choice" },
            { value: "dl", label: "Downloading" },
            { value: "local", label: "Ready · local" },
            { value: "key", label: "Ready · key" },
          ]}
          onChange={(v) => setTweak("stage", v)}
        />
        <TweakToggle
          label="Hosted-trial escape hatch"
          value={tw.trial}
          onChange={(v) => setTweak("trial", v)}
        />
        <TweakToggle
          label="Suggestion chips"
          value={tw.chips}
          onChange={(v) => setTweak("chips", v)}
        />
      </TweaksPanel>
    </div>
  );
}

ReactDOM.createRoot(document.getElementById("app-root")).render(
  <FirstRunApp />,
);
