/* global React, ReactDOM, Icon, Mark, Workspace, Settings, ACTIVITY, CONNECTORS, NOTIF */
const { useState: useApp, useEffect: useAppE } = React;

const DEST = [
  { id: "workspace", label: "Run", icon: "run", badge: "1" },
  { id: "chats", label: "Chats", icon: "chats" },
  { id: "projects", label: "Projects", icon: "folder" },
  { id: "activity", label: "Activity", icon: "activity" },
  { id: "connectors", label: "Tools", icon: "plug" },
  { id: "skills", label: "Skills", icon: "skill" },
];

/* ---------------------------- Activity ---------------------------------- */
function ActivitySurface({ navigate }) {
  const chip = {
    running: ["chip--ok", "running"],
    done: ["chip--ok", "done"],
    paused: ["chip--warn", "paused"],
    stopped: ["chip--off", "stopped"],
  };
  return (
    <div
      className="pg scroll"
      style={{ flex: 1, minHeight: 0, overflow: "auto" }}
    >
      <p className="pg-lead">
        Everything the agent has done, most recent first. This is the record the
        old build buried in an "audit log" — here it's a place you visit.
        Retention, export, and delete live in{" "}
        <a
          href="#"
          onClick={(e) => {
            e.preventDefault();
            navigate("settings", "privacy");
          }}
        >
          Settings → Privacy
        </a>
        .
      </p>
      {ACTIVITY.map((grp) => (
        <div key={grp.day}>
          <div className="act-day">{grp.day}</div>
          <div className="rowlist">
            {grp.runs.map((r, i) => {
              const [cc, cl] = chip[r.status];
              const isLive = r.status === "running";
              return (
                <button
                  key={i}
                  className="lrow"
                  onClick={() => (isLive ? navigate("workspace") : null)}
                >
                  <span
                    className="lrow__ic"
                    style={{ color: isLive ? "var(--jade)" : undefined }}
                  >
                    {isLive ? <Mark size={18} /> : <Icon.clock />}
                  </span>
                  <span className="lrow__main">
                    <span className="lrow__name">
                      {r.title}{" "}
                      <span
                        className={"chip " + cc}
                        style={{ padding: "1px 8px" }}
                      >
                        {isLive && <span className="dotk" />}
                        {cl}
                      </span>
                    </span>
                    <span
                      className="lrow__sub"
                      style={{ fontFamily: "var(--body)" }}
                    >
                      {r.meta}
                    </span>
                  </span>
                  <span className="lrow__time">{r.time}</span>
                  {isLive ? <Icon.chevR /> : <span style={{ width: 16 }} />}
                </button>
              );
            })}
          </div>
        </div>
      ))}
    </div>
  );
}

/* --------------------------- Connectors --------------------------------- */
function ConnectorsSurface({ connectors, setPerm, addConn, navigate }) {
  const [open, setOpen] = useApp(false);
  const permLabel = { read: "Read only", act: "Read & act", off: "Off" };
  return (
    <div
      className="pg scroll"
      style={{ flex: 1, minHeight: 0, overflow: "auto" }}
    >
      <p className="pg-lead">
        The apps the agent can read from and act through — a destination, not a
        settings tab. Per-tool access lives here; the agent's approval{" "}
        <em>policy</em> lives in{" "}
        <a
          href="#"
          onClick={(e) => {
            e.preventDefault();
            navigate("settings", "behavior");
          }}
        >
          Settings → Model &amp; behavior
        </a>
        .
      </p>
      <div style={{ display: "flex", alignItems: "center", marginBottom: 14 }}>
        <div className="sect-h" style={{ margin: 0 }}>
          Connected · {connectors.length}
        </div>
        <button
          className="cbtn cbtn--pri cbtn--sm"
          style={{ marginLeft: "auto" }}
          onClick={() => setOpen(true)}
        >
          <Icon.plus /> Connect a tool
        </button>
      </div>
      <div className="rowlist">
        {connectors.map((c) => (
          <div key={c.id} className="lrow" style={{ cursor: "default" }}>
            <span className="lrow__logo" style={{ background: c.color }}>
              {c.ini}
            </span>
            <span className="lrow__main">
              <span className="lrow__name">{c.name}</span>
              <span className="lrow__sub">{c.sub}</span>
            </span>
            <span className="lrow__act">
              <div className="seg">
                {[
                  ["read", "Read"],
                  ["act", "Read & act"],
                  ["off", "Off"],
                ].map(([id, l]) => (
                  <button
                    key={id}
                    data-on={c.perm === id ? "true" : undefined}
                    onClick={() => setPerm(c.id, id)}
                  >
                    {l}
                  </button>
                ))}
              </div>
            </span>
          </div>
        ))}
      </div>
      {open && (
        <window.ConnectModal
          onClose={() => setOpen(false)}
          onAdd={(c, perm) => addConn(c, perm)}
        />
      )}
    </div>
  );
}

