// Renders a clean macOS-style browser top bar (traffic lights + URL pill) to a
// PNG, for compositing a "browser window" around a captured page video.
import { chromium } from "playwright";

const OUT = process.env.OUT || "./topbar.png";
const URLTEXT = process.env.URLTEXT || "app.0xcopilot.tech";
const W = Number(process.env.W || "1600");
const HTML = `<!doctype html><html><head><style>
  html,body{margin:0;padding:0;background:transparent}
  .bar{height:60px;background:linear-gradient(#34343c,#2a2a30);display:flex;align-items:center;
       padding:0 18px;box-sizing:border-box;font-family:-apple-system,'SF Pro Text',sans-serif;
       border-radius:12px 12px 0 0}
  .lights{display:flex;gap:9px;width:120px}
  .l{width:13px;height:13px;border-radius:50%}
  .r{background:#ff5f57}.y{background:#febc2e}.g{background:#28c840}
  .url{flex:1;max-width:560px;background:#1c1c22;color:#c9c9d2;height:34px;border-radius:9px;
       display:flex;align-items:center;justify-content:center;gap:9px;font-size:15px}
  .lock{opacity:.55;font-size:13px}
  .spacer{width:120px}
</style></head><body>
  <div class="bar">
    <div class="lights"><div class="l r"></div><div class="l y"></div><div class="l g"></div></div>
    <div class="url"><span class="lock">&#128274;</span> ${URLTEXT}</div>
    <div class="spacer"></div>
  </div>
</body></html>`;

const b = await chromium.launch({ headless: true });
const p = await b.newPage({ viewport: { width: W, height: 60 }, deviceScaleFactor: 1 });
await p.setContent(HTML, { waitUntil: "load" });
await p.screenshot({ path: OUT });
await b.close();
console.log("TOPBAR=" + OUT);
process.exit(0);
