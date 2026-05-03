import type { McpServer, Skill } from "@enterprise-search/api-types";
import type { ReactElement } from "react";

export type ComposerMenuView = "root" | "mcp" | "skills";

export function ComposerPlusMenu({
  view,
  connectors,
  skills,
  onBack,
  onAttachImage,
  onAttachFile,
  onOpenMcp,
  onOpenSkills,
  onOpenMcpSettings,
  onOpenSkillsSettings,
  onShowConnectors,
  onUseMcpServer,
  onUseSkill,
}: {
  view: ComposerMenuView;
  connectors: {
    servers: McpServer[];
    loading: boolean;
  };
  skills: {
    skills: Skill[];
    loading: boolean;
  };
  onBack: () => void;
  onAttachImage: () => void;
  onAttachFile: () => void;
  onOpenMcp: () => void;
  onOpenSkills: () => void;
  onOpenMcpSettings: () => void;
  onOpenSkillsSettings: () => void;
  onShowConnectors: () => void;
  onUseMcpServer: (server: McpServer) => void;
  onUseSkill: (skill: Skill) => void;
}): ReactElement {
  const enabledSkills = skills.skills.filter((skill) => skill.enabled);

  if (view === "mcp") {
    return (
      <div className="aui-plus-menu" role="menu" aria-label="MCP server menu">
        <button
          className="aui-trigger-popover__back"
          type="button"
          title="Back to attachment and tools menu"
          onClick={onBack}
        >
          Back
        </button>
        <div className="aui-plus-menu__section">
          <strong>MCP Servers</strong>
          {connectors.loading ? (
            <span>Loading servers...</span>
          ) : connectors.servers.length === 0 ? (
            <span>No MCP servers configured.</span>
          ) : (
            connectors.servers.map((server) => (
              <button
                key={server.server_id}
                className="aui-trigger-popover__item"
                type="button"
                role="menuitem"
                title={`Use ${server.display_name} MCP server`}
                onClick={() => onUseMcpServer(server)}
              >
                <strong>{server.display_name}</strong>
                <span>
                  {server.enabled ? "Enabled" : "Disabled"} -{" "}
                  {server.auth_state.replaceAll("_", " ")}
                </span>
              </button>
            ))
          )}
        </div>
        <button
          className="aui-trigger-popover__item"
          type="button"
          role="menuitem"
          title="Show connector suggestions"
          onClick={onShowConnectors}
        >
          <strong>Show connector suggestions</strong>
          <span>Review servers that need authentication.</span>
        </button>
        <button
          className="aui-trigger-popover__item"
          type="button"
          role="menuitem"
          title="Open MCP settings"
          onClick={onOpenMcpSettings}
        >
          <strong>Open MCP settings</strong>
          <span>Manage connector auth and server configuration.</span>
        </button>
      </div>
    );
  }

  if (view === "skills") {
    return (
      <div className="aui-plus-menu" role="menu" aria-label="Skills menu">
        <button
          className="aui-trigger-popover__back"
          type="button"
          title="Back to attachment and tools menu"
          onClick={onBack}
        >
          Back
        </button>
        <div className="aui-plus-menu__section">
          <strong>Skills</strong>
          {skills.loading ? (
            <span>Loading skills...</span>
          ) : enabledSkills.length === 0 ? (
            <span>No enabled skills yet.</span>
          ) : (
            enabledSkills.map((skill) => (
              <button
                key={skill.skill_id}
                className="aui-trigger-popover__item"
                type="button"
                role="menuitem"
                title={`Use ${skill.display_name} skill`}
                onClick={() => onUseSkill(skill)}
              >
                <strong>{skill.display_name}</strong>
                <span>{skill.description || skill.name}</span>
              </button>
            ))
          )}
        </div>
        <button
          className="aui-trigger-popover__item"
          type="button"
          role="menuitem"
          title="Open skill settings"
          onClick={onOpenSkillsSettings}
        >
          <strong>Open skill settings</strong>
          <span>Manage available skills.</span>
        </button>
      </div>
    );
  }

  return (
    <div
      className="aui-plus-menu"
      role="menu"
      aria-label="Attachment and tools menu"
    >
      <button
        className="aui-trigger-popover__item"
        type="button"
        role="menuitem"
        title="Attach an image"
        onClick={onAttachImage}
      >
        <strong>Attach Image</strong>
        <span>Upload PNG, JPG, GIF, or WebP images.</span>
      </button>
      <button
        className="aui-trigger-popover__item"
        type="button"
        role="menuitem"
        title="Attach a file"
        onClick={onAttachFile}
      >
        <strong>Attach File</strong>
        <span>Upload PDF, DOCX, spreadsheets, slides, or text files.</span>
      </button>
      <button
        className="aui-trigger-popover__item"
        type="button"
        role="menuitem"
        title="Open MCP server menu"
        onClick={onOpenMcp}
      >
        <strong>MCP Servers</strong>
        <span>Choose an available server or open MCP settings.</span>
      </button>
      <button
        className="aui-trigger-popover__item"
        type="button"
        role="menuitem"
        title="Open skills menu"
        onClick={onOpenSkills}
      >
        <strong>Skills</strong>
        <span>Choose an enabled skill or open skill settings.</span>
      </button>
    </div>
  );
}
