// Web-substrate reference implementation of Router<ArtifactRoute | null>.
//
// Hash format: `#/{scheme}/{body}` where (scheme, body) round-trip through
// `buildArtifactUri` / `parseArtifactUri`. Unknown / malformed hashes
// resolve to null — silent defaulting masks routing bugs.
//
// Substrate touchpoints (`globalThis.window`, `globalThis.document`,
// `globalThis.location`) live here because this IS the web reference
// implementation of a port (same convention as LocalStorageKeyValueStore
// and DocumentPresenceSignal). The desktop substrate provides a different
// Router impl that fulfills the same contract.

import type { ArtifactRoute, NavigateOptions, Router } from "./router";
import { ARTIFACT_SCHEMES, type ArtifactScheme } from "./uri/schemes";
import { parseArtifactUri } from "./uri/parser";

export interface HashRouterConfig {
  readonly initialRoute?: ArtifactRoute | null;
}

type Listener = (route: ArtifactRoute | null) => void;

export class HashRouter implements Router<ArtifactRoute | null> {
  readonly #listeners = new Set<Listener>();
  #current: ArtifactRoute | null;
  #lastSerializedHash: string;
  readonly #hashChangeListener: () => void;

  constructor(config: HashRouterConfig = {}) {
    if (config.initialRoute !== undefined) {
      this.#current = config.initialRoute;
      const serialized = serializeRoute(config.initialRoute);
      this.#lastSerializedHash = serialized;
      const win = globalThis.window;
      if (win !== undefined) {
        win.location.hash = serialized;
      }
    } else {
      const raw = readHash();
      this.#current = parseHash(raw);
      this.#lastSerializedHash = serializeRoute(this.#current);
    }

    this.#hashChangeListener = () => {
      const next = parseHash(readHash());
      const serialized = serializeRoute(next);
      if (serialized === this.#lastSerializedHash) {
        // Dedup: this hashchange was triggered by our own navigate().
        // We already notified listeners synchronously in navigate.
        return;
      }
      this.#lastSerializedHash = serialized;
      this.#current = next;
      this.#emit();
    };

    const win = globalThis.window;
    if (win !== undefined) {
      win.addEventListener("hashchange", this.#hashChangeListener);
    }
  }

  current(): ArtifactRoute | null {
    return this.#current;
  }

  navigate(route: ArtifactRoute | null, opts: NavigateOptions = {}): void {
    const serialized = serializeRoute(route);
    this.#current = route;
    this.#lastSerializedHash = serialized;
    const win = globalThis.window;
    if (win !== undefined) {
      if (opts.replace === true) {
        const loc = win.location;
        const url = `${loc.pathname}${loc.search}${serialized}`;
        win.history.replaceState(win.history.state, "", url);
      } else {
        win.location.hash = serialized;
      }
    }
    this.#emit();
  }

  subscribe(listener: Listener): () => void {
    this.#listeners.add(listener);
    return () => {
      this.#listeners.delete(listener);
    };
  }

