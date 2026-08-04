// useRunComposerBindings — the desktop Run cockpit's shared composer data.
//
// Both run-cockpit composers — the in-chat `RunComposer` (steer an active run)
// and the empty-state `RunEmptyComposer` (start the first run: the design's
// "What should we run first?" surface) — mount the SAME shared
// `AssistantComposer` and must show the SAME model catalog, skills, MCP servers,
// and `+`-menu. This hook is that single source: it loads all of it through the
// shell's `Transport` port and returns the normalized state + handlers, so the
// two composers never drift (a model added in Settings, a skill authored, a
// connector connected all reflect in both). The composers own only what differs:
// their submit target (in-chat POSTs directly; empty binds the fresh run via the
// cockpit seam) and their inline error surface.
//
// Extracted verbatim from `RunComposer` (behavior-preserving) — the model
// catalog seeding, the configured-provider gating, and the workspace-default
// synthesis are unchanged; `RunComposer.test.tsx` pins them.

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactElement,
  type ReactNode,
} from "react";

import {
  ProjectFilingChip,
  createComposerModelPreference,
  useKeyValueStore,
  useTransport,
  type AssistantComposerPlusMenuSlotArgs,
  type ProjectFilingOption,
} from "@0x-copilot/chat-surface";
import type {
  McpServer,
  ModelCatalogModel,
  ProjectId,
  Skill,
} from "@0x-copilot/api-types";

import {
  defaultSelectedModelId,
  mergeCatalog,
  type CatalogModel,
} from "./desktopModelCatalog";
import { DesktopAnchoredPlusMenu } from "./DesktopAnchoredPlusMenu";

interface SkillsResponse {
  readonly skills?: readonly Skill[];
}
interface McpServersResponse {
  readonly servers?: readonly McpServer[];
}
interface ModelCatalogResponseLite {
  readonly models?: readonly ModelCatalogModel[];
  // The backend's chosen default model id (env ∪ BYOK credential truth). The
  // selection fallback prefers it when usable so the picked model matches the
  // pill AND is one the run-create gate will accept — without it the fallback
  // was "first usable in list order" (or `models[0]`, a keyless row when nothing
  // scanned usable), which sent a model the backend can't run.
  readonly default_model_id?: string;
}
interface WorkspaceDefaultsResponseLite {
  readonly default_model?: {
    readonly provider?: string;
    readonly model_name?: string;
  } | null;
}
// `GET /v1/local-models` → `LocalModelSummary[]`. `size_bytes` is the on-disk
// weight size; the composer's model popover joins it onto the catalog by name
// so a local row reads "42 GB · never leaves this machine".
interface LocalModelsResponse {
  readonly models?: readonly {
    readonly name?: string;
    readonly size_bytes?: number;
  }[];
}

/**
 * Where this chat is filed, as the composer needs it: the current value, the
 * projects it can move to, and the write. Host-owned in every part — the list
 * is a facade read and the write is a `PATCH` (or, before the conversation
 * exists, a value held until create), neither of which a composer may perform.
 *
 * Omitted (or an empty `options`) ⇒ no filing zone at all, which is the correct
 * degradation for a user with no projects: a picker with nothing to pick.
 */
export interface RunComposerFiling {
  /** The chat's project, or `null` when unfiled / not yet chosen. */
  readonly value: ProjectId | null;
  readonly options: ReadonlyArray<ProjectFilingOption>;
  /** Fires with the picked project, or `null` for "No project". */
  readonly onChange: (next: ProjectId | null) => void;
  /** Read-only chrome (a write in flight). */
  readonly disabled?: boolean;
}

export interface RunComposerBindings {
  // --- Skills (drive `/`-menu + skill pills) ---
  readonly skills: readonly Skill[];
  readonly skillsLoading: boolean;
  readonly selectedSkills: readonly Skill[];
  readonly onAttachSkill: (skill: Skill) => void;
  readonly onRemoveSkill: (skillId: string) => void;
  readonly onClearSkills: () => void;

  // --- MCP servers (connections shown in the Tools trigger) ---
  readonly servers: readonly McpServer[];
  readonly serversLoading: boolean;
  /** "N active for this chat" — the count reflected on the connectors trigger. */
  readonly activeConnectorCount: number;

