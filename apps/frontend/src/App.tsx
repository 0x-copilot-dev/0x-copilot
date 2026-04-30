import { useEffect, useState } from "react";
import type { ReactElement } from "react";
import type { McpAuthRequiredEventPayload, McpServer } from "@enterprise-search/api-types";
import {
  createMcpServer,
  isMcpAuthRequiredPayload,
  listMcpServers,
  skipMcpAuth,
  startMcpAuth
} from "./mcpApi";
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
  const [url, setUrl] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void refreshServers();
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
