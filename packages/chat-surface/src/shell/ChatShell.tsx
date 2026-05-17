import type { Transport } from "@enterprise-search/chat-transport";
import {
  useEffect,
  useState,
  type CSSProperties,
  type ReactElement,
  type ReactNode,
} from "react";

import type { PresenceSignal } from "../presence/presence-signal";
import { KeyValueStoreProvider } from "../providers/KeyValueStoreProvider";
import { PresenceSignalProvider } from "../providers/PresenceSignalProvider";
import { RouterProvider, useRouter } from "../providers/RouterProvider";
import { TransportProvider } from "../providers/TransportProvider";
import type { ArtifactRoute, Router } from "../routing/router";
import type { KeyValueStore } from "../storage/key-value-store";

import { AppRail } from "./AppRail";
import { ContextPanel } from "./ContextPanel";
import {
  DEFAULT_SHELL_DESTINATION,
  type ShellDestinationSlug,
} from "./destinations";
import { RightRail } from "./RightRail";
import { Topbar } from "./Topbar";

const APP_BACKGROUND = "#0B0C10";
const MAIN_BACKGROUND = "#11141B";
const TEXT_PRIMARY = "#E4E5E9";
const TEXT_SECONDARY = "#7E8492";

function destinationFromRoute(
  route: ArtifactRoute | null,
): ShellDestinationSlug {
  if (route === null) return DEFAULT_SHELL_DESTINATION;
  switch (route.kind) {
    case "chat":
    case "conversation":
      return "chats";
    case "run":
    case "subagent":
    case "tool-result":
      return "agents";
    case "mcp":
    case "mcp-tool":
      return "connectors";
    case "skill":
      return "tools";
    case "workspace":
      return "team";
    default:
      return DEFAULT_SHELL_DESTINATION;
  }
}

function DestinationOutlet(): ReactElement {
  const router = useRouter<ArtifactRoute>();
  const [route, setRoute] = useState<ArtifactRoute | null>(() => {
    try {
      return router.current();
    } catch {
      return null;
    }
  });

  useEffect(() => {
    return router.subscribe((next) => setRoute(next));
  }, [router]);

  const slug = destinationFromRoute(route);

  let leaf = "—";
  if (route !== null) {
    if (route.kind === "chat" || route.kind === "conversation") {
      leaf = route.conversationId === "" ? "—" : route.conversationId;
    } else if (route.kind === "run") {
      leaf = route.runId;
    } else if (route.kind === "skill") {
      leaf = route.skillId;
    } else if (route.kind === "mcp") {
      leaf = route.serverId;
    } else if (route.kind === "workspace") {
      leaf = route.workspaceId;
    }
  }

  const stubStyle: CSSProperties = {
    padding: 24,
    color: TEXT_PRIMARY,
    fontSize: 14,
    fontFamily: "ui-monospace, SFMono-Regular, monospace",
  };
  const secondaryStyle: CSSProperties = {
    color: TEXT_SECONDARY,
    marginTop: 8,
    fontSize: 13,
  };

  return (
    <section
      aria-label={`${slug} destination`}
      data-testid="destination-outlet"
      data-destination={slug}
      style={stubStyle}
    >
      <div>
        {slug}: {leaf}
      </div>
      <div style={secondaryStyle}>
        Phase-3 destination content renders here.
      </div>
    </section>
  );
}

function ShellGrid({ children }: { children?: ReactNode }): ReactElement {
  const [rightOpen, setRightOpen] = useState(true);

  const outerStyle: CSSProperties = {
    width: "100%",
    height: "100%",
    minHeight: 0,
    backgroundColor: APP_BACKGROUND,
    color: TEXT_PRIMARY,
    display: "grid",
    gridTemplateColumns: rightOpen
      ? "52px 224px 1fr 380px"
      : "52px 224px 1fr 0",
    gridTemplateRows: "100%",
    boxSizing: "border-box",
  };
  const mainColumnStyle: CSSProperties = {
    display: "grid",
    gridTemplateRows: "44px 1fr",
    minHeight: 0,
    backgroundColor: MAIN_BACKGROUND,
  };
  const mainBodyStyle: CSSProperties = {
    minHeight: 0,
    overflow: "auto",
  };

  return (
    <div
      data-component="chat-shell"
      data-right-rail-open={rightOpen ? "open" : "closed"}
      style={outerStyle}
    >
      <AppRail />
      <ContextPanel />
      <div style={mainColumnStyle}>
        <Topbar />
        <div style={mainBodyStyle} data-testid="chat-shell-main">
          {children ?? <DestinationOutlet />}
        </div>
      </div>
      <RightRail open={rightOpen} onToggle={() => setRightOpen((v) => !v)} />
    </div>
  );
}

export interface ChatShellProps<TRoute> {
  readonly transport: Transport;
  readonly router: Router<TRoute>;
  readonly keyValueStore: KeyValueStore;
  readonly presenceSignal: PresenceSignal;
  readonly children?: ReactNode;
}

export function ChatShell<TRoute>({
  transport,
  router,
  keyValueStore,
  presenceSignal,
  children,
}: ChatShellProps<TRoute>): ReactElement {
  return (
    <TransportProvider transport={transport}>
      <RouterProvider router={router}>
        <KeyValueStoreProvider store={keyValueStore}>
          <PresenceSignalProvider signal={presenceSignal}>
            <ShellGrid>{children}</ShellGrid>
          </PresenceSignalProvider>
        </KeyValueStoreProvider>
      </RouterProvider>
    </TransportProvider>
  );
}