  // --- Model catalog (curated cloud + local + custom + workspace default) ---
  readonly models: CatalogModel[];
  readonly selectedModel: string;
  readonly onModelChange: (id: string) => void;
  readonly onAddCustomModel: (slug: string) => void;
  /**
   * Refetch the backend catalog and, when `preferProvider` is given, auto-select
   * that provider's first usable model. Called after a provider key is saved so
   * the just-configured provider's model is picked (its rows stop reading
   * "needs key") without a surface remount — the same seam the FTUE composer uses.
   */
  readonly refresh: (preferProvider?: string) => void;
  /**
   * On-disk size in bytes of each installed LOCAL model, keyed by its Ollama
   * tag — the `GET /v1/local-models` half of the join the model popover needs
   * for "42 GB · never leaves this machine". Empty when local models are off.
   */
  readonly localModelSizes: Readonly<Record<string, number>>;

  // --- Shared `+`-menu renderer (anchored popover) ---
  readonly renderPlusMenu: (
    args: AssistantComposerPlusMenuSlotArgs,
  ) => ReactElement;

  /**
   * The composer's "filed under [project]" zone, already bound — hand it
   * straight to `AssistantComposer.projectFilingSlot`. `undefined` whenever the
   * caller passed no `filing` or the user has no projects, which is what makes
   * the zone add no height rather than an empty row.
   */
  readonly projectFilingSlot: ReactNode;
}

/**
 * Load + own the shared desktop Run composer data (skills, MCP servers, model
 * catalog + selection, `+`-menu). The one writer for selection resolution keeps
 * the remembered-pick seed, the workspace-default seed and the keep-valid
 * fallback from racing each other's stale closures.
 *
 * `conversationId` scopes the remembered model pick. The outlet re-keys (and so
 * remounts) this binder per conversation, which is precisely why the pick has to
 * be persisted rather than held in state: without it, every navigation back into
 * a chat recomputed the auto-default and threw the user's choice away. `null` =
 * a brand-new chat, which has no id yet and therefore opens on the last-used
 * model instead of a per-chat one.
 *
 * `conversationModel` is the model this chat LAST RAN with, handed down from the
 * cockpit (server truth, derived from the run list it already fetched). It backs
 * the local memory: a chat opened on a second machine, or after the client store
 * was cleared, still opens on the model its transcript was actually produced
 * with instead of a generic default.
 *
 * `filing` is the project-filing binding. It lands here, next to `renderPlusMenu`,
 * because the filing menu is the SAME anchored popover as the `+` menu — same
 * slot shape, same portal, same outside-click — and this hook is where that one
 * renderer lives. Both composers then take the finished zone as one node.
 */
