#!/usr/bin/env node
// Journey J1 — Local-first (privacy). FTUE P7 verification. (JOURNEYS.md §J1)
//
//   sign in → gate → "Start download" → composer appears (model pill
//   "Qwen 3 4B") → type / pick a chip → send while the download is in flight
//   → ack "Queued — starts when the model lands" → (handoff to workspace).
//
// ASSERTED (runnable-shaped; a later "make it green" pass tightens these):
//   1. sign-in gate → "Use locally" → FTUE gate (State A) with the local card
//      + verbatim "First, give it a model." / "Download the local model".
//   2. "Start download" advances to State B: the composer ("What should we run
//      first?") with the on-device model pill leading ("Qwen 3 4B").
//   3. a starter chip fills the composer draft (watch-wallet).
//   4. Send with the download still in flight → State C ack titled
//      "Queued — starts when the model lands".
//
// BLOCKED-UNTIL (marked, not asserted — honest coverage):
//   • The Qwen 3 4B pull to 100% needs Ollama up + a ~4.3 GB download — not a
//     smoke-run step. The model-pill "· N%" progress text, the 100%→run-create,
//     and the streamed first run are therefore blocked here.
//   • The final workspace handoff after the real (post-100%) run-create.
//
// Hermetic: own userData subdir. Prereq: staged runtime
//   COPILOT_HOME=<dir containing runtime/<platform>-<arch>> \
//     node harness/journeys/first-run-j1-local-first.mjs

import {
  COPY,
  SEL,
  makeReport,
  makeRunDir,
  signInLocal,
  startDriver,
} from "./firstRunHarness.mjs";

const PORT = Number(process.env.CTL_PORT ?? "8792");
const runDir = makeRunDir("j1-local-first");
const subdir = `journey-j1-${Date.now()}`;
const rep = makeReport(runDir, "Journey J1 — Local-first (privacy)");

let result = "PASS";
let session = null;
try {
  session = await startDriver({ subdir, port: PORT, runDir });

  rep.log("## 1. Sign-in gate → Use locally → FTUE gate (State A)");
  await session.waitForSignIn();
  await signInLocal(session);
  await session.waitFor(SEL.firstRunSurface, session.timeouts.workspace);
  await session.waitFor(SEL.gate);
  await session.waitFor(SEL.localCard);
  const gateH1 = await session.evalJs(
    `document.querySelector('.fr-hero__title')?.textContent ?? ""`,
  );
  if (gateH1 !== COPY.gateH1) {
    throw new Error(
      `gate H1 reads ${JSON.stringify(gateH1)} (want ${JSON.stringify(COPY.gateH1)})`,
    );
  }
  await session.shot("state-a-gate");
  rep.pass("State A gate with local card + verbatim hero copy");

  rep.log('## 2. "Start download" → State B composer with the on-device model');
  await session.rpc("click", { selector: SEL.startDownload });
  await session.waitFor(SEL.composer);
  const composerH1 = await session.evalJs(
    `document.querySelector('[data-testid="first-run-composer-h1"]')?.textContent ?? ""`,
  );
  if (composerH1 !== COPY.composerH1) {
    throw new Error(`composer H1 reads ${JSON.stringify(composerH1)}`);
  }
  await session.waitFor(SEL.composerModelToggle);
  const pill = await session.evalJs(
    `document.querySelector('[data-testid="composer-model-toggle"]')?.textContent ?? ""`,
  );
  if (!pill.includes(COPY.modelPresetName)) {
    throw new Error(
      `model pill ${JSON.stringify(pill)} missing ${COPY.modelPresetName}`,
    );
  }
  await session.shot("state-b-composer");
  rep.pass('State B composer; model pill leads with "Qwen 3 4B"');
  // BLOCKED: the live "· N%" progress text needs a real Ollama pull in flight.
  rep.blocked('model-pill "· N%" progress text (needs a live Ollama pull)');

  rep.log("## 3. A starter chip fills the composer draft");
  await session.rpc("click", { selector: SEL.chipWatchWallet });
  const draft = await session.evalJs(
    `document.querySelector('[data-testid="composer-textarea"]')?.value ?? ""`,
  );
  if (draft.trim().length === 0) {
    throw new Error("chip pick did not populate the composer draft");
  }
  await session.shot("chip-filled");
  rep.pass("watch-wallet chip populated the draft");

  rep.log(
    '## 4. Send in-flight → State C ack "Queued — starts when the model lands"',
  );
  await session.rpc("click", { selector: SEL.composerSend });
  await session.waitFor(SEL.ack);
  const ackTitle = await session.evalJs(
    `document.querySelector('[data-testid="first-run-ack-title"]')?.textContent ?? ""`,
  );
  if (ackTitle !== COPY.ackQueued) {
    // The queued vs starting title depends on whether the download finished
    // first. In a smoke run the pull never completes, so "queued" is expected;
    // a "starting" title here means the model-ready gate flipped early.
    throw new Error(
      `ack title reads ${JSON.stringify(ackTitle)} (want ${JSON.stringify(COPY.ackQueued)})`,
    );
  }
  await session.shot("state-c-ack-queued");
  rep.pass('State C ack titled "Queued — starts when the model lands"');

  // BLOCKED: the 100% pull → run-create → stream → workspace handoff.
  rep.blocked(
    "download-to-100% → run-create → stream → workspace handoff (needs Ollama + full ~4.3 GB pull)",
  );
  result = "PARTIAL";
} catch (err) {
  result = "FAIL";
  rep.log(`\nRESULT: FAIL — ${err instanceof Error ? err.message : err}`);
  try {
    await session?.shot("failure");
  } catch {
    /* window may be gone */
  }
  process.exitCode = 1;
} finally {
  if (result !== "FAIL")
    rep.log(`\nRESULT: ${result} (blocked steps: ${rep.blockedCount})`);
  await session?.stop();
  rep.save(result);
}
