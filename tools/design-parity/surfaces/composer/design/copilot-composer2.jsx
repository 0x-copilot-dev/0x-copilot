/* global React, Icon, CONNECTORS, PROVIDERS, LOCAL_INSTALLED, PROJECT_FILES */
const { useState: useC2, useEffect: useC2E, useRef: useC2R } = React;

const C2S = {
  width: "1em",
  height: "1em",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.7,
  strokeLinecap: "round",
  strokeLinejoin: "round",
  viewBox: "0 0 24 24",
};
const CIcon = {
  clip: () => (
    <svg {...C2S}>
      <path d="M21 11.5l-8.5 8.5a5.4 5.4 0 0 1-7.6-7.6L13.5 3.8a3.6 3.6 0 0 1 5.1 5.1l-8.6 8.6a1.8 1.8 0 0 1-2.5-2.5l7.9-7.9" />
    </svg>
  ),
  img: () => (
    <svg {...C2S}>
      <rect x="3" y="5" width="18" height="14" rx="2" />
      <circle cx="8.5" cy="10" r="1.4" />
      <path d="M21 16l-4.5-4.5L7 21" />
    </svg>
  ),
};

function Pop({ align = "l", width = 296, onClose, children }) {
  useC2E(() => {
    const k = (e) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, []);
  return (
    <>
      <div className="pop-scrim" onMouseDown={onClose} />
      <div
        className="pop"
        style={{ width, ...(align === "r" ? { left: "auto", right: 0 } : {}) }}
      >
        {children}
      </div>
    </>
  );
}

function RunComposer({ onSend, navigate, placeholder }) {
  const [draft, setDraft] = useC2("");
  const [atts, setAtts] = useC2([]);
  const [open, setOpen] = useC2(null);
  const [model, setModel] = useC2({
    id: "anthropic",
    name: "Claude Sonnet 4.5",
    sub: "Anthropic · your key",
    color: "#d97757",
    local: false,
  });
  const [extra, setExtra] = useC2([]);
  const [connect, setConnect] = useC2(false);
  const [tools, setTools] = useC2(() => {
    const m = { web: true };
    CONNECTORS.forEach((c) => {
      m[c.id] = true;
    });
    return m;
  });
  const taRef = useC2R(null);

  const keyed = PROVIDERS.filter((p) => p.status === "connected");
  const conns = [...CONNECTORS, ...extra];
  const onCount = Object.values(tools).filter(Boolean).length;
  const total = conns.length + 1;
  const nav = (d, s) => {
    setOpen(null);
    if (navigate) navigate(d, s);
  };
  const flip = (id) => setTools((t) => ({ ...t, [id]: !t[id] }));
  const toggle = (id) => setOpen((o) => (o === id ? null : id));

  const grow = () => {
    const el = taRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 130) + "px";
  };
  const addAtt = (n) => {
    setAtts((a) => (a.find((x) => x.n === n) ? a : [...a, { n }]));
    setOpen(null);
  };
  const send = () => {
    if (!draft.trim() && !atts.length) return;
    onSend(
      draft.trim(),
      atts.map((a) => a.n),
      model,
    );
    setDraft("");
    setAtts([]);
    requestAnimationFrame(() => {
      const el = taRef.current;
      if (el) el.style.height = "auto";
    });
  };

  return (
    <div className="cmp">
      {open === "attach" && (
        <Pop onClose={() => setOpen(null)}>
          <div className="pop__h">
            Attach <span className="meta">drag &amp; drop works too</span>
          </div>
          <div className="pop__list">
            <button
              className="pop-row"
              onClick={() => addAtt("q3-runway.xlsx")}
            >
              <span className="lg">
                <Icon.download />
              </span>
              <span className="m">
                <span className="nm">Upload from computer</span>
                <span className="sb">any file up to 100 MB</span>
              </span>
            </button>
            <button
              className="pop-row"
              onClick={() => addAtt("screenshot-11-44.png")}
            >
              <span className="lg">
                <CIcon.img />
              </span>
              <span className="m">
                <span className="nm">Capture screenshot</span>
                <span className="sb">a window or a selection</span>
              </span>
            </button>
            <div className="pop-div" />
            <div className="pop__grp">From project · Launch Week</div>
            {PROJECT_FILES.slice(0, 3).map((f) => (
              <button key={f.n} className="pop-row" onClick={() => addAtt(f.n)}>
                <span className="lg">
                  <Icon.doc />
                </span>
                <span className="m">
                  <span className="nm">
                    <span className="txt">{f.n}</span>
                  </span>
                  <span className="sb">{f.m}</span>
                </span>
              </button>
            ))}
          </div>
        </Pop>
      )}
      {open === "model" && (
        <Pop onClose={() => setOpen(null)} width={300}>
          <div className="pop__h">
            Model <span className="meta">this chat</span>
          </div>
          <div className="pop__list">
            <div className="pop__grp">Your keys</div>
            {keyed.map((p) => (
              <button
                key={p.id}
                className="pop-row"
                data-on={model.id === p.id || undefined}
                onClick={() => {
                  setModel({
                    id: p.id,
                    name: p.model,
                    sub: p.name,
                    color: p.color,
                    local: false,
                  });
                  setOpen(null);
                }}
              >
                <span className="lg">{p.ini}</span>
                <span className="m">
                  <span className="nm">
                    <span className="txt">{p.model}</span>
                  </span>
                  <span className="sb">{p.name} · your key</span>
                </span>
                <span className="rad">
                  {model.id === p.id && <Icon.check />}
                </span>
              </button>
            ))}
            <div className="pop__grp">Local · on-device</div>
            {LOCAL_INSTALLED.map((m) => {
              const id = "local-" + m.id;
              return (
                <button
                  key={id}
                  className="pop-row"
                  data-on={model.id === id || undefined}
                  onClick={() => {
                    setModel({
                      id,
                      name: m.name + " " + m.param,
                      sub: "local",
                      local: true,
                    });
                    setOpen(null);
                  }}
                >
                  <span className="lg">
                    <Icon.chip />
                  </span>
                  <span className="m">
                    <span className="nm">
                      <span className="txt">
                        {m.name} {m.param}
                      </span>
                    </span>
                    <span className="sb">
                      {m.size} · never leaves this machine
                    </span>
                  </span>
                  <span className="rad">
                    {model.id === id && <Icon.check />}
                  </span>
                </button>
              );
            })}
          </div>
          <div className="pop__f">
            <a onClick={() => nav("settings", "keys")}>Add a provider key →</a>
            <span className="sp" />
            <a onClick={() => nav("settings", "local")}>Get local models →</a>
          </div>
        </Pop>
      )}
      {open === "tools" && (
        <Pop onClose={() => setOpen(null)} width={318}>
          <div className="pop__h">
            Tools &amp; connections{" "}
            <span className="meta">
              {onCount} of {total} on
            </span>
          </div>
          <div className="pop__list">
            <div className="pop-row" data-off={!tools.web || undefined}>
              <span className="lg">
                <Icon.globe />
              </span>
              <span className="m">
                <span className="nm">Web search</span>
                <span className="sb">built-in</span>
              </span>
              <button
                className="ctog ctog--sm"
                data-on={tools.web ? "true" : "false"}
                onClick={() => flip("web")}
                aria-label="Toggle web search"
              />
            </div>
            <div className="pop-div" />
            {conns.map((c) => (
              <div
                key={c.id}
                className="pop-row"
                data-off={!tools[c.id] || undefined}
              >
                <span className="lg">{c.ini}</span>
                <span className="m">
                  <span className="nm">
                    <span className="txt">{c.name}</span>
                    <span
                      className={"permc" + (c.perm === "act" ? " act" : "")}
                    >
                      {c.perm === "act" ? "acts" : "reads"}
                    </span>
                  </span>
                  <span className="sb">{c.sub}</span>
                </span>
                <button
                  className="ctog ctog--sm"
                  data-on={tools[c.id] ? "true" : "false"}
                  onClick={() => flip(c.id)}
                  aria-label={"Toggle " + c.name}
                />
              </div>
            ))}
          </div>
          <button
            className="pop-row pop-row--pin"
            onClick={() => {
              setOpen(null);
              setConnect(true);
            }}
          >
            <span className="lg">
              <Icon.plus />
            </span>
            <span className="m">
              <span className="nm">Connect a tool…</span>
              <span className="sb">catalog or custom MCP server</span>
            </span>
          </button>
          <div className="pop__f">
            <a onClick={() => nav("connectors")}>Manage tools →</a>
            <span className="sp" />
            <a onClick={() => nav("settings", "behavior")}>Approval policy →</a>
          </div>
        </Pop>
      )}
      {connect && (
        <window.ConnectModal
          onClose={() => setConnect(false)}
          onAdd={(c) => {
            setExtra((x) => (x.find((y) => y.id === c.id) ? x : [...x, c]));
            setTools((t) => ({ ...t, [c.id]: true }));
          }}
        />
      )}
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
        rows={1}
        value={draft}
        placeholder={
          placeholder || "Message 0xCopilot — drop files, @ tools, / skills"
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
          onClick={() => toggle("attach")}
        >
          <CIcon.clip />
        </button>
        <button
          className="cmp-pill"
          title="Choose model"
          data-open={open === "model" || undefined}
          onClick={() => toggle("model")}
        >
          {model.local ? (
            <Icon.chip />
          ) : (
            <span className="pd" style={{ background: model.color }} />
          )}
          <span className="lb">{model.name}</span>
          <Icon.chevD />
        </button>
        <button
          className="cmp-pill"
          title="Tools & connections"
          data-open={open === "tools" || undefined}
          onClick={() => toggle("tools")}
        >
          <Icon.plug />
          <span className="lb">Tools</span>
          <span className="n">
            {onCount}/{total}
          </span>
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
  );
}

Object.assign(window, { RunComposer });
