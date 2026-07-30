// useWebRunComposerTools — the web Run cockpit's shared composer bindings.
//
// The web analog of the desktop `useRunComposerBindings` (apps/* → apps/* is
// banned, so the two hosts duplicate the same wiring over the shared component
// contract). It owns the ONE source of truth for the web run composer's model
// catalog, the inline Tools popover (web-search + per-run connectors), the
// provider-keys port, and the run-start body builder — consumed by BOTH the
// empty-state composer (`RunEmptyComposer`, hero + chips) and the in-chat
// composer (`RunComposer`, turn-N). Extracting it (PRD web-convergence AD-3)
// guarantees the two web composers can never silently diverge on model / tools /
// connector-scope behaviour.
//
// Boundary: all substrate access goes through the same web ports the FTUE / empty
// composer already bind (live `GET /v1/agent/models` catalog via
// `useOnboardingComposerModels` — never a hardcoded list — plus the reused
// connectors port + provider-keys port). No `@0x-copilot/chat-surface` internals,
// no `apps/desktop` import, no raw fetch.

import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type ReactElement,
} from "react";

import {
  type ComposerConnectorsPort,
  type FirstRunInstallableConnector,
  type ProviderKeysPort,
  type RunStartRequest,
} from "@0x-copilot/chat-surface";

import type { RequestIdentity } from "../../api/config";
import { ChatToolsTrigger } from "../chat/components/composer/ChatToolsTrigger";
import { createComposerConnectorsPort } from "../connectors/composerConnectorsPort";
import { createFirstRunProviderKeysPort } from "../onboarding/firstRunProviderKeysPort";
import { toReadableRunAttachments } from "../onboarding/firstRunAttachments";
import {
  modelSelectionForId,
  useOnboardingComposerModels,
} from "../onboarding/useOnboardingComposerModels";

/** No-op for the composer's Settings deep-links the run cockpit surfaces elsewhere. */
function noop(): void {
  /* intentional no-op */
}

export interface WebRunComposerTools {
  /** Live model catalog + selection (the shared `AssistantComposer` model pill). */
  readonly models: ReturnType<typeof useOnboardingComposerModels>["models"];
  readonly selectedModel: string;
  readonly onModelChange: (id: string) => void;
  /** Host provider-keys port — the model pill's inline "Add a provider key" form. */
  readonly providerKeysPort: ProviderKeysPort;
  /** Run-scoped Tools pill + anchored popover. */
  readonly toolsTrigger: ReactElement;
  /**
   * Build the run-start body from the composer submit (goal + resolved model +
   * attachments + web-search + connector scopes). The ONE place both web
   * composers assemble a {@link RunStartRequest}, so they can't diverge.
   */
  readonly buildRunStartRequest: (input: {
    readonly text: string;
    readonly attachments: ReadonlyArray<unknown>;
  }) => RunStartRequest;
}

export function useWebRunComposerTools(
  identity: RequestIdentity,
  autoActivateConnectorId: string | null = null,
): WebRunComposerTools {
  // Live `/v1/agent/models` catalog (never a hardcoded list); no local download
  // in the run cockpit, so `localModelPct`/`modelName` stay null (BYOK/cloud).
  const { models, selectedModel, onModelChange } = useOnboardingComposerModels({
    identity,
    localModelPct: null,
    modelName: null,
  });

  // Tools pill state. Both knobs are opt-OUTS: web search defaults on, and every
  // connected connector is live unless the user paused it for this run. The old
  // opt-in `activeConnectorIds` started empty and nothing seeded it from the
  // server list, so a connected connector rendered disabled in the composer
  // while Settings → Tools reported it connected.
  const [webSearchEnabled, setWebSearchEnabled] = useState(true);
  const [pausedConnectorIds, setPausedConnectorIds] = useState<
    readonly string[]
  >([]);
  // Bumped when durable connector state moves (an OAuth return), so the pill
  // badge and the open panel refetch rather than showing a pre-connect world.
  const [reloadToken, setReloadToken] = useState(0);

  useEffect(() => {
    if (autoActivateConnectorId === null) return;
    setReloadToken((n) => n + 1);
  }, [autoActivateConnectorId]);

  // A connector that just came back from OAuth must not stay paused from
  // earlier in the session. Derived, not an effect — an effect would paint one
  // frame of the stale answer.
  const effectivePausedIds = useMemo<readonly string[]>(
    () =>
      autoActivateConnectorId === null
        ? pausedConnectorIds
        : pausedConnectorIds.filter((id) => id !== autoActivateConnectorId),
    [autoActivateConnectorId, pausedConnectorIds],
  );

  const connectorsPort = useMemo<ComposerConnectorsPort>(
    () => createComposerConnectorsPort(identity),
    [identity],
  );
  const providerKeysPort = useMemo<ProviderKeysPort>(
    () => createFirstRunProviderKeysPort(),
    [],
  );

  const onToggleConnector = useCallback(
    (serverId: string, active: boolean): void => {
      setPausedConnectorIds((prev) =>
        active
          ? prev.filter((id) => id !== serverId)
          : prev.includes(serverId)
            ? prev
            : [...prev, serverId],
      );
    },
    [],
  );

  const onConnectCatalog = useCallback(
    (entry: FirstRunInstallableConnector): void => {
      if (entry.requiresPreRegisteredClient) {
        // No custom-config overlay in the run cockpit; pre-registered vendors
        // connect from Settings → Tools.
        return;
      }
      void connectorsPort
        .installFromCatalog(entry.slug)
        .then((server) => connectorsPort.beginAuth(server.server_id))
        .catch(() => {
          // Workspace-authorize only; a failed install surfaces at run time via
          // the mcp_auth_required card.
        });
    },
    [connectorsPort],
  );

  const toolsTrigger = useMemo(
    () => (
      <ChatToolsTrigger
        port={connectorsPort}
        reloadToken={reloadToken}
        webSearchEnabled={webSearchEnabled}
        onToggleWebSearch={setWebSearchEnabled}
        pausedConnectorIds={effectivePausedIds}
        onToggleConnector={onToggleConnector}
        onConnectCatalog={onConnectCatalog}
        onAddCustom={noop}
      />
    ),
    [
      connectorsPort,
      reloadToken,
      webSearchEnabled,
      effectivePausedIds,
      onToggleConnector,
      onConnectCatalog,
    ],
  );

  const buildRunStartRequest = useCallback(
    (input: {
      readonly text: string;
      readonly attachments: ReadonlyArray<unknown>;
    }): RunStartRequest => {
      const runAttachments = toReadableRunAttachments(input.attachments);
      return {
        goal: input.text,
        model: modelSelectionForId(models, selectedModel),
        attachments: runAttachments.length > 0 ? runAttachments : undefined,
        webSearchEnabled,
        pausedConnectorIds: effectivePausedIds,
      };
    },
    [models, selectedModel, webSearchEnabled, effectivePausedIds],
  );

  return {
    models,
    selectedModel,
    onModelChange,
    providerKeysPort,
    toolsTrigger,
    buildRunStartRequest,
  };
}
