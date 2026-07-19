import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type ReactElement,
} from "react";

import {
  AssistantComposer,
  ComposerConnectorsButton,
  useTransport,
  type AssistantComposerPlusMenuSlotArgs,
  type CompleteAttachment,
} from "@0x-copilot/chat-surface";
import type { McpServer, Skill } from "@0x-copilot/api-types";

import {
  buildModelCatalog,
  defaultSelectedModelId,
  modelSelectionForId,
  type CatalogModel,
} from "./desktopModelCatalog";
import { createDesktopAttachmentAdapter } from "./desktopAttachmentAdapter";
import { DesktopAnchoredPlusMenu } from "./DesktopAnchoredPlusMenu";
import { DesktopComposerFilePicker } from "./DesktopComposerFilePicker";
import {
  mcpServerInstructionPrompt,
  skillInstructionPrompt,
} from "./composerPrompts";

// Substrate-bound singletons — one hidden-input file picker and one
// single-stage attachment adapter per renderer. Both are stateless, so a module
// singleton is fine (mirrors the web adapter's `composerFilePicker`).
const filePicker = new DesktopComposerFilePicker();
const attachmentAdapter = createDesktopAttachmentAdapter();

export interface RunComposerProps {
  /** Conversation the run dispatch targets. */
  readonly conversationId: string;
  /**
   * Off-live (scrubbed) gate handed down by the Run cockpit through the
   * `renderComposer` seam. When true the composer is read-only — you can't send
   * into a past state (mirrors the base composer's ghost disable).
   */
  readonly disabled: boolean;
  /** Placeholder text (mirrors the cockpit's live/ghost copy). */
  readonly placeholder: string;
  /** Navigate to the Tools (connectors) surface — MCP + non-MCP visibility. */
  readonly onShowConnectors?: () => void;
  /** Navigate to the Skills surface. */
  readonly onOpenSkillsSettings?: () => void;
  /** Open Settings → Provider keys (BYOK model setup). */
  readonly onOpenModelSettings?: () => void;
}

interface SkillsResponse {
  readonly skills?: readonly Skill[];
}
interface McpServersResponse {
  readonly servers?: readonly McpServer[];
}
interface ProviderKeysResponse {
  readonly keys?: readonly { readonly provider?: string }[];
}
interface LocalModelsResponse {
  readonly models?: readonly { readonly name?: string }[];
}

/**
 * The desktop Run cockpit composer. Mounts the shared `AssistantComposer`
 * (@0x-copilot/chat-surface) — the SAME composer web uses — bound to desktop
 * substrate ports, closing the parity gap with web's Run composer:
 *
 *   - attachments   → `filePicker` + single-stage `attachmentAdapter`
 *   - `/` commands  → skills (`GET /v1/skills`) drive the `/`-menu + skill pills
 *   - connections   → MCP servers (`GET /v1/mcp/servers`) list in the `+` menu;
 *                     the connectors trigger opens the full Tools surface
 *                     (MCP + non-MCP)
 *   - model select  → curated cloud + local models, `depthVisible={false}`
 *
 * The cockpit still owns the ghost/scrub gate (passed in as `disabled`); this
 * component owns run dispatch (`onSubmit` → `POST /v1/agent/runs`, the same
 * endpoint the empty-state goal composer uses, identity derived server-side).
 */
