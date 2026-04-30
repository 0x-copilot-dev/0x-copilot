import { useEffect, useState } from "react";
import type { ReactElement } from "react";
import type { McpAuthRequiredEventPayload, McpServer, Skill, SkillScope } from "@enterprise-search/api-types";
import {
  createMcpServer,
  isMcpAuthRequiredPayload,
  listMcpServers,
  skipMcpAuth,
  startMcpAuth
} from "./mcpApi";
import {
  createSkill,
  DEFAULT_SKILL_MARKDOWN,
  deleteSkill,
  listSkills,
  updateSkill
} from "./skillsApi";
import "./styles.css";

const sampleAuthPayload: Record<string, unknown> = {
  server_id: "sample",
  server_name: "drive_mcp",
  display_name: "Drive MCP",
  auth_url: "https://mcp.example.com/oauth/authorize",
  expires_at: new Date(Date.now() + 15 * 60 * 1000).toISOString(),
  message: "The agent needs access to this MCP server before it can continue."
};

export default function App(): ReactElement {
  const [servers, setServers] = useState<McpServer[]>([]);
  const [skills, setSkills] = useState<Skill[]>([]);
  const [url, setUrl] = useState("");
  const [skillId, setSkillId] = useState<string | null>(null);
  const [skillMarkdown, setSkillMarkdown] = useState(DEFAULT_SKILL_MARKDOWN);
  const [skillEnabled, setSkillEnabled] = useState(true);
  const [skillScope, setSkillScope] = useState<SkillScope>("user");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void refreshServers();
    void refreshSkills();
  }, []);

  async function refreshServers(): Promise<void> {
    try {
      setServers(await listMcpServers());
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load MCP servers");
    }
  }

  async function onAddServer(): Promise<void> {
    if (!url.trim()) {
      return;
    }
    try {
      await createMcpServer(url.trim());
      setUrl("");
      await refreshServers();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not add MCP server");
    }
  }

  async function refreshSkills(): Promise<void> {
    try {
      setSkills(await listSkills());
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load Skills");
    }
  }

  async function onSaveSkill(): Promise<void> {
    try {
      if (skillId) {
        await updateSkill(skillId, skillMarkdown, skillEnabled, skillScope);
      } else {
        await createSkill(skillMarkdown, skillScope);
      }
      resetSkillForm();
      await refreshSkills();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save Skill");
    }
  }

  function onEditSkill(skill: Skill): void {
    setSkillId(skill.skill_id);
    setSkillMarkdown(skill.markdown);
    setSkillEnabled(skill.enabled);
    setSkillScope(skill.scope);
  }

  async function onDeleteSkill(skill: Skill): Promise<void> {
    try {
      await deleteSkill(skill.skill_id);
      if (skillId === skill.skill_id) {
        resetSkillForm();
      }
      await refreshSkills();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not delete Skill");
    }
  }

  function resetSkillForm(): void {
    setSkillId(null);
    setSkillMarkdown(DEFAULT_SKILL_MARKDOWN);
    setSkillEnabled(true);
    setSkillScope("user");
  }

  return (
    <main className="shell">
      <section className="hero">
        <p className="eyebrow">Enterprise Search</p>
        <h1>MCP Registry</h1>
        <p>
          Add MCP servers, choose whether to authenticate now or later, and let the
          agent request access from chat when it needs a skipped server.
        </p>
      </section>

      <section className="panel">
        <h2>Add MCP Server</h2>
        <div className="row">
          <input
            value={url}
            onChange={(event) => setUrl(event.target.value)}
            placeholder="https://mcp.example.com"
          />
          <button onClick={() => void onAddServer()}>Add server</button>
        </div>
        {error ? <p className="error">{error}</p> : null}
      </section>

      <section className="panel">
        <h2>Configured Servers</h2>
        <div className="server-list">
          {servers.map((server) => (
            <ServerCard key={server.server_id} server={server} onChanged={refreshServers} />
          ))}
          {servers.length === 0 ? <p className="muted">No MCP servers yet.</p> : null}
        </div>
      </section>

      <section className="panel">
        <h2>Skills Registry</h2>
        <p className="muted">
          Store user-created Agent Skills as Markdown. The agent sees compact
          Skill cards first and loads full Markdown only when needed.
        </p>
        <div className="skill-editor">
          <textarea
            value={skillMarkdown}
            onChange={(event) => setSkillMarkdown(event.target.value)}
            spellCheck={false}
          />
          <div className="row">
            <label className="check">
              <input
                type="checkbox"
                checked={skillEnabled}
                onChange={(event) => setSkillEnabled(event.target.checked)}
              />
              Enabled
            </label>
            <select
              value={skillScope}
              onChange={(event) => setSkillScope(event.target.value as SkillScope)}
            >
              <option value="user">User</option>
              <option value="org">Org</option>
            </select>
            <button onClick={() => void onSaveSkill()}>{skillId ? "Update Skill" : "Create Skill"}</button>
            {skillId ? (
              <button className="secondary" onClick={resetSkillForm}>
                New Skill
              </button>
            ) : null}
          </div>
        </div>
        <div className="server-list">
          {skills.map((skill) => (
            <SkillCard
              key={skill.skill_id}
              skill={skill}
              onEdit={onEditSkill}
              onDelete={(selected) => void onDeleteSkill(selected)}
            />
          ))}
          {skills.length === 0 ? <p className="muted">No Skills yet.</p> : null}
        </div>
      </section>

      <section className="panel chat-preview">
        <h2>Chat Auth Card</h2>
        {isMcpAuthRequiredPayload(sampleAuthPayload) ? (
          <McpAuthCard payload={sampleAuthPayload} />
        ) : null}
      </section>
    </main>
  );
}

