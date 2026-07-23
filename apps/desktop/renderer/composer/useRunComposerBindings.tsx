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
} from "react";

import {
  useTransport,
  type AssistantComposerPlusMenuSlotArgs,
} from "@0x-copilot/chat-surface";
import type { McpServer, Skill } from "@0x-copilot/api-types";

import {
  buildModelCatalog,
  defaultSelectedModelId,
  type CatalogModel,
} from "./desktopModelCatalog";
import { DesktopAnchoredPlusMenu } from "./DesktopAnchoredPlusMenu";

interface SkillsResponse {
  readonly skills?: readonly Skill[];
}
interface McpServersResponse {
  readonly servers?: readonly McpServer[];
}
interface ProviderKeysResponse {
  readonly keys?: readonly { readonly provider?: string }[];
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

export interface RunComposerBindings {
  // --- Skills (drive `/`-menu + skill pills) ---
  readonly skills: readonly Skill[];
  readonly skillsLoading: boolean;
  readonly selectedSkills: readonly Skill[];
  readonly onAttachSkill: (skill: Skill) => void;
  readonly onRemoveSkill: (skillId: string) => void;
  readonly onClearSkills: () => void;

  // --- MCP servers (connections shown in the `+` menu / Tools trigger) ---
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
   * On-disk size in bytes of each installed LOCAL model, keyed by its Ollama
   * tag — the `GET /v1/local-models` half of the join the model popover needs
   * for "42 GB · never leaves this machine". Empty when local models are off.
   */
  readonly localModelSizes: Readonly<Record<string, number>>;

  // --- Shared `+`-menu renderer (anchored popover) ---
  readonly renderPlusMenu: (
    args: AssistantComposerPlusMenuSlotArgs,
  ) => ReactElement;
}

/**
 * Load + own the shared desktop Run composer data (skills, MCP servers, model
 * catalog + selection, `+`-menu). The one writer for selection resolution keeps
 * the workspace-default seed and the keep-valid fallback from racing each
 * other's stale closures (identical to the pre-extraction `RunComposer`).
 */
export function useRunComposerBindings(): RunComposerBindings {
  const transport = useTransport();

  // --- Skills ---
  const [skills, setSkills] = useState<readonly Skill[]>([]);
  const [skillsLoading, setSkillsLoading] = useState(true);
  const [selectedSkills, setSelectedSkills] = useState<readonly Skill[]>([]);

  // --- MCP servers ---
  const [servers, setServers] = useState<readonly McpServer[]>([]);
  const [serversLoading, setServersLoading] = useState(true);

  // --- Model catalog inputs ---
  const [configuredProviders, setConfiguredProviders] = useState<
    ReadonlySet<string>
  >(new Set());
  const [providersKnown, setProvidersKnown] = useState(false);
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
      .request<ProviderKeysResponse>({
        method: "GET",
        path: "/v1/settings/provider-keys",
      })
      .then((res) => {
        if (cancelled) return;
        const providers = new Set<string>();
        for (const key of res.keys ?? []) {
          if (key.provider) providers.add(key.provider);
          // The key store speaks `google`; the curated catalog (and the
          // runtime's model resolver) speak `gemini`. Alias so a Google key
          // actually lights up the Gemini rows.
          if (key.provider === "google") providers.add("gemini");
        }
        setConfiguredProviders(providers);
        setProvidersKnown(true);
      })
      .catch(() => {
        // Probe failed → leave `providersKnown` false so the catalog fails open
        // (a configured user is never blocked; run-start error is the backstop).
      });
    return () => {
      cancelled = true;
    };
  }, [transport]);

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
    const base = buildModelCatalog({
      configuredProviders,
      providersKnown,
      localModelNames,
    });
    const merged = [...base, ...customModels];
    // The persisted workspace default may live outside the curated set (e.g.
    // the Add-key wizard's gpt-4o). Surface it as a synthetic entry so it is
    // visible and selectable, with the same configured-gating as curated rows.
    if (workspaceDefault !== null) {
      const listed = merged.some(
        (m) =>
          m.provider === workspaceDefault.provider &&
          m.model_name === workspaceDefault.model_name,
      );
      if (!listed) {
        const configured =
          !providersKnown || configuredProviders.has(workspaceDefault.provider);
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
  }, [
    configuredProviders,
    providersKnown,
    localModelNames,
    customModels,
    workspaceDefault,
  ]);

  // Selection resolution — ONE writer so the workspace-default seed and the
  // keep-valid fallback cannot race each other's stale closures:
  //   1. seed the persisted default exactly once, when present and usable;
  //   2. otherwise keep a valid current pick;
  //   3. otherwise fall back to the first usable model.
  useEffect(() => {
    setSelectedModel((current) => {
      if (
        !userPickedRef.current &&
        !seededDefaultRef.current &&
        workspaceDefault !== null
      ) {
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
      return current !== "" && models.some((m) => m.id === current)
        ? current
        : defaultSelectedModelId(models);
    });
  }, [models, workspaceDefault]);

  const onModelChange = useCallback((id: string): void => {
    userPickedRef.current = true;
    setSelectedModel(id);
  }, []);

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
    localModelSizes,
    renderPlusMenu,
  };
}