export function RunComposer(props: RunComposerProps): ReactElement {
  const {
    conversationId,
    disabled,
    placeholder,
    onShowConnectors,
    onOpenSkillsSettings,
    onOpenModelSettings,
  } = props;

  const transport = useTransport();

  // --- Skills (drive `/`-menu + skill pills) ---
  const [skills, setSkills] = useState<readonly Skill[]>([]);
  const [skillsLoading, setSkillsLoading] = useState(true);
  const [selectedSkills, setSelectedSkills] = useState<readonly Skill[]>([]);

  // --- MCP servers (connections shown in the `+` menu) ---
  const [servers, setServers] = useState<readonly McpServer[]>([]);
  const [serversLoading, setServersLoading] = useState(true);

  // --- Model catalog inputs ---
  const [configuredProviders, setConfiguredProviders] = useState<
    ReadonlySet<string>
  >(new Set());
  const [providersKnown, setProvidersKnown] = useState(false);
  const [localModelNames, setLocalModelNames] = useState<readonly string[]>([]);
  const [customModels, setCustomModels] = useState<readonly CatalogModel[]>([]);
  const [selectedModel, setSelectedModel] = useState<string>("");

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
        const names = (res.models ?? [])
          .map((m) => m.name)
          .filter((n): n is string => typeof n === "string" && n.length > 0);
        setLocalModelNames(names);
      })
      .catch(() => {
        // Local models are optional/server-gated (404 when off) → empty list.
        if (!cancelled) setLocalModelNames([]);
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
    return [...base, ...customModels];
  }, [configuredProviders, providersKnown, localModelNames, customModels]);

  // Keep a valid selection: default to the first usable model, and re-resolve
  // if the current pick falls out of the list (e.g. a local model was removed).
  useEffect(() => {
    setSelectedModel((current) =>
      current !== "" && models.some((m) => m.id === current)
        ? current
        : defaultSelectedModelId(models),
    );
  }, [models]);

  const handleModelChange = useCallback((id: string): void => {
    setSelectedModel(id);
  }, []);

  const handleAddCustomModel = useCallback((slug: string): void => {
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

  const handleAttachSkill = useCallback((skill: Skill): void => {
    setSelectedSkills((current) =>
      current.some((s) => s.skill_id === skill.skill_id)
        ? current
        : [...current, skill],
    );
  }, []);
  const handleRemoveSkill = useCallback((skillId: string): void => {
    setSelectedSkills((current) =>
      current.filter((s) => s.skill_id !== skillId),
    );
  }, []);
  const handleClearSkills = useCallback((): void => {
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

  // "N active for this chat" — the count reflected on the connectors trigger.
  const activeConnectorCount = useMemo(
    () => servers.filter((s) => s.enabled).length,
    [servers],
  );

  const connectorsTrigger = (
    <ComposerConnectorsButton
      activeCount={activeConnectorCount}
      open={false}
      onClick={() => onShowConnectors?.()}
      disabled={disabled}
    />
  );

  const handleSubmit = useCallback(
    async ({
      text,
      attachments,
    }: {
      text: string;
      attachments: ReadonlyArray<unknown>;
    }): Promise<void> => {
      if (disabled) return;
      const trimmed = text.trim();
      const atts = attachments as ReadonlyArray<CompleteAttachment>;
      if (trimmed === "" && atts.length === 0) return;
      const model = modelSelectionForId(models, selectedModel);
      const body: Record<string, unknown> = {
        conversation_id: conversationId,
        user_input: text,
      };
      if (model !== null) {
        body.model = model;
      }
      if (atts.length > 0) {
        body.attachments = atts.map(toRunAttachment);
      }
      await transport.request({
        method: "POST",
        path: "/v1/agent/runs",
        body,
      });
    },
    [conversationId, disabled, models, selectedModel, transport],
  );

  return (
    <AssistantComposer
      connectors={{ servers: [...servers], loading: serversLoading }}
      skills={{ skills: [...skills], loading: skillsLoading }}
      attachmentAdapter={attachmentAdapter}
      filePicker={filePicker}
      renderPlusMenu={renderPlusMenu}
      skillInstructionPrompt={skillInstructionPrompt}
      mcpServerInstructionPrompt={mcpServerInstructionPrompt}
      onOpenMcpSettings={() => onShowConnectors?.()}
      onOpenSkillsSettings={() => onOpenSkillsSettings?.()}
      onShowConnectors={() => onShowConnectors?.()}
      selectedSkills={selectedSkills}
      onAttachSkill={handleAttachSkill}
      onRemoveSkill={handleRemoveSkill}
      onClearSkills={handleClearSkills}
      connectorsTrigger={connectorsTrigger}
      models={models}
      selectedModel={selectedModel}
      onModelChange={handleModelChange}
      onAddCustomModel={handleAddCustomModel}
      // The Fast/Balanced/Deep depth grid is intentionally hidden — the picker
      // is a model list (Cursor/Claude shape), not a depth toggle.
      depthVisible={false}
      onSubmit={handleSubmit}
      disabled={disabled}
      onOpenSkillsPanel={() => onOpenSkillsSettings?.()}
      // Surfaced for the "Set up your model" path; harmless if unused here.
      onOpenDetailsPanel={onOpenModelSettings ? () => undefined : undefined}
    />
  );
}

// --- Run-create wire mappers (CompleteAttachment → RunAttachmentRequest) ---

interface RunContentPartFile {
  readonly type: "file";
  readonly filename: string;
  readonly data: string;
  readonly mime_type: string;
}
type RunContentPartWire = RunContentPartFile | Record<string, unknown>;

function toRunContentPart(part: Record<string, unknown>): RunContentPartWire {
  if (part.type === "file") {
    return {
      type: "file",
      filename: String(part.name ?? ""),
      data: String(part.data ?? ""),
      mime_type: String(part.mime ?? ""),
    };
  }
  // Image parts (`{ type: "image", image }`) pass through unchanged.
  return { ...part };
}

function toRunAttachment(att: CompleteAttachment): Record<string, unknown> {
  return {
    id: att.id,
    type: att.type,
    name: att.name,
    content_type: att.type !== "" ? att.type : null,
    size: att.size ?? null,
    content: (att.content ?? []).map((part) =>
      toRunContentPart(part as Record<string, unknown>),
    ),
  };
}
