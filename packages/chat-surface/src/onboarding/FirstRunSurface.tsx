// FirstRunSurface — the shared 3-state FTUE gate surface (SPEC · PRD-P1 §3).
//
// Presentational SSOT mounted by BOTH hosts (desktop `FirstRunGate` binder, web
// `FirstRunRoute` binder) at the post–sign-in seam. It owns the state machine
//   stage ∈ {choice, dl, ready} + `sent`
// and the persistent chrome (top bar + footer). It performs NO I/O: BYOK save
// goes through the injected `ProviderKeysPort`; skip/complete are host
// callbacks; the local-download body (P2) and the real composer/ack (P3) are
// injected SLOTS. P1 ships internal placeholders so the machine + tests are
// complete without P2/P3.
//
// PRD-P8 D4 refines the machine: only an EXPLICIT gesture advances the stage.
// `ctx.onStartDownload` (a "Start download" click) starts the pull and advances,
// as before; `ctx.onContinue` (D4a's "Continue →") advances without restarting
// a pull; a download the local-model hook auto-started on runtime detection
// calls NEITHER, so `stage` stays "choice", the card stays mounted, and the
// runtime states ③ downloading / ④ stopped are reachable instead of flashing for
// one frame. §7's `localModelBlocked` keeps the composer/ack honest when the
// awaited model is not landing.
//
// SLOT CONTRACT (consumed by P2/P3 — keep stable):
//   • renderLocalCard(ctx: FirstRunLocalCardCtx)  — P2 replaces the Gate's
//        local `.fr-gcard` (curated preset + in-gate SSE progress). P8 adds
//        `ctx.onContinue`.
//   • renderComposer(ctx: FirstRunComposerCtx)    — P3 mounts AssistantComposer
//        for the `dl`/`ready` body. `ctx.modelReady` is the shared model-ready
//        signal (key → always true; local → localModelPct === 100). `onSent`
//        flips the surface to the acknowledgment.
//   • renderAcknowledgment(ctx: FirstRunAckCtx)   — P3's State C. `ctx.onComplete`
//        is the bound handoff (host markComplete + navigate); the slot decides
//        the ~1.5s timing. P1's placeholder fires it once on mount.
//
// Substrate-agnostic; colors resolve to design-system tokens (`onboarding.css`).

import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type ReactElement,
  type ReactNode,
} from "react";

import { Button } from "@0x-copilot/design-system";

import { BrandMark } from "../shell/BrandMark";
import type { ProviderKeysPort } from "../settings/data/providerKeys";
import type { ModelsPort } from "../settings/data/models";
import { Gate, type FirstRunLocalCardCtx } from "./Gate";
import type { KeyFormConnected } from "./KeyForm";
import {
  firstRunAckAction,
  firstRunAckNote,
  firstRunAckTitle,
  type FirstRunAckState,
} from "./firstRunAckLines";
import {
  FIRST_RUN_COPY,
  type FirstRunEngine,
  type FirstRunKeyProvider,
  type FirstRunStage,
} from "./firstRun";
import { useConnectorTools } from "./useConnectorTools";
import type { ConnectorToolsHostPort } from "./ports/ConnectorToolsHostPort";
import type { FirstRunConnectorsPort } from "./ports/FirstRunConnectorsPort";
import type { FirstRunProfilePort } from "./ports/FirstRunProfilePort";
import type { FirstRunInstallableConnector } from "./projectFirstRunConnectors";
import {
  FirstRunProfileProvider,
  FirstRunWalletChip,
} from "./providers/FirstRunProfileProvider";

// ---------------------------------------------------------------------------
// Slot context types (P3 fills; P1 ships placeholders)
// ---------------------------------------------------------------------------

export type { FirstRunLocalCardCtx } from "./Gate";

