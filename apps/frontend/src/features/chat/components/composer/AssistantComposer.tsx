import {
  AuiIf,
  ComposerPrimitive,
  unstable_useMentionAdapter,
  unstable_useSlashCommandAdapter,
  useAui,
  type Unstable_MentionCategory,
  type Unstable_SlashCommand,
} from "@assistant-ui/react";
import type { McpServer, Skill } from "@enterprise-search/api-types";
import { useEffect, useMemo, useRef, useState, type ReactElement } from "react";
import {
  mcpServerInstructionPrompt,
  skillInstructionPrompt,
} from "../../prompts";
import { AttachmentPill } from "./AttachmentPill";
import { ComposerPlusMenu, type ComposerMenuView } from "./ComposerPlusMenu";
import { TriggerPopoverList } from "./TriggerPopoverList";
import { fileAttachmentAccept } from "./fileAttachmentAccept";

export type DetailsPanelKind = "context" | "usage";

export function AssistantComposer({
  connectors,
  skills,
  onOpenMcpSettings,
  onOpenSkillsSettings,
  onShowConnectors,
  onOpenDetailsPanel,
}: {
  connectors: {
    servers: McpServer[];
    loading: boolean;
  };
  skills: {
    skills: Skill[];
    loading: boolean;
  };
  onOpenMcpSettings: () => void;
  onOpenSkillsSettings: () => void;
  onShowConnectors: () => void;
  onOpenDetailsPanel?: (kind: DetailsPanelKind) => void;
}): ReactElement {
  const aui = useAui();
  const menuRef = useRef<HTMLDivElement | null>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const [menuView, setMenuView] = useState<ComposerMenuView>("root");
  const slash = unstable_useSlashCommandAdapter({
    commands: useMemo<readonly Unstable_SlashCommand[]>(
      () => [
        {
          id: "context",
          label: "Context",
          description: "Show this conversation's token usage.",
          execute: () => onOpenDetailsPanel?.("context"),
        },
        {
          id: "usage",
          label: "Usage",
          description: "Show your token usage and cost.",
          execute: () => onOpenDetailsPanel?.("usage"),
        },
        {
          id: "summarize",
          label: "Summarize",
          description: "Summarize the conversation.",
          execute: () => undefined,
        },
        {
          id: "translate",
          label: "Translate",
          description: "Translate text to another language.",
          execute: () => undefined,
        },
        {
          id: "search",
          label: "Search",
          description: "Search connected context.",
          execute: () => undefined,
        },
        {
          id: "help",
          label: "Help",
          description: "List available commands.",
          execute: () => undefined,
        },
      ],
      [onOpenDetailsPanel],
    ),
    removeOnExecute: true,
  });
  const mention = unstable_useMentionAdapter({
    categories: useMemo<readonly Unstable_MentionCategory[]>(
      () => [
        {
          id: "context",
          label: "Context",
          items: [
            {
              id: "current-thread",
              type: "context",
              label: "Current thread",
              description: "Reference this conversation.",
            },
            {
              id: "connectors",
              type: "context",
              label: "Connectors",
              description: "Reference enabled connectors.",
            },
          ],
        },
      ],
      [],
    ),
    includeModelContextTools: true,
  });

  useEffect(() => {
    if (!menuOpen) {
      return;
    }
    function onPointerDown(event: PointerEvent): void {
      if (menuRef.current && !menuRef.current.contains(event.target as Node)) {
        setMenuOpen(false);
        setMenuView("root");
      }
    }
    document.addEventListener("pointerdown", onPointerDown);
    return () => document.removeEventListener("pointerdown", onPointerDown);
  }, [menuOpen]);

  function openFilePicker(accept: string): void {
    const input = document.createElement("input");
    input.type = "file";
    input.multiple = true;
    input.accept = accept;
    input.hidden = true;
    document.body.appendChild(input);
    input.onchange = () => {
      const files = input.files;
      if (files) {
        for (const file of files) {
          void aui.composer().addAttachment(file);
        }
      }
      input.remove();
      setMenuOpen(false);
      setMenuView("root");
    };
    input.oncancel = () => input.remove();
    input.click();
  }

  function appendComposerInstruction(text: string): void {
    const composer = aui.composer();
    const currentText = composer.getState().text.trimEnd();
    composer.setText(currentText ? `${currentText}\n${text}` : text);
    setMenuOpen(false);
    setMenuView("root");
  }

  return (
    <ComposerPrimitive.Unstable_TriggerPopoverRoot>
      <ComposerPrimitive.Root
        className="aui-composer"
        data-slot="aui_composer-shell"
      >
        <ComposerPrimitive.AttachmentDropzone className="aui-composer__dropzone">
          <ComposerPrimitive.Quote className="aui-quote-preview">
            <ComposerPrimitive.QuoteText />
            <ComposerPrimitive.QuoteDismiss
              className="aui-icon-button"
              aria-label="Remove quoted text"
              data-tooltip="Remove quoted text"
            >
              ×
            </ComposerPrimitive.QuoteDismiss>
          </ComposerPrimitive.Quote>
          <div className="aui-composer-attachments">
            <ComposerPrimitive.Attachments>
              {({ attachment }) => <AttachmentPill attachment={attachment} />}
            </ComposerPrimitive.Attachments>
          </div>
          <ComposerPrimitive.Input
            className="aui-composer__input"
            aria-label="Message"
            placeholder="Send a message... (@ to mention, / for commands)"
            minRows={1}
            maxRows={5}
            submitMode="enter"
          />
          <div className="aui-composer-action-wrapper">
            <div className="aui-plus-menu-root" ref={menuRef}>
              <button
                className="aui-icon-button aui-composer-add-attachment"
                type="button"
                aria-expanded={menuOpen}
                aria-haspopup="menu"
                aria-label="Open attachment and tools menu"
                data-tooltip="Add attachment"
                onClick={() => {
                  setMenuOpen((current) => !current);
                  setMenuView("root");
                }}
              >
                +
              </button>
              {menuOpen ? (
                <ComposerPlusMenu
                  view={menuView}
                  connectors={connectors}
                  skills={skills}
                  onBack={() => setMenuView("root")}
                  onAttachImage={() => openFilePicker("image/*")}
                  onAttachFile={() => openFilePicker(fileAttachmentAccept)}
                  onOpenMcp={() => setMenuView("mcp")}
                  onOpenSkills={() => setMenuView("skills")}
                  onOpenMcpSettings={onOpenMcpSettings}
                  onOpenSkillsSettings={onOpenSkillsSettings}
                  onShowConnectors={() => {
                    onShowConnectors();
                    setMenuOpen(false);
                    setMenuView("root");
                  }}
                  onUseMcpServer={(server) =>
                    appendComposerInstruction(
                      mcpServerInstructionPrompt(server.display_name),
                    )
                  }
                  onUseSkill={(skill) =>
                    appendComposerInstruction(
                      skillInstructionPrompt(skill.display_name),
                    )
                  }
                />
              ) : null}
            </div>
            <AuiIf condition={(state) => !state.thread.isRunning}>
              <ComposerPrimitive.Send
                className="aui-send-button aui-composer-send"
                aria-label="Send message"
                data-tooltip="Send message"
              >
                ↑
              </ComposerPrimitive.Send>
            </AuiIf>
            <AuiIf condition={(state) => state.thread.isRunning}>
              <ComposerPrimitive.Cancel
                className="aui-send-button aui-send-button--stop"
                aria-label="Stop response"
                data-tooltip="Stop response"
              >
                Stop
              </ComposerPrimitive.Cancel>
            </AuiIf>
          </div>
        </ComposerPrimitive.AttachmentDropzone>
        <ComposerPrimitive.Unstable_TriggerPopover
          char="/"
          adapter={slash.adapter}
          className="aui-trigger-popover"
        >
          <ComposerPrimitive.Unstable_TriggerPopover.Action {...slash.action} />
          <TriggerPopoverList />
        </ComposerPrimitive.Unstable_TriggerPopover>
        <ComposerPrimitive.Unstable_TriggerPopover
          char="@"
          adapter={mention.adapter}
          className="aui-trigger-popover"
        >
          <ComposerPrimitive.Unstable_TriggerPopover.Directive
            {...mention.directive}
          />
          <TriggerPopoverList />
        </ComposerPrimitive.Unstable_TriggerPopover>
      </ComposerPrimitive.Root>
    </ComposerPrimitive.Unstable_TriggerPopoverRoot>
  );
}
