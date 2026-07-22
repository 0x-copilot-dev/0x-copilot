// First-Run onboarding (FTUE) — presentational SSOT data + port types (P1).
//
// This module is I/O-FREE: types, verbatim copy constants, and provider swatch
// data only. The gate surface never performs I/O directly — every side effect
// travels through a host-injected port (`ProviderKeysPort`, `ModelsPort`) or a
// host callback (`onSkip`/`onComplete`). Persistence of the first-run flag is a
// pure HOST concern behind the `FirstRunStore` port (owned by P0's binder).
//
// Design source of truth: docs/plan/first-run-onboarding/design-source/SPEC.md
// (§"Copy strings" is byte-verbatim here; §"Data" supplies the dot swatches).
//
// Substrate-agnostic: no bare `window`/`fetch`/`localStorage` (eslint-banned in
// this package). Colors resolve to design-system tokens in `onboarding.css`;
// the three per-provider dot hexes below are inline swatch DATA (SPEC §Data),
// never the app accent.

// ---------------------------------------------------------------------------
// State machine vocabulary (SPEC §"State machine")
// ---------------------------------------------------------------------------

/** The gate cycles `choice → dl → ready`; `sent` is a separate boolean flag. */
export type FirstRunStage = "choice" | "dl" | "ready";

/**
 * The chosen inference engine. `null` until the user picks one.
 *  - `local` — the on-device model (P2 resolves `modelId` from the pull).
 *  - `key`   — a BYOK provider (P1 KeyForm resolves it; `modelId` picked in P3).
 * `dotColor` is inline swatch DATA (SPEC §Data), not a design-system token.
 */
export type FirstRunEngine =
  | null
  | { readonly kind: "local"; readonly modelId: string | null }
  | {
      readonly kind: "key";
      readonly provider: string; // "anthropic" | "openai" | "openrouter"
      readonly label: string; // "Anthropic" …
      readonly dotColor: string; // inline swatch (data, NOT a token)
      readonly modelId: string | null; // resolved later from /v1/agent/models (P3)
    };

/** Why the gate was dismissed — the host persists this via `FirstRunStore`. */
export type FirstRunCompleteReason = "skip" | "sent" | "configured";

/**
 * The first-run completion flag seam — canonical port, shared by P0–P3.
 *
 * The HOST binder owns persistence (desktop: main-process `first-run.json` over
 * IPC; web: a `KeyValueStore` namespaced by user id). The presentational
 * surface NEVER calls this — the binder reads `isComplete()` to gate mounting
 * and calls `markComplete(reason)` on skip/complete. Defined here so every
 * phase imports ONE port shape rather than re-declaring it.
 */
export interface FirstRunStore {
  isComplete(): boolean | Promise<boolean>;
  markComplete(reason: FirstRunCompleteReason): void | Promise<void>;
}

/** BYOK provider row for the KeyForm tri-toggle (SPEC §Data). */
export interface FirstRunKeyProvider {
  readonly id: string; // ProviderKeyProvider slug
  readonly label: string;
  readonly meta: string; // e.g. "Claude Sonnet 4.5"
  readonly dotColor: string; // inline swatch value (SPEC §Data)
  readonly placeholder: string; // "sk-ant-…"
  readonly keyPrefix?: string; // client format-check hint
}

// SPEC §Data — dot colors are swatch data, not the app accent. (Anthropic's
// #d97757 coincidentally equals the design-system rust accent-theme value —
// it is kept as DATA here, never wired to `--color-accent`.)
export const FIRST_RUN_KEY_PROVIDERS: readonly FirstRunKeyProvider[] = [
  {
    id: "anthropic",
    label: "Anthropic",
    meta: "Claude Sonnet 4.5",
    dotColor: "#d97757",
    placeholder: "sk-ant-…",
    keyPrefix: "sk-ant-",
  },
  {
    id: "openai",
    label: "OpenAI",
    meta: "GPT-5.2",
    dotColor: "#6aa88f",
    placeholder: "sk-…",
    keyPrefix: "sk-",
  },
  {
    id: "openrouter",
    label: "OpenRouter",
    meta: "200+ models",
    dotColor: "#9a7fd6",
    placeholder: "sk-or-v1-…",
    keyPrefix: "sk-or-",
  },
];

/**
 * Verbatim copy (SPEC §"Copy strings"). One frozen object so tests pin it and
 * a single edit keeps the surface + tests in lock-step. Do NOT paraphrase — the
 * P7 ui-design-reviewer pass asserts byte-parity against the design source.
 */