export interface FirstRunComposerCtx {
  /** `dl` while the local model downloads; `ready` once an engine is usable. */
  readonly stage: Exclude<FirstRunStage, "choice">;
  readonly engine: FirstRunEngine;
  readonly models?: ModelsPort;
  /** P2 progress feed; drives the "Qwen 3 4B · N%" model pill. */
  readonly localModelPct: number | null;
  /** Shared model-ready signal: key → true; local → localModelPct === 100. */
  readonly modelReady: boolean;
  /**
   * P8 §7 — the awaited local model demonstrably is NOT landing (the hook's
   * `blocked !== null` / `runtime === "stopped"`, threaded in via
   * `localModelBlocked`). Hosts pass it straight to `useFirstRunLaunch`'s
   * `modelBlocked` so a send can't hang on "Queued" forever.
   */
  readonly modelBlocked: boolean;
  /** The composer calls this after run-create → surface renders the ack. */
  readonly onSent: () => void;
  // --- P4 tools wiring (present only when a `connectorsPort` is injected) ---
  /** Run-scoped Tools pill + anchored popover. */
  readonly toolsTrigger?: ReactNode;
  /**
   * Per-run web-search toggle at render time (SPEC `webOn`, default true). The
   * host threads this into `createFirstRun` on send.
   */
  readonly webSearchEnabled: boolean;
  /**
   * Connectors the user PAUSED for the run, or `undefined` when none are —
   * which is the default, because a connected connector is already callable.
   * Threaded into `createFirstRun` as `request_context.paused_connectors`.
   */
  readonly pausedConnectorIds?: readonly string[];
}

export interface FirstRunAckCtx {
  readonly engine: FirstRunEngine;
  readonly modelReady: boolean;
  /** P8 §7 — the awaited model is not landing; the ack must not claim it is. */
  readonly modelBlocked: boolean;
  /** Bound handoff — host markComplete + navigate. The slot owns the timing. */
  readonly onComplete: () => void;
  /**
   * P8 §7 — return to the composer (un-`sent`). The escape hatch that makes the
   * blocked launch phase actionable: the user gets their composer back and
   * `useFirstRunLaunch.launch()` now accepts the re-submit.
   */
  readonly onBack: () => void;
}

export interface FirstRunSurfaceProps {
  /** BYOK seam (required). */
  readonly providerKeys: ProviderKeysPort;
  /** /v1/agent/models catalog — NEVER a hardcoded model list. P3 uses it. */
  readonly models?: ModelsPort;
  /** Top-bar skip (host: markComplete("skip") + navigate to workspace). */
  readonly onSkip: () => void;
  /** Handoff (P3 does run-create first; P1 host = markComplete + navigate). */
  readonly onComplete: (engine: FirstRunEngine) => void;
  /**
   * P1 seam: an explicit wallet-chip node the host injects into the top bar.
   * P4 supersedes it — when `profilePort` is provided the surface renders its
   * own `FirstRunWalletChip` (fed by `useFirstRunProfile`) and this prop is
   * ignored. Kept for hosts that mount a pre-built chip without the provider.
   */
  readonly walletChipSlot?: ReactNode;
  /**
   * P4 — host-injected read of the signed-in identity (`GET /v1/me/profile`).
   * When provided, the surface wraps itself in a `FirstRunProfileProvider` and
   * fills the top-bar wallet slot with the connected `FirstRunWalletChip`
   * (renders nothing for email/Google accounts — SIWE-only).
   */
  readonly profilePort?: FirstRunProfilePort;
  /**
   * P4 — host-injected MCP connector surface for the composer Tools popover.
   * When provided, the surface owns `webOn` + `pausedConnectorIds` and mounts
   * the Tools pill beside the model selector. Absent ⇒ no per-run tools pill.
   */
  readonly connectorsPort?: FirstRunConnectorsPort;
  /**
   * P4 — host handler for a 1-click connect of a catalog entry (mirrors
   * `ChatScreen.onMcpInstallCatalog`; on desktop main opens the system browser
   * for OAuth). Defaults to a `connectorsPort`-driven install → `beginAuth`
   * when omitted.
   *
   * RETURN THE PROMISE when the host can tell that the connect FINISHED — the
   * surface refetches on it, and that is the only way the popover learns the
   * connector is connected. Resolving is the completion signal, so a host that
   * brokers OAuth out-of-process (desktop: main + the system browser) must not
   * resolve until the round-trip is done. Returning `void` is still honoured
   * and still means "I cannot report completion": correct for a host whose
   * connect navigates the whole document away (web full-page redirect), where
   * the remount does the refetch. Anything else leaves the panel showing a
   * pre-connect world until the app restarts.
   */
  readonly onConnectCatalog?: (
    entry: FirstRunInstallableConnector,
  ) => void | Promise<unknown>;
  /**
   * P4 — abort the connect in flight. Supplying it is what makes the Tools
   * popover render a Cancel beside the spinner, so a host that cannot really
   * stop its flow should omit it rather than pass a no-op: a Cancel that only
   * tidies the UI leaves the provider's tab live and the user misinformed.
   */
  readonly onCancelConnect?: () => void | Promise<unknown>;
  /**
   * P4 — host handler that opens the custom-MCP config form. Defaults to a
   * no-op (the inline paste-a-config form is a host concern). Also the routing
   * target for `requiresPreRegisteredClient` catalog rows.
   */
  readonly onAddCustom?: () => void;
  /** Footer left; default `FIRST_RUN_COPY.footer.left`. */
  readonly appVersion?: string;
  readonly keyProviders?: readonly FirstRunKeyProvider[];
  // --- Deferred-phase seams (optional; P1 ships internal placeholders) ---
  /**
   * P2: fired when the user EXPLICITLY starts the local download (→ stage=dl).
   * P8 D4: NOT fired by `ctx.onContinue`, and never by an auto-started pull.
   */
  readonly onStartLocalDownload?: () => void;
  /** P2: local download progress 0–100 (null before/without P2). */
  readonly localModelPct?: number | null;
  /**
   * P8 §6: the preset was already installed before any pull (the hook's
   * `modelInstalled`). A local engine is then ready with no pct at all — without
   * this the surface would report `modelReady: false` forever and a send would
   * queue behind a download that will never run.
   */
  readonly localModelInstalled?: boolean;
  /**
   * P8 §7: the awaited local model demonstrably is NOT landing — the hook's
   * `blocked !== null` or `runtime === "stopped"`. Surfaced on the composer/ack
   * ctx so neither keeps claiming a download is in flight.
   */
  readonly localModelBlocked?: boolean;
  /** P2: replaces the Gate's local card. */
  readonly renderLocalCard?: (ctx: FirstRunLocalCardCtx) => ReactNode;
  /** P3: the `dl`/`ready` composer body. */
  readonly renderComposer?: (ctx: FirstRunComposerCtx) => ReactNode;
  /** P3: State C acknowledgment. */
  readonly renderAcknowledgment?: (ctx: FirstRunAckCtx) => ReactNode;
  /** Tests only — seed the initial stage. */
  readonly initialStage?: FirstRunStage;
  /** P1 may disable the local download until P2's default preset lands. */
  readonly localDownloadDisabled?: boolean;
}