export function useRunComposerBindings(
  catalogRefreshKey = 0,
  conversationId: string | null = null,
  conversationModel: string | null = null,
  filing?: RunComposerFiling,
): RunComposerBindings {
  const transport = useTransport();
  const keyValueStore = useKeyValueStore();
  // The pill's memory. Stable per store, so the selection effect below can
  // depend on it without re-running on every render.
  const modelPreference = useMemo(
    () => createComposerModelPreference(keyValueStore),
    [keyValueStore],
  );

  // --- Skills ---
  const [skills, setSkills] = useState<readonly Skill[]>([]);
  const [skillsLoading, setSkillsLoading] = useState(true);
  const [selectedSkills, setSelectedSkills] = useState<readonly Skill[]>([]);

  // --- MCP servers ---
  const [servers, setServers] = useState<readonly McpServer[]>([]);
  const [serversLoading, setServersLoading] = useState(true);

  // --- Model catalog inputs ---
  const [cloudModels, setCloudModels] = useState<readonly ModelCatalogModel[]>(
    [],
  );
  const [defaultModelId, setDefaultModelId] = useState<string>("");
  const [localModelNames, setLocalModelNames] = useState<readonly string[]>([]);
  const [localModelSizes, setLocalModelSizes] = useState<
    Readonly<Record<string, number>>
  >({});
  const [customModels, setCustomModels] = useState<readonly CatalogModel[]>([]);
  const [selectedModel, setSelectedModel] = useState<string>("");
  const [workspaceDefault, setWorkspaceDefault] = useState<{
    readonly provider: string;
    readonly model_name: string;
  } | null>(null);
  // Seed-once guards: the persisted workspace default wins over the curated
  // fallback exactly once, and never over an explicit user pick.
  const seededDefaultRef = useRef(false);
  const userPickedRef = useRef(false);
  // Bumping this re-runs the catalog fetch. `refresh()` bumps it after a key is
  // saved so `configured` reflects the new BYOK key — the catalog was otherwise
  // fetched once at mount (before any key existed), so a just-added provider's
  // rows stayed "needs key" and its model could never be selected.
  const [reloadToken, setReloadToken] = useState(0);
  // The provider whose key was just added — steers the NEXT selection to that
  // provider's model (add OpenAI → GPT-5.4 Mini, not a leftover keyless pick).
  const preferProviderRef = useRef<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void transport
      .request<SkillsResponse>({ method: "GET", path: "/v1/skills" })
      .then((res) => {
        if (!cancelled) setSkills(res.skills ?? []);
      })
      .catch(() => {
        if (!cancelled) setSkills([]);
      })
      .finally(() => {
        if (!cancelled) setSkillsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [transport]);

  useEffect(() => {
    let cancelled = false;
    void transport
      .request<McpServersResponse>({ method: "GET", path: "/v1/mcp/servers" })
      .then((res) => {
        if (!cancelled) setServers(res.servers ?? []);
      })
      .catch(() => {
        if (!cancelled) setServers([]);
      })
      .finally(() => {
        if (!cancelled) setServersLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [transport]);

  useEffect(() => {
    let cancelled = false;
    void transport
      .request<ModelCatalogResponseLite>({
        method: "GET",
        path: "/v1/agent/models",
      })
      .then((res) => {
        if (cancelled) return;
        setCloudModels(res.models ?? []);
        setDefaultModelId(res.default_model_id ?? "");
      })
      .catch(() => {
        // Catalog probe failed → empty cloud list (a configured user's run-start
        // error is the backstop if the catalog was momentarily unreachable).
        if (!cancelled) setCloudModels([]);
      });
    return () => {
      cancelled = true;
    };
  }, [transport, reloadToken]);

  const refresh = useCallback((preferProvider?: string): void => {
    if (preferProvider !== undefined && preferProvider !== "") {
      preferProviderRef.current = preferProvider;
    }
    setReloadToken((token) => token + 1);
  }, []);

  // Provider-key setup happens in the Settings destination. The desktop keeps
  // the Run destination mounted while Settings is open so an in-progress draft
  // survives the round trip; this token is the corresponding data seam that
  // refreshes the already-mounted catalog once Settings confirms a key write.
  // Do not refetch on the initial mount: the normal catalog effect above is
  // already responsible for that first load.
  const seenCatalogRefreshKey = useRef(catalogRefreshKey);
  useEffect(() => {
    if (seenCatalogRefreshKey.current === catalogRefreshKey) return;
    seenCatalogRefreshKey.current = catalogRefreshKey;
    refresh();
  }, [catalogRefreshKey, refresh]);

  useEffect(() => {
    let cancelled = false;
    void transport
      .request<LocalModelsResponse>({ method: "GET", path: "/v1/local-models" })
      .then((res) => {
        if (cancelled) return;
        const rows = (res.models ?? []).filter(
          (m): m is { name: string; size_bytes?: number } =>
            typeof m.name === "string" && m.name.length > 0,
        );
        setLocalModelNames(rows.map((m) => m.name));
        // Same response, second projection: the name → bytes join the model
        // popover needs. Kept off `CatalogModel` on purpose — sizes come from a
        // different endpoint than the catalog, and `ModelCatalogModel` is a wire
        // contract.
        setLocalModelSizes(
          Object.fromEntries(
            rows
              .filter((m) => typeof m.size_bytes === "number")
              .map((m) => [m.name, m.size_bytes as number]),
          ),
        );
      })
      .catch(() => {
        // Local models are optional/server-gated (404 when off) → empty list.
        if (!cancelled) {
          setLocalModelNames([]);
          setLocalModelSizes({});
        }
      });
    return () => {
      cancelled = true;
    };
  }, [transport]);

  useEffect(() => {
    let cancelled = false;
    void transport
      .request<WorkspaceDefaultsResponseLite>({
        method: "GET",
        path: "/v1/agent/workspace/defaults",
      })
      .then((res) => {
        if (cancelled) return;
        const dm = res.default_model;
        if (
          dm &&
          typeof dm.provider === "string" &&
          dm.provider !== "" &&
          typeof dm.model_name === "string" &&
          dm.model_name !== ""
        ) {
          setWorkspaceDefault({
            provider: dm.provider,
            model_name: dm.model_name,
          });
        }
      })
      .catch(() => {
        // Defaults are optional — the curated fallback selection stands.
      });
    return () => {
      cancelled = true;
    };
  }, [transport]);

  const models = useMemo<CatalogModel[]>(() => {
    const base = mergeCatalog({ cloudModels, localModelNames });
    const merged = [...base, ...customModels];
    // The persisted workspace default may live outside the catalog (e.g. a pick
    // from the Add-key wizard's offline fallback list, which is written straight
    // to the workspace default). Surface it as a synthetic entry so it is visible
    // and selectable; it's configured when the catalog reports its provider as
    // usable (any configured cloud model of that provider).
    if (workspaceDefault !== null) {
      const listed = merged.some(
        (m) =>
          m.provider === workspaceDefault.provider &&
          m.model_name === workspaceDefault.model_name,
      );
      if (!listed) {
        const configured = base.some(
          (m) => m.provider === workspaceDefault.provider && m.configured,
        );
        merged.push({
          id: workspaceDefault.model_name,
          provider: workspaceDefault.provider,
          model_name: workspaceDefault.model_name,
          name: workspaceDefault.model_name,
          description: "Workspace default",
          configured,
          supports_streaming: true,
          disabled: !configured,
        });
      }
    }
    return merged;
  }, [cloudModels, localModelNames, customModels, workspaceDefault]);

  // Selection resolution — ONE writer so the seeds and the keep-valid fallback
  // cannot race each other's stale closures:
  //   0. a provider key was JUST added (`preferProviderRef`) → jump to that
  //      provider's model, overriding a stale keyless / wrong-provider pick;
  //   1. seed exactly once, most-specific first:
  //        a. this chat's remembered pick — an explicit choice made IN it;
  //        b. `conversationModel` — what this chat actually RAN with (server
  //           truth, so it works on a machine that never stored a pick);
  //        c. the last model picked in any chat — the sticky default.
  //      All three outrank the workspace default because each is a newer and
  //      more specific signal: Settings answers "what should a fresh install
  //      open on", these answer "what is this chat running".
  //   2. else seed the persisted workspace default exactly once, when
  //      present+usable;
  //   3. otherwise keep a valid current pick;
  //   4. otherwise fall back to the provider-aware default (backend
  //      `default_model_id` when usable, else the provider's best rung) — NOT a
  //      bare first-in-list pick, so the fallback matches the pill and is a model
  //      the run-create gate will accept.
  // Every seed is validated against the live catalog first: a remembered model
  // that was curated out, uninstalled, or lost its key falls through to the next
  // step rather than selecting something the run-create gate would reject.
  useEffect(() => {
    const prefer = preferProviderRef.current;
    if (prefer !== null) {
      const picked = defaultSelectedModelId(models, {
        preferProvider: prefer,
        defaultModelId,
      });
      if (picked === "") {
        // Preferred provider not usable yet — the refetch after the key save may
        // still be in flight. Keep the hint and wait for the next catalog update
        // rather than consuming it against a stale list.
        return;
      }
      preferProviderRef.current = null;
      // An explicit post-key selection counts as seeded, so a late
      // workspace-default fetch can't clobber the model the user just enabled.
      seededDefaultRef.current = true;
      setSelectedModel(picked);
      return;
    }
    setSelectedModel((current) => {
      if (!userPickedRef.current && !seededDefaultRef.current) {
        const usable = (id: string | null): string | null => {
          if (id === null) return null;
          // A remembered pick is a catalog id; `conversationModel` is a wire
          // `model_name` off a run row. Match either, since for most rows they
          // are the same string and for the rest only one of them can hit.
          const row = models.find((m) => m.id === id || m.model_name === id);
          return row && row.configured && row.disabled !== true ? row.id : null;
        };
        const remembered =
          usable(modelPreference.forConversation(conversationId)) ??
          usable(conversationModel) ??
          usable(modelPreference.lastUsed());
        if (remembered !== null) {
          seededDefaultRef.current = true;
          return remembered;
        }
        if (workspaceDefault !== null) {
          const dm = models.find(
            (m) =>
              m.provider === workspaceDefault.provider &&
              m.model_name === workspaceDefault.model_name,
          );
          // An unusable default (key removed since) stays unseeded so a later
          // configured flip can still seed it; the fallback keeps things honest.
          if (dm && dm.configured && dm.disabled !== true) {
            seededDefaultRef.current = true;
            return dm.id;
          }
        }
      }
      return current !== "" && models.some((m) => m.id === current)
        ? current
        : defaultSelectedModelId(models, { defaultModelId });
    });
  }, [
    models,
    workspaceDefault,
    defaultModelId,
    modelPreference,
    conversationId,
    conversationModel,
  ]);

  const onModelChange = useCallback(
    (id: string): void => {
      userPickedRef.current = true;
      setSelectedModel(id);
      // Persist immediately, not on send: the pick is the user's answer to
      // "what should this chat run on", and a pick they never send still has to
      // survive walking away from the destination.
      modelPreference.remember(id, conversationId);
    },
    [modelPreference, conversationId],
  );

  const onAddCustomModel = useCallback((slug: string): void => {
    const trimmed = slug.trim();
    if (trimmed === "") return;
    setCustomModels((prev) =>
      prev.some((m) => m.id === trimmed)
        ? prev
        : [
            ...prev,
            {
              id: trimmed,
              provider: "openrouter",
              model_name: trimmed,
              name: trimmed,
              description: "Custom OpenRouter model",
              configured: true,
              supports_streaming: true,
            },
          ],
    );
    setSelectedModel(trimmed);
  }, []);

  const onAttachSkill = useCallback((skill: Skill): void => {
    setSelectedSkills((current) =>
      current.some((s) => s.skill_id === skill.skill_id)
        ? current
        : [...current, skill],
    );
  }, []);
  const onRemoveSkill = useCallback((skillId: string): void => {
    setSelectedSkills((current) =>
      current.filter((s) => s.skill_id !== skillId),
    );
  }, []);
  const onClearSkills = useCallback((): void => {
    setSelectedSkills([]);
  }, []);

  const renderPlusMenu = useCallback(
    ({
      open,
      anchorRef,
      onDismiss,
      children,
    }: AssistantComposerPlusMenuSlotArgs): ReactElement => (
      <DesktopAnchoredPlusMenu
        open={open}
        anchorRef={anchorRef}
        onDismiss={onDismiss}
      >
        {children}
      </DesktopAnchoredPlusMenu>
    ),
    [],
  );

  const activeConnectorCount = useMemo(
    () => servers.filter((s) => s.enabled).length,
    [servers],
  );

  // The filing zone. `renderPlusMenu` is passed as the chip's `renderMenu`
  // unchanged — the two slot argument types are the same four fields, and one
  // anchored-popover implementation for both composer popovers is the point.
  const projectFilingSlot = useMemo<ReactNode>(() => {
    if (filing === undefined || filing.options.length === 0) return undefined;
    return (
      <ProjectFilingChip
        value={filing.value}
        options={filing.options}
        onChange={filing.onChange}
        disabled={filing.disabled}
        renderMenu={renderPlusMenu}
      />
    );
  }, [filing, renderPlusMenu]);

  return {
    skills,
    skillsLoading,
    selectedSkills,
    onAttachSkill,
    onRemoveSkill,
    onClearSkills,
    servers,
    serversLoading,
    activeConnectorCount,
    models,
    selectedModel,
    onModelChange,
    onAddCustomModel,
    refresh,
    localModelSizes,
    renderPlusMenu,
    projectFilingSlot,
  };
}