export const FIRST_RUN_COPY = {
  gate: {
    h1: "First, give it a model.",
    sub: "The only required choice — switch anytime.",
  },
  local: {
    title: "Download the local model",
    // D5 (PRD-P8 §3) — FROZEN. The mock says 5.6 GB; 4.3 GB is the verified
    // Qwen3-4B Q8_0 size (4,280,404,704 B). Honesty beats parity; do not edit.
    meta: "Qwen 3 4B · 4.3 GB · free forever",
    body: "Runs on this machine. Nothing you send ever leaves it.",
    btn: "Start download",
    note: "type your first prompt while it downloads",
    // --- PRD-P8 §5 — the four runtime-state feet -------------------------
    // Design wording verbatim EXCEPT the two D4a deviations (`continueBtn`
    // and `downloadingNote`) and the three strings the design has no state
    // for (`unavailable`, `reconnecting`, `stoppedWatchUnmanaged`), each
    // flagged below so the design-parity report reads them as intent.
    /** Feature gated off (web/cloud) — unchanged from P2. */
    unavailable:
      "Local models run in the desktop app. Add a key to use a frontier model here.",
    /** ① not_installed / unknown — the host-brokered external open. */
    getOllama: "Get Ollama ↗",
    /** ① watch line: the card polls, so the user never has to re-check. */
    watchDetect: "download starts once it's detected",
    /** ① → detected: the runtime edge auto-starts the pull (PRD-P8 §6). */
    detected: "Ollama detected — starting your download",
    /** ② model already installed / pull finished — no redundant pull. */
    ready: "on-device · ready",
    /** ③ the `.dling` headline while the pull is in flight. */
    downloading: "Ollama detected — downloading now",
    /**
     * ③ note tail. DEVIATION D4a-2: the design reads "type your first prompt
     * while it lands", which would be a lie on the gate (there is no composer
     * there). The design's line is used verbatim once the user has advanced.
     */
    downloadingNote: "downloading in the background",
    /** ③ note tail while the stream is retrying (`transient`, PRD-P8 §6). */
    reconnecting: "reconnecting — nothing already downloaded is lost",
    /**
     * ③ action. DEVIATION D4a-1: the design's ③ foot has no button because
     * the mock has already moved the user to the composer. D4 keeps the card
     * mounted, so the user needs a way forward.
     */
    continueBtn: "Continue →",
    /** Accessible name for the ③ progress track (prefixes the model name). */
    progressLabel: "Downloading",
    /** ④ runtime stopped. */
    stopped: "Ollama stopped responding",
    /** ④ action — renders ONLY when `runtimeManaged` (PRD-P8 §5). */
    restart: "Restart Ollama",
    /** ④ watch line when this server can restart the runtime. */
    stoppedWatch: "download resumes on its own",
    /**
     * ④ watch line when it cannot (web / containerised). The design has no
     * unmanaged state; without it the degraded foot would tell the user the
     * download resumes without saying what has to happen first.
     */
    stoppedWatchUnmanaged:
      "start Ollama again — the download resumes on its own",
    /** ④ terminal-error action (`terminal` kind, no auto-retry). */
    resume: "Resume download",
    /**
     * A break the server did not classify, or one with no message at all (a
     * torn stream, a transport throw). Client-authored, so it lives here with
     * the rest of the card copy rather than as a literal in the hook.
     */
    interrupted: "Download interrupted.",
    /**
     * The `transient` retry budget is spent (PRD-P8 §6's cap). The auto-retry
     * lane has stopped, so this must NOT promise a reconnect — it says what is
     * true (the bytes are safe) and leaves the way out to `Resume download`.
     */
    retriesExhausted:
      "The download keeps dropping — nothing already downloaded is lost",
  },
  /**
   * PRD-P8 §7 — the acknowledgment's THIRD state.
   *
   * Only the new strings live here: `starting` / `queued` remain in
   * `FIRST_RUN_ACK_TITLES` (`Acknowledgment.tsx`), so no ack string ever has
   * two homes. `firstRunAckLines.ts` composes the two sources.
   */
  ack: {
    stalled: {
      /**
       * Replaces "Queued — starts when the model lands" when the model
       * demonstrably is NOT landing. It names the state (held), names the
       * reason (the download stopped), and promises nothing.
       */
      title: "Held — the model isn't downloading",
      /** Both real ways out, plus the reassurance that nothing was lost. */
      note: "Restart Ollama or add a key — your prompt is saved.",
      /** The action that makes the state actionable (`FirstRunAckCtx.onBack`). */
      action: "Back to the composer",
    },
  },
  key: {
    title: "Bring your own key",
    meta: "Anthropic · OpenAI · OpenRouter",
    body: "Frontier models, ready in ~30 seconds. Keys stay in your OS keychain.",
    btn: "Add a key",
  },
  keyForm: {
    placeholder: "sk-…  paste your API key",
    note: "stored in your OS keychain — never uploaded",
    btn: "Connect",
  },
  topbar: {
    brandLead: "0x",
    brandRest: "Copilot",
    skip: "skip — open the workspace →",
  },
  footer: {
    left: "v0.1.0 · local build",
    // Footer-right is engine-keyed (SPEC §Copy strings · parity-audit medium):
    // a BYOK/key engine surfaces `right`; a local (on-device) engine surfaces
    // `rightLocal`. The hosted-starter variant is SHELVED in v1.
    right: "keys in OS keychain · runs via your provider",
    rightLocal: "nothing leaves this machine",
  },
} as const;

/**
 * Pure client-side format check for a BYOK key against a `FirstRunKeyProvider`.
 *
 * Mirrors the documented server rules (packages/api-types/src/providerKeys.ts)
 * WITHOUT coupling the gate to the settings `ProviderCatalogEntry` shape: a
 * known prefix if the provider declares one, otherwise a permissive length
 * check (>= 20). This is a cheap pre-flight so an obviously malformed key never
 * reaches the network; the authoritative validation is the server's on `save`.
 * It NEVER stores the key — it returns a verdict only.
 */
export function checkFirstRunKeyFormat(
  provider: FirstRunKeyProvider,
  apiKey: string,
): { readonly ok: true } | { readonly ok: false; readonly error: string } {
  const trimmed = apiKey.trim();
  if (trimmed.length === 0) {
    return { ok: false, error: "Enter a key to continue." };
  }
  if (
    provider.keyPrefix !== undefined &&
    !trimmed.startsWith(provider.keyPrefix)
  ) {
    return {
      ok: false,
      error: `${provider.label} keys start with "${provider.keyPrefix}".`,
    };
  }
  if (trimmed.length < 20) {
    return {
      ok: false,
      error: "That key looks too short — check you pasted the whole value.",
    };
  }
  return { ok: true };
}