function ServerCard({
  server,
  onChanged
}: {
  server: McpServer;
  onChanged: () => Promise<void>;
}): ReactElement {
  async function authenticate(): Promise<void> {
    const auth = await startMcpAuth(server.server_id);
    window.location.href = auth.auth_url;
  }

  async function skip(): Promise<void> {
    await skipMcpAuth(server.server_id);
    await onChanged();
  }

  return (
    <article className="server-card">
      <div>
        <h3>{server.display_name}</h3>
        <p>{server.url}</p>
      </div>
      <span className={`badge ${server.auth_state}`}>{server.auth_state}</span>
      <div className="actions">
        <button onClick={() => void authenticate()}>Authenticate</button>
        <button className="secondary" onClick={() => void skip()}>
          Skip for now
        </button>
      </div>
    </article>
  );
}

function McpAuthCard({ payload }: { payload: McpAuthRequiredEventPayload }): ReactElement {
  return (
    <article className="auth-card">
      <p className="eyebrow">Action needed</p>
      <h3>Authenticate {payload.display_name}</h3>
      <p>{payload.message}</p>
      <a href={payload.auth_url}>Authorize MCP server</a>
      <small>Link expires at {new Date(payload.expires_at).toLocaleString()}.</small>
    </article>
  );
}

function SkillCard({
  skill,
  onEdit,
  onDelete
}: {
  skill: Skill;
  onEdit: (skill: Skill) => void;
  onDelete: (skill: Skill) => void;
}): ReactElement {
  return (
    <article className="server-card skill-card">
      <div>
        <h3>{skill.display_name}</h3>
        <p>{skill.description}</p>
        <small>{skill.virtual_path}</small>
      </div>
      <span className={`badge ${skill.enabled ? "authenticated" : "auth_failed"}`}>
        {skill.enabled ? "enabled" : "disabled"}
      </span>
      <div className="actions">
        <button onClick={() => onEdit(skill)}>Edit</button>
        <button className="secondary" onClick={() => onDelete(skill)}>
          Delete
        </button>
      </div>
    </article>
  );
}