/* ----------------------------- Skills ----------------------------------- */
const SKILLS = [
  {
    name: "Weekly treasury reconciliation",
    sub: "Sheets · Safe · Dune",
    runs: 14,
    ic: "coin",
  },
  {
    name: "Contributor payout batch",
    sub: "Sheets → Safe multisig",
    runs: 6,
    ic: "coin",
  },
  {
    name: "Launch thread + AMA recap",
    sub: "X · Discord",
    runs: 9,
    ic: "skill",
  },
  {
    name: "Investor update draft",
    sub: "Docs · Local files",
    runs: 4,
    ic: "folder",
  },
];
function SkillsSurface({ navigate }) {
  return (
    <div
      className="pg scroll"
      style={{ flex: 1, minHeight: 0, overflow: "auto" }}
    >
      <p className="pg-lead">
        Saved multi-step workflows you can re-run in one click. Like connectors,
        skills are their own place — not a settings tab.
      </p>
      <div style={{ display: "flex", alignItems: "center", marginBottom: 14 }}>
        <div className="sect-h" style={{ margin: 0 }}>
          Your skills
        </div>
        <button className="cbtn cbtn--sm" style={{ marginLeft: "auto" }}>
          <Icon.plus /> New skill
        </button>
      </div>
      <div className="grid2">
        {SKILLS.map((s) => {
          const I = Icon[s.ic];
          return (
            <div
              key={s.name}
              className="card"
              style={{ display: "flex", gap: 13, alignItems: "flex-start" }}
            >
              <span className="lrow__ic" style={{ flex: "none" }}>
                <I />
              </span>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div
                  style={{
                    fontWeight: 600,
                    fontSize: 13.5,
                    fontFamily: "var(--disp)",
                  }}
                >
                  {s.name}
                </div>
                <div className="lrow__sub" style={{ marginTop: 2 }}>
                  {s.sub} · {s.runs} runs
                </div>
                <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
                  <button
                    className="cbtn cbtn--sm cbtn--pri"
                    onClick={() => navigate("workspace")}
                  >
                    <Icon.play /> Run
                  </button>
                  <button className="cbtn cbtn--sm cbtn--ghost">Edit</button>
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

/* ------------------------- Chats & Projects ----------------------------- */
function ChatRow({ c, navigate }) {
  const st = {
    running: ["chip--ok", "running"],
    done: ["chip--ok", "done"],
    paused: ["chip--warn", "paused"],
    archived: ["chip--off", "archived"],
  }[c.status];
  const live = c.status === "running";
  return (
    <button className="lrow" onClick={() => navigate("workspace")}>
      <span
        className="lrow__ic"
        style={{ color: live ? "var(--jade)" : undefined }}
      >
        {live ? <Mark size={18} /> : <Icon.chats />}
      </span>
      <span className="lrow__main">
        <span className="lrow__name">
          {c.title}{" "}
          <span className={"chip " + st[0]} style={{ padding: "1px 8px" }}>
            {live && <span className="dotk" />}
            {st[1]}
          </span>
        </span>
        <span className="lrow__sub" style={{ fontFamily: "var(--body)" }}>
          {c.preview} · <span className="mono">{c.model}</span>
        </span>
      </span>
      <span className="lrow__time">{c.when}</span>
    </button>
  );
}
function ChatsSurface({ navigate }) {
  const pinned = CHATS.filter((c) => c.pinned);
  const recent = CHATS.filter((c) => !c.pinned && c.status !== "archived");
  const archived = CHATS.filter((c) => c.status === "archived");
  return (
    <div
      className="pg scroll"
      style={{ flex: 1, minHeight: 0, overflow: "auto" }}
    >
      <p className="pg-lead">
        Every conversation with the agent — each chat is a run you can reopen,
        continue, or archive.
      </p>
      <div style={{ display: "flex", alignItems: "center", marginBottom: 14 }}>
        <div className="sect-h" style={{ margin: 0 }}>
          Pinned
        </div>
        <button
          className="cbtn cbtn--pri cbtn--sm"
          style={{ marginLeft: "auto" }}
          onClick={() => navigate("workspace")}
        >
          <Icon.plus /> New chat
        </button>
      </div>
      <div className="rowlist">
        {pinned.map((c) => (
          <ChatRow key={c.id} c={c} navigate={navigate} />
        ))}
      </div>
      <div className="sect-h">Recent</div>
      <div className="rowlist">
        {recent.map((c) => (
          <ChatRow key={c.id} c={c} navigate={navigate} />
        ))}
      </div>
      <div className="sect-h">Archived · history</div>
      <div className="rowlist">
        {archived.map((c) => (
          <ChatRow key={c.id} c={c} navigate={navigate} />
        ))}
      </div>
    </div>
  );
}
function ProjectsSurface({ navigate }) {
  const [sel, setSel] = useApp(null);
  if (sel) {
    const p = PROJECTS.find((x) => x.id === sel);
    const chats = CHATS.filter((c) => c.project === sel);
    return (
      <div
        className="pg scroll"
        style={{ flex: 1, minHeight: 0, overflow: "auto" }}
      >
        <button className="backlink" onClick={() => setSel(null)}>
          <Icon.back /> All projects
        </button>
        <div
          style={{
            display: "flex",
            gap: 13,
            alignItems: "center",
            marginBottom: 4,
          }}
        >
          <span className="proj-ic" style={{ background: p.color }}>
            {p.name[0]}
          </span>
          <div>
            <h2 style={{ fontSize: 18 }}>{p.name}</h2>
            <div className="lrow__sub" style={{ fontFamily: "var(--body)" }}>
              {p.desc}
            </div>
          </div>
        </div>
        <div className="sect-h">Chats · {chats.length}</div>
        <div className="rowlist">
          {chats.map((c) => (
            <ChatRow key={c.id} c={c} navigate={navigate} />
          ))}
        </div>
        <div className="sect-h">Files · {p.files}</div>
        <div className="rowlist">
          {PROJECT_FILES.map((f) => (
            <div key={f.n} className="lrow" style={{ cursor: "default" }}>
              <span className="lrow__ic">
                <Icon.doc />
              </span>
              <span className="lrow__main">
                <span className="lrow__name">{f.n}</span>
                <span className="lrow__sub">{f.m}</span>
              </span>
            </div>
          ))}
        </div>
      </div>
    );
  }
  return (
    <div
      className="pg scroll"
      style={{ flex: 1, minHeight: 0, overflow: "auto" }}
    >
      <p className="pg-lead">
        Group related chats, files, and context. Open a project to see its
        conversations and working files.
      </p>
      <div className="grid3">
        {PROJECTS.map((p) => (
          <button
            key={p.id}
            className="card proj-card"
            onClick={() => setSel(p.id)}
          >
            <div style={{ display: "flex", gap: 12, alignItems: "center" }}>
              <span className="proj-ic" style={{ background: p.color }}>
                {p.name[0]}
              </span>
              <div
                style={{
                  fontFamily: "var(--disp)",
                  fontWeight: 600,
                  fontSize: 14,
                }}
              >
                {p.name}
              </div>
            </div>
            <div
              className="lrow__sub"
              style={{ fontFamily: "var(--body)", marginTop: 10 }}
            >
              {p.desc}
            </div>
            <div className="lrow__sub" style={{ marginTop: 10 }}>
              {p.chats} chats · {p.files} files
            </div>
          </button>
        ))}
      </div>
    </div>
  );
}

/* ------------------------- Command palette ------------------------------ */
function Palette({ onClose, navigate }) {
  const [q, setQ] = useApp("");
  const items = [
    {
      t: "Go to Run",
      k: "workspace",
      nav: () => navigate("workspace"),
      ic: "run",
    },
    { t: "Go to Chats", k: "chats", nav: () => navigate("chats"), ic: "chats" },
    {
      t: "Go to Projects",
      k: "projects",
      nav: () => navigate("projects"),
      ic: "folder",
    },
    {
      t: "New chat",
      k: "new run",
      nav: () => navigate("workspace"),
      ic: "plus",
    },
    {
      t: "Go to Activity",
      k: "activity",
      nav: () => navigate("activity"),
      ic: "activity",
    },
    {
      t: "Go to Tools",
      k: "connectors",
      nav: () => navigate("connectors"),
      ic: "plug",
    },
    {
      t: "Go to Skills",
      k: "skills",
      nav: () => navigate("skills"),
      ic: "skill",
    },
    {
      t: "Add a provider key",
      k: "BYOK",
      nav: () => navigate("settings", "keys"),
      ic: "key",
    },
    {
      t: "Download a local model",
      k: "local",
      nav: () => navigate("settings", "local"),
      ic: "chip",
    },
    {
      t: "Connect a tool",
      k: "connect",
      nav: () => navigate("connectors"),
      ic: "plug",
    },
    {
      t: "Model & behavior",
      k: "policy",
      nav: () => navigate("settings", "behavior"),
      ic: "sliders",
    },
    {
      t: "Appearance",
      k: "theme",
      nav: () => navigate("settings", "appearance"),
      ic: "sun",
    },
    {
      t: "Open Settings",
      k: "settings",
      nav: () => navigate("settings", "profile"),
      ic: "gear",
    },
  ];
  const f = items.filter((it) =>
    (it.t + it.k).toLowerCase().includes(q.toLowerCase()),
  );
  return ReactDOM.createPortal(
    <div className="cmdk-scrim" onMouseDown={onClose}>
      <div className="cmdk" onMouseDown={(e) => e.stopPropagation()}>
        <div className="cmdk__in">
          <Icon.search />
          <input
            autoFocus
            placeholder="Search commands, settings, tools…"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && f[0]) {
                f[0].nav();
                onClose();
              }
            }}
          />
        </div>
        <div className="cmdk__list">
          {f.map((it) => {
            const I = Icon[it.ic];
            return (
              <button
                key={it.t}
                className="cmdk__row"
                onClick={() => {
                  it.nav();
                  onClose();
                }}
              >
                <I />
                <span className="t">{it.t}</span>
                <span className="k">{it.k}</span>
              </button>
            );
          })}
          {!f.length && (
            <div className="empty" style={{ padding: 30 }}>
              <p>No matches.</p>
            </div>
          )}
        </div>
      </div>
    </div>,
    document.querySelector(".mw") || document.body,
  );
}

/* ------------------------------- App ------------------------------------ */
const TWEAK_DEFAULTS = {
  mode: "studio",
  localFirst: "subtle",
  showApproval: true,
  theme: "dark",
  accent: "sky",
  density: "comfortable",
  reduceMotion: false,
};
const DEFAULT_PREFS = {
  name: "Sasha",
  hours: "9:00 – 18:00",
  tz: "America/Los_Angeles (UTC−7)",
  sync: false,
  theme: "dark",
  accent: "sky",
  density: "comfortable",
  reduceMotion: false,
  retention: "Forever",
  memory: true,
  notif: Object.fromEntries(NOTIF.map((r) => [r.id, { ...r.d }])),
  quiet: true,
  encrypt: true,
  lock: true,
  lockAfter: "15 minutes",
  model: "Anthropic · Claude Sonnet 4.5",
  depth: "Auto",
  web: true,
  polRead: "Auto-approve",
  polWrite: "Require approval",
  polDanger: "Require approval",
  cap: "200",
  capPause: true,
};

const TITLES = {
  workspace: ["Run", "the agent, working — scrub every step"],
  chats: ["Chats", "every conversation with the agent"],
  projects: ["Projects", "group chats, files & context"],
  activity: ["Activity", "every action the agent has taken"],
  connectors: ["Tools", "apps the agent can act through"],
  skills: ["Skills", "saved multi-step workflows"],
};

function App() {
  const [dest, setDest] = useApp("workspace");
  const [setSec, setSetSec] = useApp("profile");
  const [prefs, setPrefs] = useApp(DEFAULT_PREFS);
  const [tw, setTweak] = useTweaks(TWEAK_DEFAULTS);
  const [providers, setProviders] = useApp(window.PROVIDERS);
  const [localInstalled, setLocal] = useApp(window.LOCAL_INSTALLED);
  const [connectors, setConnectors] = useApp(CONNECTORS);
  const [palette, setPalette] = useApp(false);

  const setPref = (k, v) => setPrefs((p) => ({ ...p, [k]: v }));
  const setVis = (k, v) => {
    setPref(k, v);
    setTweak(k, v);
  };
  const navigate = (d, sec) => {
    setDest(d);
    if (sec) setSetSec(sec);
    setPalette(false);
  };
  const addProvider = (id, key, model) =>
    setProviders((ps) =>
      ps.map((p) =>
        p.id === id
          ? {
              ...p,
              status: "connected",
              key:
                key.slice(0, 6) +
                "•••• " +
                Math.random().toString(16).slice(2, 6),
              model,
            }
          : p,
      ),
    );
  const addLocal = (m, def) =>
    setLocal((ls) => [
      ...(def
        ? ls.map((x) => ({
            ...x,
            note: x.note === "default local" ? "installed" : x.note,
          }))
        : ls),
      {
        id: m.id,
        name: m.name,
        param: m.param,
        size: m.size,
        note: def ? "default local" : m.note,
      },
    ]);
  const setPerm = (id, perm) =>
    setConnectors((cs) => cs.map((c) => (c.id === id ? { ...c, perm } : c)));
  const addConn = (c, perm) =>
    setConnectors((cs) =>
      cs.find((x) => x.id === c.id)
        ? cs
        : [
            ...cs,
            {
              id: c.id,
              name: c.name,
              color: c.color,
              ini: c.ini,
              sub: c.sub,
              perm,
            },
          ],
    );

  useAppE(() => {
    const onKey = (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPalette((o) => !o);
      }
      if ((e.metaKey || e.ctrlKey) && e.key === ",") {
        e.preventDefault();
        navigate("settings", "profile");
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const resolvedTheme =
    prefs.theme === "auto"
      ? window.matchMedia("(prefers-color-scheme: light)").matches
        ? "light"
        : "dark"
      : prefs.theme;

  let surface;
  if (dest === "workspace")
    surface = (
      <Workspace
        mode={tw.mode}
        onMode={(m) => setTweak("mode", m)}
        showApproval={tw.showApproval}
      />
    );
  else if (dest === "chats") surface = <ChatsSurface navigate={navigate} />;
  else if (dest === "projects")
    surface = <ProjectsSurface navigate={navigate} />;
  else if (dest === "activity")
    surface = <ActivitySurface navigate={navigate} />;
  else if (dest === "connectors")
    surface = (
      <ConnectorsSurface
        connectors={connectors}
        setPerm={setPerm}
        addConn={addConn}
        navigate={navigate}
      />
    );
  else if (dest === "skills") surface = <SkillsSurface navigate={navigate} />;
  else if (dest === "settings")
    surface = (
      <Settings
        active={setSec}
        setActive={setSetSec}
        prefs={prefs}
        setPref={setPref}
        providers={providers}
        addProvider={addProvider}
        localInstalled={localInstalled}
        addLocal={addLocal}
        gotoActivity={() => navigate("activity")}
        gotoConnectors={() => navigate("connectors")}
      />
    );

  const showTopbar = dest !== "workspace" && dest !== "settings";
  const [tTitle, tSub] = TITLES[dest] || ["", ""];

  return (
    <div className="stage">
      <div
        className="mw"
        data-theme={resolvedTheme}
        data-accent={prefs.accent}
        data-density={prefs.density}
        data-reduce-motion={prefs.reduceMotion ? "1" : "0"}
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
            — local agent workspace
          </div>
          {tw.localFirst === "medium" && (
            <span className="mw-chip">running locally · your key</span>
          )}
        </div>
        {tw.localFirst === "loud" && (
          <div className="mw-lf">
            <span className="dotk" /> Running locally · your keys · nothing
            leaves this machine
          </div>
        )}
        <div className="mw-body">
          <nav className="rail">
            <button
              className="rail-brand"
              title="0xCopilot"
              onClick={() => navigate("workspace")}
            >
              <Mark size={22} />
            </button>
            {DEST.map((d) => {
              const I = Icon[d.icon];
              return (
                <button
                  key={d.id}
                  className="rail-item"
                  data-active={dest === d.id || undefined}
                  onClick={() => navigate(d.id)}
                  title={d.label}
                >
                  <I />
                  <span className="rl">{d.label}</span>
                  {d.badge && dest !== "workspace" && d.id === "workspace" && (
                    <span className="rbadge">{d.badge}</span>
                  )}
                </button>
              );
            })}
            <div className="rail-foot">
              <button
                className="rail-item"
                data-active={dest === "settings" || undefined}
                onClick={() => navigate("settings", setSec)}
                title="Settings"
              >
                <Icon.gear />
                <span className="rl">Settings</span>
              </button>
              <button className="rail-me" title={prefs.name}>
                {prefs.name.slice(0, 1)}
              </button>
            </div>
          </nav>
          <div className="main">
            {showTopbar && (
              <div className="topbar">
                <div className="tb-title">
                  <h1>{tTitle}</h1>
                  <span className="sub">{tSub}</span>
                </div>
                <div className="tb-spacer" />
                <button className="tb-search" onClick={() => setPalette(true)}>
                  <Icon.search /> Search & commands <kbd>⌘K</kbd>
                </button>
              </div>
            )}
            {surface}
          </div>
        </div>
      </div>
      {palette && (
        <Palette onClose={() => setPalette(false)} navigate={navigate} />
      )}
      <TweaksPanel title="Tweaks">
        <TweakSection label="Workspace" />
        <TweakRadio
          label="Mode"
          value={tw.mode}
          options={[
            { value: "studio", label: "Studio" },
            { value: "focus", label: "Focus" },
            { value: "auto", label: "Auto" },
          ]}
          onChange={(v) => setTweak("mode", v)}
        />
        <TweakToggle
          label="Staged approval step"
          value={tw.showApproval}
          onChange={(v) => setTweak("showApproval", v)}
        />
        <TweakRadio
          label="Local-first cue"
          value={tw.localFirst}
          options={[
            { value: "subtle", label: "Subtle" },
            { value: "medium", label: "Medium" },
            { value: "loud", label: "Loud" },
          ]}
          onChange={(v) => setTweak("localFirst", v)}
        />
        <TweakSection label="Appearance" />
        <TweakRadio
          label="Theme"
          value={prefs.theme}
          options={[
            { value: "dark", label: "Dark" },
            { value: "light", label: "Light" },
            { value: "auto", label: "System" },
          ]}
          onChange={(v) => setVis("theme", v)}
        />
        <TweakRadio
          label="Accent"
          value={prefs.accent}
          options={[
            { value: "sky", label: "Sky" },
            { value: "jade", label: "Jade" },
            { value: "ember", label: "Ember" },
            { value: "violet", label: "Violet" },
          ]}
          onChange={(v) => setVis("accent", v)}
        />
        <TweakRadio
          label="Density"
          value={prefs.density}
          options={[
            { value: "comfortable", label: "Comfy" },
            { value: "compact", label: "Compact" },
            { value: "spacious", label: "Roomy" },
          ]}
          onChange={(v) => setVis("density", v)}
        />
        <TweakToggle
          label="Reduce motion"
          value={prefs.reduceMotion}
          onChange={(v) => setVis("reduceMotion", v)}
        />
      </TweaksPanel>
    </div>
  );
}

if (!window.__COPILOT_SKIP_MOUNT)
  ReactDOM.createRoot(document.getElementById("app-root")).render(<App />);
Object.assign(window, {
  App,
  ActivitySurface,
  ConnectorsSurface,
  SkillsSurface,
  ChatsSurface,
  ProjectsSurface,
  Palette,
  DEST,
  TITLES,
  DEFAULT_PREFS,
});