  /**
   * Detach the substrate listener. Tests use this to avoid cross-test
   * leaks; production callers typically keep the router for the app's
   * lifetime.
   */
  dispose(): void {
    const win = globalThis.window;
    if (win !== undefined) {
      win.removeEventListener("hashchange", this.#hashChangeListener);
    }
    this.#listeners.clear();
  }

  #emit(): void {
    for (const listener of this.#listeners) {
      listener(this.#current);
    }
  }
}

const KIND_TO_SCHEME: Readonly<Record<ArtifactRoute["kind"], ArtifactScheme>> =
  {
    chat: ARTIFACT_SCHEMES.chat,
    conversation: ARTIFACT_SCHEMES.conversation,
    run: ARTIFACT_SCHEMES.run,
    subagent: ARTIFACT_SCHEMES.subagent,
    "tool-result": ARTIFACT_SCHEMES.toolResult,
    mcp: ARTIFACT_SCHEMES.mcp,
    "mcp-tool": ARTIFACT_SCHEMES.mcpTool,
    skill: ARTIFACT_SCHEMES.skill,
    workspace: ARTIFACT_SCHEMES.workspace,
  };

function readHash(): string {
  const loc = globalThis.location;
  if (loc === undefined) {
    return "";
  }
  return loc.hash;
}

function parseHash(raw: string): ArtifactRoute | null {
  if (raw.length === 0) {
    return null;
  }
  // Strip leading "#" then leading "/" if present. Accept either
  // "#scheme/body" or "#/scheme/body".
  let body = raw.startsWith("#") ? raw.slice(1) : raw;
  if (body.startsWith("/")) {
    body = body.slice(1);
  }
  const firstSlash = body.indexOf("/");
  if (firstSlash <= 0) {
    return null;
  }
  const scheme = body.slice(0, firstSlash);
  const rest = body.slice(firstSlash + 1);
  if (rest.length === 0) {
    return null;
  }
  const parsed = parseArtifactUri(`${scheme}://${rest}`);
  if (parsed === null) {
    return null;
  }
  return fromParsed(parsed.scheme, parsed.body);
}

function fromParsed(
  scheme: ArtifactScheme,
  body: string,
): ArtifactRoute | null {
  switch (scheme) {
    case ARTIFACT_SCHEMES.chat:
      return { kind: "chat", conversationId: body };
    case ARTIFACT_SCHEMES.conversation:
      return { kind: "conversation", conversationId: body };
    case ARTIFACT_SCHEMES.run:
      return { kind: "run", runId: body };
    case ARTIFACT_SCHEMES.subagent: {
      const [runId, subagentId] = splitOnce(body, "/");
      if (subagentId === null) return null;
      return { kind: "subagent", runId, subagentId };
    }
    case ARTIFACT_SCHEMES.toolResult: {
      const [runId, stepId] = splitOnce(body, "/");
      if (stepId === null) return null;
      return { kind: "tool-result", runId, stepId };
    }
    case ARTIFACT_SCHEMES.mcp:
      return { kind: "mcp", serverId: body };
    case ARTIFACT_SCHEMES.mcpTool: {
      const [serverId, toolName] = splitOnce(body, "/");
      if (toolName === null) return null;
      return { kind: "mcp-tool", serverId, toolName };
    }
    case ARTIFACT_SCHEMES.skill:
      return { kind: "skill", skillId: body };
    case ARTIFACT_SCHEMES.workspace:
      return { kind: "workspace", workspaceId: body };
    default:
      // Schemes that exist in ARTIFACT_SCHEMES but aren't ArtifactRoute
      // kinds (email, sheet-row, sf-opp, slide, time-machine) are surface
      // URIs, not navigation routes. They resolve to null here.
      return null;
  }
}

function splitOnce(s: string, delim: string): readonly [string, string | null] {
  const idx = s.indexOf(delim);
  if (idx < 0) {
    return [s, null];
  }
  return [s.slice(0, idx), s.slice(idx + delim.length)];
}

function serializeRoute(route: ArtifactRoute | null): string {
  if (route === null) {
    return "";
  }
  const scheme = KIND_TO_SCHEME[route.kind];
  const body = serializeBody(route);
  if (body.length === 0) {
    // Route with an empty ID field is an invariant violation upstream;
    // surface it loudly rather than silently emitting an unparseable hash.
    throw new Error(
      `HashRouter: ArtifactRoute kind "${route.kind}" has empty body`,
    );
  }
  return `#/${scheme}/${body}`;
}

function serializeBody(route: ArtifactRoute): string {
  switch (route.kind) {
    case "chat":
    case "conversation":
      return route.conversationId;
    case "run":
      return route.runId;
    case "subagent":
      return `${route.runId}/${route.subagentId}`;
    case "tool-result":
      return `${route.runId}/${route.stepId}`;
    case "mcp":
      return route.serverId;
    case "mcp-tool":
      return `${route.serverId}/${route.toolName}`;
    case "skill":
      return route.skillId;
    case "workspace":
      return route.workspaceId;
  }
}
