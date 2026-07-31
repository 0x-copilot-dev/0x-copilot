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

import { useCallback, useMemo, type ReactNode } from "react";

import {
  useConnectorTools,
  type ComposerConnectorsPort,
  type ConnectorToolsHostPort,
  type ProviderKeysPort,
  type RunStartRequest,
} from "@0x-copilot/chat-surface";

import type { RequestIdentity } from "../../api/config";
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
  readonly toolsTrigger: ReactNode;
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

  const connectorsPort = useMemo<ComposerConnectorsPort>(
    () => createComposerConnectorsPort(identity),
    [identity],
  );
  const providerKeysPort = useMemo<ProviderKeysPort>(
    () => createFirstRunProviderKeysPort(),
    [],
  );

  // Tools state comes from the shared machine (web-search, paused ids, reload
  // token, connect lifecycle) — the same one the FTUE and the desktop composer
  // mount. Web binds only the connect verb.
  //
  // `beginAuth` here is a full-page redirect (`location.href = auth_url`), so
  // this promise never really resolves — the document unloads. That is fine and
  // is why the port allows it: nothing stays mounted to show a stale list, and
  // the remount on return re-reads everything. There is no cancel for the same
  // reason, so the host supplies none and the popover shows no Cancel.
  const host = useMemo<ConnectorToolsHostPort>(
    () => ({
      async connect(entry) {
        const server = await connectorsPort.installFromCatalog(entry.slug);
        await connectorsPort.beginAuth(server.server_id);
        return { serverId: server.server_id };
      },
    }),
    [connectorsPort],
  );

  const {
    toolsTrigger,
    webSearchEnabled,
    pausedConnectorIds: effectivePausedIds,
  } = useConnectorTools({
    port: connectorsPort,
    host,
    autoActivateConnectorId,
    // No custom-config overlay in the run cockpit; pre-registered vendors
    // connect from Settings → Tools.
    onAddCustom: noop,
  });

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