// ---------------------------------------------------------------------------
// P1 placeholders — replaced by P3's real composer / acknowledgment slots.
// ---------------------------------------------------------------------------

function ComposerPlaceholder({
  ctx,
}: {
  readonly ctx: FirstRunComposerCtx;
}): ReactElement {
  return (
    <div className="fr-slot" data-testid="first-run-composer-placeholder">
      <p className="fr-slot__note">
        {ctx.stage === "dl"
          ? "Your model is downloading — the composer lands in P3."
          : "Model ready — the composer lands in P3."}
      </p>
      <Button
        type="button"
        variant="primary"
        size="sm"
        onClick={ctx.onSent}
        data-testid="first-run-placeholder-send"
      >
        Continue
      </Button>
    </div>
  );
}

function AckPlaceholder({
  ctx,
}: {
  readonly ctx: FirstRunAckCtx;
}): ReactElement {
  // P8 §7 — "Queued — starts when the model lands" is only true while the model
  // still can land; a blocked download gets the honest stalled title instead.
  const ackState: FirstRunAckState = ctx.modelReady
    ? "starting"
    : ctx.modelBlocked
      ? "stalled"
      : "queued";
  const stalled = ackState === "stalled";
  const note = firstRunAckNote(ackState);
  const action = firstRunAckAction(ackState);

  // P1 hands off immediately (one-shot); P3 owns the real ack + ~1.5s timing.
  //
  // P8 §7 adds the one exception: a STALLED ack must not hand off. Completing
  // here would drop the user into the workspace on the strength of a run that
  // never started — the same lie as the old "Queued" title, just told by the
  // navigation instead of the copy. The effect re-runs if the model lands after
  // all (`stalled` flips false), so a recovery still completes with no gesture.
  const { onComplete, onBack } = ctx;
  useEffect(() => {
    if (stalled) return;
    onComplete();
  }, [stalled, onComplete]);

  return (
    <div className="fr-slot" data-testid="first-run-ack-placeholder">
      <p className="fr-slot__note" data-ack-state={ackState}>
        {firstRunAckTitle(ackState)}
      </p>
      {note !== null ? (
        <p className="fr-slot__note" data-testid="first-run-ack-note">
          {note}
        </p>
      ) : null}
      {/* Omitted-means-no-button, exactly as `FirstRunLocalCard` does it: only
       * the stalled state has an action, so only it renders a control. */}
      {action !== null ? (
        <Button
          type="button"
          variant="primary"
          size="sm"
          onClick={onBack}
          data-testid="first-run-ack-back"
        >
          {action}
        </Button>
      ) : null}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Shell + state machine
// ---------------------------------------------------------------------------

export function FirstRunSurface({
  providerKeys,
  models,
  onSkip,
  onComplete,
  walletChipSlot,
  profilePort,
  connectorsPort,
  onConnectCatalog,
  onCancelConnect,
  onAddCustom,
  appVersion,
  keyProviders,
  onStartLocalDownload,
  localModelPct = null,
  localModelInstalled = false,
  localModelBlocked = false,
  renderLocalCard,
  renderComposer,
  renderAcknowledgment,
  initialStage = "choice",
  localDownloadDisabled = false,
}: FirstRunSurfaceProps): ReactElement {
  const [stage, setStage] = useState<FirstRunStage>(initialStage);
  const [engine, setEngine] = useState<FirstRunEngine>(null);
  const [sent, setSent] = useState(false);
  // P4 — per-run Tools state. The FTUE holds `conn[]` as PAUSED ids because it
  // has no conversation to PATCH at toggle time, and both knobs default ON: a
  // connected connector is one the runtime can already call, so the toggle is
  // the opt-out, not the opt-in.
  //
  // The machine itself lives in `useConnectorTools` — shared with both Run
  // composers. It used to be re-implemented here, which is precisely how the
  // FTUE ended up missing the refetch-on-connect the desktop composer had.
  //
  // The host's connect verb is adapted, not re-specified: `onConnectCatalog`
  // stays the public prop (hosts already bind it), and the default is the
  // port-driven install → `beginAuth` for a host that supplies no override.
  const connectHost = useMemo<ConnectorToolsHostPort>(
    () => ({
      async connect(entry) {
        if (onConnectCatalog) {
          // `void` from a host means "I cannot report completion" — correct for
          // a full-page redirect, where the remount does the refresh.
          await onConnectCatalog(entry);
          return;
        }
        if (!connectorsPort) return;
        const server = await connectorsPort.installFromCatalog(entry.slug);
        await connectorsPort.beginAuth(server.server_id);
        return { serverId: server.server_id };
      },
      // Present only when the host gave one — the popover keys its Cancel
      // affordance off the verb existing, so an absent one honestly means
      // "this host cannot stop the flow".
      ...(onCancelConnect === undefined
        ? {}
        : {
            cancel: async () => {
              await onCancelConnect();
            },
          }),
    }),
    [onConnectCatalog, onCancelConnect, connectorsPort],
  );

  const {
    toolsTrigger,
    webSearchEnabled: webOn,
    pausedConnectorIds,
  } = useConnectorTools({
    port: connectorsPort,
    host: connectHost,
    onAddCustom,
  });

  // Paused connector ids → the run's `request_context.paused_connectors`, the
  // one field the runtime's MCP gate reads for a per-run opt-out. Omitted
  // entirely when nothing is paused, so a default run body carries no connector
  // payload and every connected connector stays available.
  const pausedConnectors = useMemo<readonly string[] | undefined>(
    () => (pausedConnectorIds.length === 0 ? undefined : pausedConnectorIds),
    [pausedConnectorIds],
  );

  // A local engine is usable once the pull reaches 100% — or immediately when
  // the preset was already installed (P8 §6's short-circuit issues no pull, so
  // `localModelPct` legitimately stays null).
  const localModelLanded = localModelPct === 100 || localModelInstalled;

  // The single "the user chose local, move them on" transition. `dl` vs `ready`
  // is derived, not assumed: continuing onto an already-landed model must not
  // park the composer in a downloading body it will never leave.
  const advanceToLocalComposer = useCallback((): void => {
    setEngine({ kind: "local", modelId: null });
    setStage(localModelLanded ? "ready" : "dl");
  }, [localModelLanded]);

  // Explicit "Start download" click — starts the pull AND advances (P8 D4).
  const handleStartDownload = useCallback(() => {
    advanceToLocalComposer();
    onStartLocalDownload?.();
  }, [advanceToLocalComposer, onStartLocalDownload]);

  const handleKeyConnected = useCallback((r: KeyFormConnected) => {
    setEngine({
      kind: "key",
      provider: r.provider,
      label: r.label,
      dotColor: r.dotColor,
      modelId: r.modelId,
    });
    setStage("ready");
  }, []);

  // Shared model-ready signal (completeness-critic cross-cutting seam): a BYOK
  // engine is ready the moment it connects; a local engine once the download
  // reaches 100% (P2 feeds `localModelPct`) or the preset was already installed
  // (P8 `localModelInstalled`).
  const modelReady = useMemo(() => {
    if (engine?.kind === "key") return true;
    if (engine?.kind === "local") return localModelLanded;
    return false;
  }, [engine, localModelLanded]);

  const composerCtx = useMemo<FirstRunComposerCtx>(
    () => ({
      stage: stage === "choice" ? "ready" : stage,
      engine,
      models,
      localModelPct,
      modelReady,
      modelBlocked: localModelBlocked,
      onSent: () => setSent(true),
      toolsTrigger,
      webSearchEnabled: webOn,
      pausedConnectorIds: pausedConnectors,
    }),
    [
      stage,
      engine,
      models,
      localModelPct,
      modelReady,
      localModelBlocked,
      toolsTrigger,
      webOn,
      pausedConnectors,
    ],
  );

  const ackCtx = useMemo<FirstRunAckCtx>(
    () => ({
      engine,
      modelReady,
      modelBlocked: localModelBlocked,
      onComplete: () => onComplete(engine),
      onBack: () => setSent(false),
    }),
    [engine, modelReady, localModelBlocked, onComplete],
  );

  let body: ReactNode;
  if (sent) {
    body = renderAcknowledgment ? (
      renderAcknowledgment(ackCtx)
    ) : (
      <AckPlaceholder ctx={ackCtx} />
    );
  } else if (stage === "choice") {
    body = (
      <>
        <div className="fr-hero">
          <h1 className="fr-hero__title">{FIRST_RUN_COPY.gate.h1}</h1>
          <p className="fr-hero__sub">{FIRST_RUN_COPY.gate.sub}</p>
        </div>
        <Gate
          keyPort={providerKeys}
          keyProviders={keyProviders}
          onStartDownload={handleStartDownload}
          onContinue={advanceToLocalComposer}
          onKeyConnected={handleKeyConnected}
          localDownloadDisabled={localDownloadDisabled}
          localModelPct={localModelPct}
          renderLocalCard={renderLocalCard}
        />
      </>
    );
  } else {
    // dl / ready → the composer body (P3 slot, else placeholder).
    body = renderComposer ? (
      renderComposer(composerCtx)
    ) : (
      <ComposerPlaceholder ctx={composerCtx} />
    );
  }

  // Wallet chip: P4's `profilePort` wins (connected `FirstRunWalletChip` under a
  // provider); else the P1 injected node. `resolvedWalletChip` is always a
  // defined element when either path is active, so the slot span renders.
  const resolvedWalletChip: ReactNode = profilePort ? (
    <FirstRunWalletChip />
  ) : (
    walletChipSlot
  );

  // Footer-right is engine-keyed (SPEC + design): the "keys in OS keychain"
  // line is only truthful once a BYOK *key* engine is chosen. The pre-choice
  // gate and the local (on-device) engine both promise "nothing leaves this
  // machine" — the design's gate default. Only a `key` engine shows the
  // keychain line.
  const footerRight =
    engine?.kind === "key"
      ? FIRST_RUN_COPY.footer.right
      : FIRST_RUN_COPY.footer.rightLocal;

  const surface = (
    <div className="fr" data-testid="first-run-surface">
      <header className="fr-top">
        <span className="fr-brand" data-testid="first-run-brand">
          <BrandMark size={18} />
          <span className="fr-brand__name">
            <span className="fr-brand__zx">
              {FIRST_RUN_COPY.topbar.brandLead}
            </span>
            {FIRST_RUN_COPY.topbar.brandRest}
          </span>
        </span>
        {resolvedWalletChip !== undefined ? (
          <span className="fr-top__chip" data-testid="first-run-wallet-slot">
            {resolvedWalletChip}
          </span>
        ) : null}
        <span className="fr-top__spacer" />
        <button
          type="button"
          className="fr-skiplink"
          onClick={onSkip}
          data-testid="first-run-skip"
        >
          {FIRST_RUN_COPY.topbar.skip}
        </button>
      </header>

      <main className="fr-main">{body}</main>

      <footer className="fr-foot" data-testid="first-run-footer">
        <span>{appVersion ?? FIRST_RUN_COPY.footer.left}</span>
        <span>{footerRight}</span>
      </footer>
    </div>
  );

  // When a profile port is injected, the whole surface reads the wallet-chip
  // identity from ONE `FirstRunProfileProvider` (fetched once) so both the chip
  // and any host chrome share the snapshot.
  return profilePort ? (
    <FirstRunProfileProvider port={profilePort}>
      {surface}
    </FirstRunProfileProvider>
  ) : (
    surface
  );
}
