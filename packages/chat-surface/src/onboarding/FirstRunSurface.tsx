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
  type CSSProperties,
  type ReactElement,
  type ReactNode,
} from "react";

import type { ConversationConnectorScopes } from "@0x-copilot/api-types";
import { Button } from "@0x-copilot/design-system";

import { BrandMark } from "../shell/BrandMark";
import type { ProviderKeysPort } from "../settings/data/providerKeys";
import type { ModelsPort } from "../settings/data/models";
import { Gate, type FirstRunLocalCardCtx } from "./Gate";
import type { KeyFormConnected } from "./KeyForm";
import { firstRunAckTitle, type FirstRunAckState } from "./firstRunAckLines";
import {
  FIRST_RUN_COPY,
  type FirstRunEngine,
  type FirstRunKeyProvider,
  type FirstRunStage,
} from "./firstRun";
import { ComposerToolsButton } from "./ComposerToolsButton";
import { ToolsPopover } from "./ToolsPopover";
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
  /**
   * The connector-aware Tools trigger (`ComposerToolsButton` + its
   * `ToolsPopover`) the host mounts into `AssistantComposer`'s `toolsTrigger`
   * slot. `undefined` when no `connectorsPort` was injected (the composer's
   * bottom bar then stays byte-identical to pre-P4).
   */
  readonly toolsTrigger?: ReactNode;
  /**
   * Per-run web-search toggle at render time (SPEC `webOn`, default true). The
   * host threads this into `createFirstRun` on send.
   */
  readonly webSearchEnabled: boolean;
  /**
   * Active connector scopes for the run (active ids → scopes), or `undefined`
   * when the user activated no connectors. Threaded into `createFirstRun`.
   */
  readonly connectorScopes?: ConversationConnectorScopes;
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
   * When provided, the surface owns `webOn` + `activeConnectorIds` and mounts
   * the `ComposerToolsButton` + `ToolsPopover` into the composer via the
   * composer ctx's `toolsTrigger`. Absent ⇒ no tools pill (pre-P4 composer).
   */
  readonly connectorsPort?: FirstRunConnectorsPort;
  /**
   * P4 — host handler for a 1-click connect of a catalog entry (mirrors
   * `ChatScreen.onMcpInstallCatalog`; on desktop main opens the system browser
   * for OAuth). Defaults to a `connectorsPort`-driven install → `beginAuth`
   * when omitted.
   */
  readonly onConnectCatalog?: (entry: FirstRunInstallableConnector) => void;
  /**
   * P4 — host handler that opens the custom-MCP config form. Defaults to a
   * no-op (the inline paste-a-config form is a host concern). Also the routing
   * target for `requiresPreRegisteredClient` catalog rows.
   */
  readonly onAddCustom?: () => void;
  /**
   * P4 — host-owned portal root for the Tools popover (the package has no
   * `document`). When omitted the popover renders inline, floated above the
   * trigger.
   */
  readonly toolsPortalTarget?: HTMLElement;
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
  // P1 hands off immediately (one-shot); P3 owns the real ack + ~1.5s timing.
  const { onComplete } = ctx;
  useEffect(() => {
    onComplete();
  }, [onComplete]);
  // P8 §7 — "Queued — starts when the model lands" is only true while the model
  // still can land; a blocked download gets the honest stalled title instead.
  const ackState: FirstRunAckState = ctx.modelReady
    ? "starting"
    : ctx.modelBlocked
      ? "stalled"
      : "queued";
  return (
    <div className="fr-slot" data-testid="first-run-ack-placeholder">
      <p className="fr-slot__note" data-ack-state={ackState}>
        {firstRunAckTitle(ackState)}
      </p>
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
  onAddCustom,
  toolsPortalTarget,
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
  // P4 — per-run Tools state owned by the surface (SPEC `webOn`, default true;
  // `conn[]` held as active connector ids since the FTUE has no conversation
  // to PATCH at toggle time).
  const [webOn, setWebOn] = useState(true);
  const [activeConnectorIds, setActiveConnectorIds] = useState<
    readonly string[]
  >([]);
  const [toolsOpen, setToolsOpen] = useState(false);

  const handleToggleConnector = useCallback(
    (serverId: string, active: boolean): void => {
      setActiveConnectorIds((prev) =>
        active
          ? prev.includes(serverId)
            ? prev
            : [...prev, serverId]
          : prev.filter((id) => id !== serverId),
      );
    },
    [],
  );

  // Default 1-click connect: mirror `ChatScreen.onMcpInstallCatalog` over the
  // injected port (install → begin OAuth). A pre-registered vendor routes to
  // the custom-config form (keyless install 422s). Hosts may override both via
  // `onConnectCatalog` / `onAddCustom` (desktop opens the system browser).
  const handleConnectCatalog = useCallback(
    (entry: FirstRunInstallableConnector): void => {
      if (onConnectCatalog) {
        onConnectCatalog(entry);
        return;
      }
      if (!connectorsPort) {
        return;
      }
      if (entry.requiresPreRegisteredClient) {
        onAddCustom?.();
        return;
      }
      void connectorsPort
        .installFromCatalog(entry.slug)
        .then((server) => connectorsPort.beginAuth(server.server_id))
        .catch(() => {
          // The popover's "connect" is workspace-authorize only; a failed
          // install surfaces later via the run-time `mcp_auth_required` card,
          // so a swallow here keeps the FTUE composer unblocked.
        });
    },
    [onConnectCatalog, onAddCustom, connectorsPort],
  );

  const handleAddCustom = useCallback((): void => {
    onAddCustom?.();
  }, [onAddCustom]);

  // Active connector ids → the run's `request_context.connector_scopes` (active
  // → `[]`, i.e. enabled with no extra scopes). Omitted entirely when nothing
  // is active so a default run body carries no connector-scope payload.
  const connectorScopes = useMemo<
    ConversationConnectorScopes | undefined
  >(() => {
    if (activeConnectorIds.length === 0) {
      return undefined;
    }
    const scopes: Record<string, readonly string[] | null> = {};
    for (const id of activeConnectorIds) {
      scopes[id] = [];
    }
    return scopes;
  }, [activeConnectorIds]);

  // The Tools trigger (button + popover) — built only when a connectors port is
  // injected, then handed to the composer via the composer ctx's `toolsTrigger`.
  const toolsTrigger = useMemo<ReactNode>(() => {
    if (!connectorsPort) {
      return undefined;
    }
    return (
      <FirstRunToolsTrigger
        port={connectorsPort}
        open={toolsOpen}
        onOpenChange={setToolsOpen}
        webSearchEnabled={webOn}
        onToggleWebSearch={setWebOn}
        activeConnectorIds={activeConnectorIds}
        onToggleConnector={handleToggleConnector}
        onConnectCatalog={handleConnectCatalog}
        onAddCustom={handleAddCustom}
        portalTarget={toolsPortalTarget}
      />
    );
  }, [
    connectorsPort,
    toolsOpen,
    webOn,
    activeConnectorIds,
    handleToggleConnector,
    handleConnectCatalog,
    handleAddCustom,
    toolsPortalTarget,
  ]);

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
      connectorScopes,
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
      connectorScopes,
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

// ---------------------------------------------------------------------------
// P4 Tools trigger — `ComposerToolsButton` + its `ToolsPopover`, floated above
// the button when no host portal target is supplied.
// ---------------------------------------------------------------------------

interface FirstRunToolsTriggerProps {
  readonly port: FirstRunConnectorsPort;
  readonly open: boolean;
  readonly onOpenChange: (open: boolean) => void;
  readonly webSearchEnabled: boolean;
  readonly onToggleWebSearch: (next: boolean) => void;
  readonly activeConnectorIds: readonly string[];
  readonly onToggleConnector: (serverId: string, active: boolean) => void;
  readonly onConnectCatalog: (entry: FirstRunInstallableConnector) => void;
  readonly onAddCustom: () => void;
  readonly portalTarget?: HTMLElement;
}

function FirstRunToolsTrigger(props: FirstRunToolsTriggerProps): ReactElement {
  const {
    port,
    open,
    onOpenChange,
    webSearchEnabled,
    onToggleWebSearch,
    activeConnectorIds,
    onToggleConnector,
    onConnectCatalog,
    onAddCustom,
    portalTarget,
  } = props;

  // Badge count from surface state alone (web search + toggled connectors —
  // each active id is by construction a connected row); the popover header
  // recomputes the exact count against the loaded projection.
  const activeCount = (webSearchEnabled ? 1 : 0) + activeConnectorIds.length;

  const popover = (
    <ToolsPopover
      open={open}
      onClose={() => onOpenChange(false)}
      port={port}
      webSearchEnabled={webSearchEnabled}
      onToggleWebSearch={onToggleWebSearch}
      activeConnectorIds={activeConnectorIds}
      onToggleConnector={onToggleConnector}
      onConnectCatalog={onConnectCatalog}
      onAddCustom={onAddCustom}
      portalTarget={portalTarget}
    />
  );

  return (
    <span style={triggerWrapStyle}>
      <ComposerToolsButton
        open={open}
        onClick={() => onOpenChange(!open)}
        activeCount={activeCount}
      />
      {portalTarget !== undefined ? (
        popover
      ) : (
        <span style={floatWrapStyle}>{popover}</span>
      )}
    </span>
  );
}

const triggerWrapStyle: CSSProperties = {
  position: "relative",
  display: "inline-flex",
};

// Inline (non-portaled) popover floats above the trigger, right-aligned, so it
// never widens the composer bottom bar.
const floatWrapStyle: CSSProperties = {
  position: "absolute",
  bottom: "calc(100% + 8px)",
  right: 0,
  zIndex: 50,
};
