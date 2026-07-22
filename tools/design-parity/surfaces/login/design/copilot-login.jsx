/* global React, ReactDOM, Icon, Mark */
/* Design source of truth: Claude Design project 73f810d9 (copilot-login.jsx).
   Vendored for the design-parity harness. The ONLY adaptation from the original
   is the initial-view line below (marked `harness:`), which lets `?state=` pick
   any of the 8 views for extraction without click-driving the flow. Everything
   else is byte-faithful — edit the app to match this, not the reverse.
   Refresh via DesignSync get_file (see ../../../design-kit/REFRESH.md). */
const { useState: useLg, useRef: useLgRef } = React;

const WALLETS = [
  { id: "metamask", name: "MetaMask", ini: "MM", sub: "browser extension" },
  { id: "rabby", name: "Rabby", ini: "Ra", sub: "browser extension" },
  {
    id: "walletconnect",
    name: "WalletConnect",
    ini: "WC",
    sub: "scan from any mobile wallet",
  },
  { id: "ledger", name: "Ledger", ini: "Le", sub: "hardware · via USB" },
];
const ADDR = "0x7f3C…a92C";

function LoginApp() {
  // harness: initial view overridable via ?state= (design default: "pick").
  // views: pick · wallets · connecting · werr · sign · google · gerr · done
  const [view, setView] = useLg(
    new URLSearchParams(location.search).get("state") || "pick",
  );
  const [wallet, setWallet] = useLg(WALLETS[0]);

  const [gTry, setGTry] = useLg(0);
  const [wTry, setWTry] = useLg(0);
  const tRef = useLgRef(null);
  const arm = (fn, ms) => {
    clearTimeout(tRef.current);
    tRef.current = setTimeout(fn, ms);
  };
  const cancelTo = (v) => {
    clearTimeout(tRef.current);
    setView(v);
  };
  const enter = () => {
    setView("done");
    arm(() => {
      location.href = "0xCopilot First Run.html";
    }, 900);
  };
  // demo: WalletConnect & Ledger stall on the first attempt so the failure state is visible; retry succeeds
  const pickWallet = (w, retry) => {
    setWallet(w);
    setView("connecting");
    const fail =
      !retry && !wTry && (w.id === "walletconnect" || w.id === "ledger");
    if (fail)
      arm(() => {
        setWTry(1);
        setView("werr");
      }, 3800);
    else arm(() => setView("sign"), retry ? 1500 : 1200);
  };
  // demo: first Google attempt fails so the recovery state is visible; retry succeeds
  const google = () => {
    setView("google");
    if (gTry === 0)
      arm(() => {
        setGTry(1);
        setView("gerr");
      }, 1900);
    else arm(enter, 1400);
  };

  let body;
  if (view === "pick")
    body = (
      <>
        <h1>
          Welcome to <span className="zx">0x</span>Copilot
        </h1>
        <p className="sub">
          Choose how to sign in — either way, it runs on your machine.
        </p>
        <div className="login-opts">
          <button
            className="login-opt login-opt--pri"
            onClick={() => setView("wallets")}
          >
            <span className="ic">
              <Icon.wallet />
            </span>
            <span className="lx">
              Continue with a wallet
              <small>MetaMask · Rabby · WalletConnect · Ledger</small>
            </span>
            <Icon.chevR />
          </button>
          <button className="login-opt" onClick={google}>
            <span className="ic" style={{ fontWeight: 600, fontSize: 13 }}>
              G
            </span>
            <span className="lx">
              Continue with Google<small>for encrypted settings sync</small>
            </span>
            <Icon.chevR />
          </button>
          <div className="login-div">or</div>
          <button className="login-opt" onClick={enter}>
            <span className="ic">
              <Icon.chip />
            </span>
            <span className="lx">
              Use locally, no account
              <small>everything stays on this device</small>
            </span>
            <Icon.chevR />
          </button>
        </div>
        <div className="login-foot">
          <b>No seed phrase, ever.</b> Wallet sign-in is a signed message — no
          transaction, no gas. You can link an account later in Settings.
        </div>
      </>
    );
  if (view === "wallets")
    body = (
      <>
        <button
          className="backlink"
          style={{ alignSelf: "flex-start" }}
          onClick={() => setView("pick")}
        >
          <Icon.back /> Back
        </button>
        <h1>Choose a wallet</h1>
        <p className="sub">
          We'll ask it to sign a one-line message. Nothing is broadcast
          on-chain.
        </p>
        <div className="login-opts" style={{ textAlign: "left" }}>
          {WALLETS.map((w) => (
            <button
              key={w.id}
              className="mrow"
              style={{ marginBottom: 0 }}
              onClick={() => pickWallet(w)}
            >
              <span className="mrow__logo">{w.ini}</span>
              <span className="mrow__main">
                <span className="mrow__name">{w.name}</span>
                <span className="mrow__sub">{w.sub}</span>
              </span>
              <Icon.chevR />
            </button>
          ))}
        </div>
      </>
    );
  if (view === "connecting")
    body = (
      <div className="empty" style={{ padding: "30px 0" }}>
        <span
          className="spin"
          style={{ width: 24, height: 24, borderWidth: 2.5 }}
        />
        <h3 style={{ marginTop: 16 }}>Waiting for {wallet.name}…</h3>
        <p>
          Approve the connection request in{" "}
          {wallet.id === "walletconnect"
            ? "your mobile wallet"
            : wallet.id === "ledger"
              ? "your device"
              : "the extension"}
          .
        </p>
        <button
          className="cbtn cbtn--ghost cbtn--sm"
          style={{ marginTop: 18 }}
          onClick={() => cancelTo("wallets")}
        >
          Cancel
        </button>
      </div>
    );
  if (view === "werr")
    body = (
      <div className="empty" style={{ padding: "26px 0" }}>
        <span
          style={{
            color: "var(--ember)",
            fontSize: 26,
            display: "inline-flex",
          }}
        >
          <Icon.warn />
        </span>
        <h3 style={{ marginTop: 12 }}>
          No response from {wallet && wallet.name}
        </h3>
        <p style={{ maxWidth: 320 }}>
          {wallet && wallet.id === "ledger"
            ? "Check it’s plugged in, unlocked, and the Ethereum app is open."
            : wallet && wallet.id === "walletconnect"
              ? "The session expired before a wallet approved it."
              : "The request was dismissed or timed out."}{" "}
          Nothing was signed.
        </p>
        <div className="login-row" style={{ marginTop: 18 }}>
          <button className="cbtn" onClick={() => cancelTo("wallets")}>
            Choose another wallet
          </button>
          <button
            className="cbtn cbtn--pri"
            onClick={() => pickWallet(wallet, true)}
          >
            Try again
          </button>
        </div>
        <button
          className="backlink"
          style={{ marginTop: 14 }}
          onClick={() => cancelTo("pick")}
        >
          <Icon.back /> Back to sign-in
        </button>
      </div>
    );
  if (view === "sign")
    body = (
      <>
        <h1>Signature request</h1>
        <p className="sub">
          Signing proves you own this address. It never leaves your machine.
        </p>
        <div className="sig-addr">
          <span className="dotk" />
          <span className="a">{ADDR}</span>
          <span className="w">{wallet.name} · Base</span>
        </div>
        <div className="sig-msg">
          {"0xcopilot wants you to sign in\naddress: " +
            ADDR +
            "\nnonce: 88213 · issued: 2026-07-18\nno transaction · no gas"}
        </div>
        <div className="login-row">
          <button className="cbtn" onClick={() => setView("wallets")}>
            Cancel
          </button>
          <button className="cbtn cbtn--pri" onClick={enter}>
            <Icon.check /> Sign &amp; continue
          </button>
        </div>
      </>
    );
  if (view === "google")
    body = (
      <div className="empty" style={{ padding: "30px 0" }}>
        <span
          className="spin"
          style={{ width: 24, height: 24, borderWidth: 2.5 }}
        />
        <h3 style={{ marginTop: 16 }}>Authorizing with Google…</h3>
        <p>Finish signing in from the browser window that just opened.</p>
        <button
          className="backlink"
          style={{ marginTop: 18 }}
          onClick={() => cancelTo("pick")}
        >
          Cancel — use a different method
        </button>
      </div>
    );
  if (view === "gerr")
    body = (
      <div className="empty" style={{ padding: "26px 0" }}>
        <span
          style={{
            color: "var(--ember)",
            fontSize: 26,
            display: "inline-flex",
          }}
        >
          <Icon.warn />
        </span>
        <h3 style={{ marginTop: 12 }}>{"Google didn’t finish"}</h3>
        <p style={{ maxWidth: 320 }}>
          The browser window closed or timed out before confirming. No account
          was linked.
        </p>
        <div className="login-row" style={{ marginTop: 18 }}>
          <button className="cbtn" onClick={() => setView("wallets")}>
            Use a wallet instead
          </button>
          <button className="cbtn cbtn--pri" onClick={google}>
            Try again
          </button>
        </div>
        <button
          className="backlink"
          style={{ marginTop: 14 }}
          onClick={() => cancelTo("pick")}
        >
          <Icon.back /> Back to sign-in
        </button>
      </div>
    );
  if (view === "done")
    body = (
      <div className="empty" style={{ padding: "30px 0" }}>
        <span style={{ color: "var(--jade)", display: "inline-flex" }}>
          <svg
            viewBox="0 0 24 24"
            width="30"
            height="30"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.7"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="M5 12l5 5L20 7" />
          </svg>
        </span>
        <h3 style={{ marginTop: 12 }}>Signed in</h3>
        <p>Opening your workspace…</p>
      </div>
    );

  return (
    <div className="stage">
      <div className="mw" data-theme="dark" style={{ height: 640, width: 960 }}>
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
            — sign in
          </div>
        </div>
        <div className="login scroll">
          <div className="login-card" data-screen-label="Login" key={view}>
            <div className="login-mark">
              <Mark size={44} />
            </div>
            {body}
            {view === "pick" && (
              <div className="login-ver">
                <span>v2.1.0 · local build</span>
                <span>main</span>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

ReactDOM.createRoot(document.getElementById("app-root")).render(<LoginApp />);
