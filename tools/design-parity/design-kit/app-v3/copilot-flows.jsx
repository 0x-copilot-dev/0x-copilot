/* global React, ReactDOM, Icon, PROVIDERS, LOCAL_AVAILABLE, CONNECTOR_CATALOG */
const { useState: useFl, useEffect: useFlE, useRef: useFlRef } = React;
const { Toggle, Field, SetCard, SecHead } = window;

const MODELS = {
  anthropic: ["Claude Opus 4.1", "Claude Sonnet 4.5", "Claude Haiku 4"],
  openai: ["GPT-5", "GPT-5 mini", "o4"],
  google: ["Gemini 2.5 Pro", "Gemini 2.5 Flash"],
  groq: ["Llama 3.3 70B", "Mixtral 8x22B"],
  xai: ["Grok 4", "Grok 4 mini"],
  openrouter: ["Auto (router)", "Pick per-run"],
};

function FlowModal({ onClose, children }) {
  useFlE(() => {
    const onKey = (e) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  const mount = document.querySelector(".mw") || document.body;
  return ReactDOM.createPortal(
    <div className="scrim" onMouseDown={onClose}>
      <div className="modal" onMouseDown={(e) => e.stopPropagation()}>
        {children}
      </div>
    </div>,
    mount,
  );
}

function StepDots({ n, i }) {
  return (
    <div className="steps-dot">
      {Array.from({ length: n }).map((_, k) => (
        <i key={k} data-on={k <= i || undefined} />
      ))}
    </div>
  );
}

/* ============================ BYOK: add a key =========================== */
function AddKeyModal({ preset, onClose, onAdd }) {
  const [prov, setProv] = useFl(preset || null);
  const [keyval, setKeyval] = useFl("");
  const [phase, setPhase] = useFl("enter"); // enter · validating · choose
  const [model, setModel] = useFl(null);
  const empties = PROVIDERS.filter((p) => p.status === "empty");
  const P = PROVIDERS.find((p) => p.id === prov);

  const validate = () => {
    setPhase("validating");
    setTimeout(() => {
      setPhase("choose");
      setModel(MODELS[prov][0]);
    }, 1100);
  };
  const stepIdx = phase === "enter" ? 0 : phase === "validating" ? 1 : 2;

  return (
    <FlowModal onClose={onClose}>
      <div className="modal__head">
        <div
          className="modal__logo"
          style={{ background: P ? P.color : "var(--panel3)" }}
        >
          {P ? P.ini : <Icon.key />}
        </div>
        <div className="modal__title">
          <h3>{P ? `Connect ${P.name}` : "Add a provider key"}</h3>
          <p>bring your own key · stored in keychain</p>
        </div>
        <button className="modal__x" onClick={onClose}>
          <Icon.x />
        </button>
      </div>
      <div className="modal__body">
        {!prov && (
          <>
            <div className="flabel">Choose a provider</div>
            {empties.map((p) => (
              <button key={p.id} className="mrow" onClick={() => setProv(p.id)}>
                <span className="mrow__logo" style={{ background: p.color }}>
                  {p.ini}
                </span>
                <span className="mrow__main">
                  <span className="mrow__name">{p.name}</span>
                  <span className="mrow__sub">
                    {MODELS[p.id].length} models
                  </span>
                </span>
                <Icon.chevR />
              </button>
            ))}
          </>
        )}
        {prov && phase !== "choose" && (
          <>
            <div className="flabel">
              <Icon.key /> {P.name} API key
            </div>
            <input
              className="cin mono"
              autoFocus
              placeholder="sk-…"
              value={keyval}
              onChange={(e) => setKeyval(e.target.value)}
              disabled={phase === "validating"}
            />
            <div className={"fhint" + (phase === "validating" ? "" : "")}>
              {phase === "validating" ? (
                <span
                  style={{
                    display: "inline-flex",
                    gap: 8,
                    alignItems: "center",
                  }}
                >
                  <span className="spin" /> Validating with {P.name}…
                </span>
              ) : (
                <>
                  Paste a key from your {P.name} dashboard. It's sent only to{" "}
                  {P.name} to verify, then stored in your macOS Keychain.
                </>
              )}
            </div>
          </>
        )}
        {prov && phase === "choose" && (
          <>
            <div
              className="fhint ok"
              style={{
                marginTop: 0,
                marginBottom: 14,
                display: "flex",
                gap: 8,
                alignItems: "center",
              }}
            >
              <Icon.check /> Key verified ·{" "}
              {keyval ? keyval.slice(0, 6) : "sk-••••"}••••
            </div>
            <div className="flabel">
              Set the default model for this provider
            </div>
            {MODELS[prov].map((m) => (
              <button
                key={m}
                className="mrow"
                data-on={model === m || undefined}
                onClick={() => setModel(m)}
              >
                <span className="mrow__main">
                  <span className="mrow__name">{m}</span>
                </span>
                <span className="mrow__chk">
                  {model === m && <Icon.check />}
                </span>
              </button>
            ))}
          </>
        )}
      </div>
      <div className="modal__foot">
        <StepDots n={3} i={stepIdx} />
        <span className="sp" />
        <button className="cbtn cbtn--ghost" onClick={onClose}>
          Cancel
        </button>
        {phase !== "choose" ? (
          <button
            className="cbtn cbtn--pri"
            disabled={!prov || !keyval || phase === "validating"}
            onClick={validate}
          >
            Validate key
          </button>
        ) : (
          <button
            className="cbtn cbtn--pri"
            disabled={!model}
            onClick={() => {
              onAdd(prov, keyval || "sk-••••", model);
              onClose();
            }}
          >
            Add key
          </button>
        )}
      </div>
    </FlowModal>
  );
}

/* ========================= Local model download ======================== */
function LocalModelModal({ onClose, onAdd }) {
  const [pick, setPick] = useFl(null);
  const [phase, setPhase] = useFl("pick"); // pick · downloading · ready
  const [pct, setPct] = useFl(0);
  const [asDefault, setAsDefault] = useFl(true);
  const timer = useFlRef(null);
  const M = LOCAL_AVAILABLE.find((m) => m.id === pick);

  const start = (m) => {
    setPick(m.id);
    setPhase("downloading");
    setPct(0);
    timer.current = setInterval(
      () =>
        setPct((p) => {
          if (p >= 100) {
            clearInterval(timer.current);
            setPhase("ready");
            return 100;
          }
          return p + 4;
        }),
      90,
    );
  };
  useFlE(() => () => clearInterval(timer.current), []);

  return (
    <FlowModal onClose={onClose}>
      <div className="modal__head">
        <div
          className="modal__logo"
          style={{ background: "#201f2a", color: "var(--jade)" }}
        >
          <Icon.chip />
        </div>
        <div className="modal__title">
          <h3>Download a local model</h3>
          <p>runs on your machine · no key, no network</p>
        </div>
        <button className="modal__x" onClick={onClose}>
          <Icon.x />
        </button>
      </div>
      <div className="modal__body">
        {phase === "pick" &&
          LOCAL_AVAILABLE.map((m) => (
            <button key={m.id} className="mrow" onClick={() => start(m)}>
              <span
                className="mrow__logo"
                style={{ background: "#201f2a", color: "var(--jade)" }}
              >
                <Icon.chip />
              </span>
              <span className="mrow__main">
                <span className="mrow__name">
                  {m.name} · {m.param}
                </span>
                <span className="mrow__sub">
                  {m.size} · {m.note}
                </span>
              </span>
              <Icon.download />
            </button>
          ))}
        {phase !== "pick" && M && (
          <div>
            <div
              className="krow"
              style={{ borderTop: 0, padding: 0, marginBottom: 14 }}
            >
              <span
                className="krow__logo"
                style={{ background: "#201f2a", color: "var(--jade)" }}
              >
                <Icon.chip />
              </span>
              <span className="krow__main">
                <span className="krow__name">
                  {M.name} · {M.param}
                </span>
                <span className="krow__sub">
                  {M.size} · {M.note}
                </span>
              </span>
            </div>
            {phase === "downloading" ? (
              <>
                <div
                  className="fhint"
                  style={{
                    marginTop: 0,
                    display: "flex",
                    justifyContent: "space-between",
                  }}
                >
                  <span>Downloading…</span>
                  <span className="mono">{pct}%</span>
                </div>
                <div className="bar">
                  <div className="bar__f" style={{ width: pct + "%" }} />
                </div>
              </>
            ) : (
              <>
                <div
                  className="fhint ok"
                  style={{ display: "flex", gap: 8, alignItems: "center" }}
                >
                  <Icon.check /> Ready to run locally.
                </div>
                <label
                  className="mrow"
                  data-on={asDefault || undefined}
                  onClick={() => setAsDefault((v) => !v)}
                  style={{ marginTop: 14 }}
                >
                  <span className="mrow__main">
                    <span className="mrow__name">
                      Use as my default local model
                    </span>
                    <span className="mrow__sub">
                      picked when you choose "Local" in Model &amp; behavior
                    </span>
                  </span>
                  <span className="mrow__chk">
                    {asDefault && <Icon.check />}
                  </span>
                </label>
              </>
            )}
          </div>
        )}
      </div>
      <div className="modal__foot">
        <span className="sp" />
        <button className="cbtn cbtn--ghost" onClick={onClose}>
          {phase === "ready" ? "Cancel" : "Close"}
        </button>
        {phase === "ready" && (
          <button
            className="cbtn cbtn--pri"
            onClick={() => {
              onAdd(M, asDefault);
              onClose();
            }}
          >
            Finish
          </button>
        )}
      </div>
    </FlowModal>
  );
}

/* ======================= Connect a connector =========================== */
const MCP_TMPL = `{
  "mcpServers": {
    "grafana": {
      "command": "npx",
      "args": ["-y", "mcp-grafana"],
      "env": { "GRAFANA_API_KEY": "glsa_…" }
    }
  }
}`;
function parseMCP(text) {
  let o;
  try {
    o = JSON.parse(text);
  } catch (e) {
    return { err: String(e.message).split(" at ")[0] };
  }
  if (!o || typeof o !== "object" || Array.isArray(o))
    return { err: "Expected a JSON object." };
  let entries =
    o.mcpServers && typeof o.mcpServers === "object"
      ? Object.entries(o.mcpServers)
      : o.command || o.url
        ? [["custom", o]]
        : Object.entries(o);
  entries = entries.filter(
    ([, v]) => v && typeof v === "object" && !Array.isArray(v),
  );
  if (!entries.length)
    return {
      err: "No server found — expected mcpServers.<name> with a command or url.",
    };
  const [name, cfg] = entries[0];
  if (!cfg.command && !cfg.url)
    return { err: `“${name}” needs a "command" (stdio) or "url" (remote).` };
  const transport = cfg.url
    ? /sse/i.test(cfg.type || cfg.url)
      ? "sse"
      : "http"
    : "stdio";
  const endpoint = cfg.command
    ? [cfg.command, ...(cfg.args || [])].join(" ")
    : cfg.url;
  return {
    name,
    transport,
    endpoint,
    env: cfg.env ? Object.keys(cfg.env).length : 0,
    count: entries.length,
  };
}

function ConnectModal({ onClose, onAdd }) {
  const [pick, setPick] = useFl(null);
  const [phase, setPhase] = useFl("pick"); // pick · auth · perm · json
  const [perm, setPerm] = useFl("act");
  const [json, setJson] = useFl(MCP_TMPL);
  const [custom, setCustom] = useFl(null);
  const C = custom || CONNECTOR_CATALOG.find((c) => c.id === pick);
  const P = phase === "json" ? parseMCP(json) : null;

  const choose = (c) => {
    setPick(c.id);
    setPhase("auth");
    setTimeout(() => setPhase("perm"), 1200);
  };
  const addCustom = () => {
    setCustom({
      id: "mcp-" + P.name.toLowerCase().replace(/[^a-z0-9]+/g, "-"),
      name: P.name,
      color: "#7c6df2",
      ini: P.name[0].toUpperCase(),
      sub:
        P.transport === "stdio"
          ? "local MCP · stdio"
          : "remote MCP · " + P.transport,
      custom: true,
    });
    setPhase("perm");
  };

  return (
    <FlowModal onClose={onClose}>
      <div className="modal__head">
        <div
          className="modal__logo"
          style={{ background: C ? C.color : "var(--panel3)" }}
        >
          {C ? C.ini : <Icon.plug />}
        </div>
        <div className="modal__title">
          <h3>
            {C
              ? `Connect ${C.name}`
              : phase === "json"
                ? "Add MCP server"
                : "Connect a tool"}
          </h3>
          <p>
            {C
              ? C.sub
              : phase === "json"
                ? "paste a server config as JSON"
                : "the agent acts through your accounts"}
          </p>
        </div>
        <button className="modal__x" onClick={onClose}>
          <Icon.x />
        </button>
      </div>
      <div className="modal__body">
        {phase === "pick" && (
          <>
            {CONNECTOR_CATALOG.map((c) => (
              <button key={c.id} className="mrow" onClick={() => choose(c)}>
                <span className="mrow__logo" style={{ background: c.color }}>
                  {c.ini}
                </span>
                <span className="mrow__main">
                  <span className="mrow__name">{c.name}</span>
                  <span className="mrow__sub">{c.sub}</span>
                </span>
                <Icon.chevR />
              </button>
            ))}
            <button
              className="mrow mrow--dash mrow--pin"
              onClick={() => setPhase("json")}
            >
              <span
                className="mrow__logo"
                style={{ fontFamily: "var(--mono)" }}
              >
                {"{\u2009}"}
              </span>
              <span className="mrow__main">
                <span className="mrow__name">Custom MCP server</span>
                <span className="mrow__sub">
                  paste a JSON config — stdio or remote
                </span>
              </span>
              <Icon.chevR />
            </button>
          </>
        )}
        {phase === "json" && (
          <>
            <div className="flabel">
              Server config{" "}
              <span style={{ fontWeight: 400, color: "var(--mut2)" }}>
                — same shape as claude_desktop_config.json
              </span>
            </div>
            <textarea
              className="jed-ta"
              spellCheck={false}
              value={json}
              onChange={(e) => setJson(e.target.value)}
            />
            {P.err ? (
              <div className="fhint err">{P.err}</div>
            ) : (
              <div className="jed-st">
                <span className="chip chip--ok">
                  <Icon.check /> {P.name}
                </span>
                <span className="chip">{P.transport}</span>
                <span className="chip" title={P.endpoint}>
                  {P.endpoint.length > 34
                    ? P.endpoint.slice(0, 34) + "…"
                    : P.endpoint}
                </span>
                {P.env > 0 && (
                  <span className="chip">
                    {P.env} env var{P.env > 1 ? "s" : ""}
                  </span>
                )}
                {P.count > 1 && (
                  <span className="chip chip--warn">
                    +{P.count - 1} more — added one at a time
                  </span>
                )}
              </div>
            )}
          </>
        )}
        {phase === "auth" && (
          <div className="empty" style={{ padding: "40px 10px" }}>
            <span
              className="spin"
              style={{ width: 26, height: 26, borderWidth: 3 }}
            />
            <h3 style={{ marginTop: 16 }}>Authorizing with {C.name}…</h3>
            <p>Approve the connection in the window that just opened.</p>
          </div>
        )}
        {phase === "perm" && (
          <>
            <div
              className="fhint ok"
              style={{
                marginTop: 0,
                marginBottom: 14,
                display: "flex",
                gap: 8,
                alignItems: "center",
              }}
            >
              <Icon.check />{" "}
              {C.custom
                ? "Config valid — the server starts on first use."
                : "Authorized as your account."}
            </div>
            <div className="flabel">What can the agent do with {C.name}?</div>
            {[
              ["read", "Read only", "See data — never change anything"],
              [
                "act",
                "Read & act",
                "Take actions, pausing for approval per your policy",
              ],
            ].map(([id, n, d]) => (
              <button
                key={id}
                className="mrow"
                data-on={perm === id || undefined}
                onClick={() => setPerm(id)}
              >
                <span className="mrow__main">
                  <span className="mrow__name">{n}</span>
                  <span
                    className="mrow__sub"
                    style={{ fontFamily: "var(--body)" }}
                  >
                    {d}
                  </span>
                </span>
                <span className="mrow__chk">
                  {perm === id && <Icon.check />}
                </span>
              </button>
            ))}
          </>
        )}
      </div>
      <div className="modal__foot">
        {phase === "json" && (
          <button className="cbtn cbtn--ghost" onClick={() => setPhase("pick")}>
            ← Catalog
          </button>
        )}
        <span className="sp" />
        <button className="cbtn cbtn--ghost" onClick={onClose}>
          Cancel
        </button>
        {phase === "perm" && (
          <button
            className="cbtn cbtn--pri"
            onClick={() => {
              onAdd(C, perm);
              onClose();
            }}
          >
            Connect
          </button>
        )}
        {phase === "json" && (
          <button
            className="cbtn cbtn--pri"
            disabled={!!P.err}
            onClick={addCustom}
          >
            Add server
          </button>
        )}
      </div>
    </FlowModal>
  );
}

/* ============================== sections =============================== */
function ProviderKeysSection({ providers, addProvider, set }) {
  const [modal, setModal] = useFl(null); // null | {preset}
  const connected = providers.filter((p) => p.status === "connected");
  const empty = providers.filter((p) => p.status === "empty");
  return (
    <div className="set-sec">
      <SecHead
        title="Provider keys"
        desc="Bring your own key. 0xCopilot talks to each provider directly — the key stays on your machine."
      />
      <SetCard
        title="Connected"
        meta={`${connected.length} active`}
        note={
          <span>
            Keys are stored in your <b>macOS Keychain</b>, encrypted at rest,
            and never sent to a 0xCopilot server.
          </span>
        }
      >
        {connected.map((p) => (
          <div className="krow" key={p.id}>
            <span className="krow__logo" style={{ background: p.color }}>
              {p.ini}
            </span>
            <div className="krow__main">
              <div className="krow__name">
                {p.name}{" "}
                <span className="chip chip--ok" style={{ padding: "1px 8px" }}>
                  {p.model}
                </span>
              </div>
              <div className="krow__sub">{p.key}</div>
            </div>
            <div className="krow__act">
              <button
                className="cbtn cbtn--sm cbtn--ghost"
                onClick={() => set("_toast", `Rotated ${p.name} key.`)}
              >
                Rotate
              </button>
              <button
                className="cbtn cbtn--sm cbtn--ghost"
                onClick={() => set("_toast", `Removed ${p.name}.`)}
              >
                <Icon.trash />
              </button>
            </div>
          </div>
        ))}
      </SetCard>
      <SetCard title="Add a provider">
        {empty.map((p) => (
          <div className="krow" key={p.id}>
            <span className="krow__logo" style={{ background: p.color }}>
              {p.ini}
            </span>
            <div className="krow__main">
              <div className="krow__name">{p.name}</div>
              <div className="krow__sub" style={{ fontFamily: "var(--body)" }}>
                Not connected
              </div>
            </div>
            <div className="krow__act">
              <button
                className="cbtn cbtn--sm"
                onClick={() => setModal({ preset: p.id })}
              >
                <Icon.plus /> Add key
              </button>
            </div>
          </div>
        ))}
        <div className="frow">
          <div className="frow__l">
            <div className="frow__lbl">Another provider</div>
            <div className="frow__hint">
              Any OpenAI-compatible endpoint works too.
            </div>
          </div>
          <button
            className="cbtn cbtn--pri"
            onClick={() => setModal({ preset: null })}
          >
            <Icon.key /> Add a key
          </button>
        </div>
      </SetCard>
      {modal && (
        <AddKeyModal
          preset={modal.preset}
          onClose={() => setModal(null)}
          onAdd={(prov, key, model) => {
            addProvider(prov, key, model);
            set("_toast", "Provider connected.");
          }}
        />
      )}
    </div>
  );
}

function LocalModelsSection({ localInstalled, addLocal, prefs, set }) {
  const [open, setOpen] = useFl(false);
  return (
    <div className="set-sec">
      <SecHead
        title="Local models"
        desc="Run a model entirely on this machine — no key, no network, nothing leaves your box."
      />
      <SetCard
        title="Installed"
        meta={`${localInstalled.length} models`}
        note={
          <span>
            Powered by your local runtime (<b>Ollama</b>). Inference uses your
            GPU/CPU — private and offline.
          </span>
        }
      >
        {localInstalled.map((m) => (
          <div className="krow" key={m.id}>
            <span
              className="krow__logo"
              style={{ background: "#201f2a", color: "var(--jade)" }}
            >
              <Icon.chip />
            </span>
            <div className="krow__main">
              <div className="krow__name">
                {m.name} · {m.param}{" "}
                {m.note === "default local" && (
                  <span
                    className="chip chip--sky"
                    style={{ padding: "1px 8px" }}
                  >
                    default local
                  </span>
                )}
              </div>
              <div className="krow__sub">{m.size} on disk</div>
            </div>
            <div className="krow__act">
              <button
                className="cbtn cbtn--sm cbtn--ghost"
                onClick={() => set("_toast", `${m.name} ready.`)}
              >
                Run
              </button>
              <button className="cbtn cbtn--sm cbtn--ghost">
                <Icon.trash />
              </button>
            </div>
          </div>
        ))}
        <div className="frow">
          <div className="frow__l">
            <div className="frow__lbl">Get another model</div>
            <div className="frow__hint">
              Browse open models and pull one down.
            </div>
          </div>
          <button className="cbtn cbtn--pri" onClick={() => setOpen(true)}>
            <Icon.download /> Download a model
          </button>
        </div>
      </SetCard>
      {open && (
        <LocalModelModal
          onClose={() => setOpen(false)}
          onAdd={(m, def) => {
            addLocal(m, def);
            set(
              "_toast",
              `${m.name} installed${def ? " · set as default local" : ""}.`,
            );
          }}
        />
      )}
    </div>
  );
}

function ModelBehaviorSection({
  prefs,
  set,
  providers,
  localInstalled,
  gotoConnectors,
}) {
  const cloud = providers
    .filter((p) => p.status === "connected")
    .map((p) => `${p.name} · ${p.model}`);
  const local = localInstalled.map((m) => `Local · ${m.name} ${m.param}`);
  return (
    <div className="set-sec">
      <SecHead
        title="Model & behavior"
        desc="How the agent thinks and how far it can go on its own."
      />
      <SetCard title="Defaults">
        <Field
          label="Default model"
          hint="Used for new runs. Switch any run in the composer."
        >
          <select
            className="csel cin--narrow"
            value={prefs.model}
            onChange={(e) => set("model", e.target.value)}
          >
            <optgroup label="Cloud · your keys">
              {cloud.map((m) => (
                <option key={m}>{m}</option>
              ))}
            </optgroup>
            <optgroup label="Local · your machine">
              {local.map((m) => (
                <option key={m}>{m}</option>
              ))}
            </optgroup>
          </select>
        </Field>
        <Field label="Reasoning depth" hint="Deeper plans more before acting.">
          <select
            className="csel"
            style={{ width: 150 }}
            value={prefs.depth}
            onChange={(e) => set("depth", e.target.value)}
          >
            <option>Auto</option>
            <option>Quick</option>
            <option>Standard</option>
            <option>Deep</option>
          </select>
        </Field>
        <Field label="Web access" hint="Let the agent fetch URLs and search.">
          <Toggle on={prefs.web} onChange={(v) => set("web", v)} />
        </Field>
      </SetCard>

      <SetCard
        title="Approval policy"
        note={
          <span>
            The one tool control that belongs in Settings. <b>Which</b> tools
            the agent may use is set per-connector on the{" "}
            <a
              href="#"
              onClick={(e) => {
                e.preventDefault();
                gotoConnectors();
              }}
            >
              Connectors page →
            </a>
          </span>
        }
      >
        <Field label="Read-only actions" hint="Search, list, view. No changes.">
          <select
            className="csel"
            style={{ width: 170 }}
            value={prefs.polRead}
            onChange={(e) => set("polRead", e.target.value)}
          >
            <option>Auto-approve</option>
            <option>Ask first</option>
          </select>
        </Field>
        <Field label="Write actions" hint="Post, message, edit records.">
          <select
            className="csel"
            style={{ width: 170 }}
            value={prefs.polWrite}
            onChange={(e) => set("polWrite", e.target.value)}
          >
            <option>Require approval</option>
            <option>Ask first</option>
            <option>Auto-approve</option>
            <option>Block</option>
          </select>
        </Field>
        <Field
          label="On-chain, spend & destructive"
          hint="Sign transactions, move funds, delete."
        >
          <select
            className="csel"
            style={{ width: 170 }}
            value={prefs.polDanger}
            onChange={(e) => set("polDanger", e.target.value)}
          >
            <option>Require approval</option>
            <option>Block</option>
          </select>
        </Field>
      </SetCard>

      <SetCard title="Spend guardrail">
        <Field label="Monthly API cap" hint="Across all your provider keys.">
          <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <span className="mono" style={{ color: "var(--mut)" }}>
              $
            </span>
            <input
              className="cin mono"
              style={{ width: 90 }}
              value={prefs.cap}
              onChange={(e) => set("cap", e.target.value)}
            />
          </div>
        </Field>
        <Field
          label="Pause runs at cap"
          hint="Stop and ask before exceeding it."
        >
          <Toggle on={prefs.capPause} onChange={(v) => set("capPause", v)} />
        </Field>
      </SetCard>
    </div>
  );
}

Object.assign(window, {
  AddKeyModal,
  LocalModelModal,
  ConnectModal,
  ProviderKeysSection,
  LocalModelsSection,
  ModelBehaviorSection,
});
