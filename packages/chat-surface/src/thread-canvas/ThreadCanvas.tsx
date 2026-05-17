import type { CSSProperties, ReactElement } from "react";

import type { Transport } from "@enterprise-search/chat-transport";

import { TcSurfaceMount, type PendingDiffHandle } from "./TcSurfaceMount";
import { TcTabs, type TcTab } from "./TcTabs";

export interface ThreadCanvasProps {
  readonly conversationId: string;
  readonly tabs: readonly TcTab[];
  readonly activeUri: string;
  readonly onActivateTab: (uri: string) => void;
  readonly onCloseTab: (uri: string) => void;
  readonly transport: Transport;
  readonly onApprove?: (diffId: string) => void;
  readonly onReject?: (diffId: string) => void;
  readonly onSuggestChanges?: (diffId: string) => void;
  readonly pendingDiff?: PendingDiffHandle | null;
}

const gridStyle: CSSProperties = {
  display: "grid",
  gridTemplateColumns: "1fr 360px",
  gridTemplateRows: "1fr auto",
  gridTemplateAreas: '"canvas chat" "swimlanes swimlanes"',
  height: "100%",
  minHeight: 0,
  width: "100%",
  background: "#0e1015",
  color: "#f4f5f6",
  fontFamily:
    "ui-sans-serif, system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif",
};

const canvasColumnStyle: CSSProperties = {
  gridArea: "canvas",
  display: "flex",
  flexDirection: "column",
  minWidth: 0,
  minHeight: 0,
  borderRight: "1px solid #22252e",
  overflow: "hidden",
};

const canvasBodyStyle: CSSProperties = {
  flex: "1 1 auto",
  display: "flex",
  flexDirection: "column",
  minHeight: 0,
  padding: 16,
  overflow: "auto",
};

const chatSlotStyle: CSSProperties = {
  gridArea: "chat",
  display: "flex",
  flexDirection: "column",
  minWidth: 0,
  minHeight: 0,
  background: "#16181f",
  borderLeft: "1px solid #22252e",
  overflow: "hidden",
};

const swimlanesSlotStyle: CSSProperties = {
  gridArea: "swimlanes",
  borderTop: "1px solid #22252e",
  background: "#0e1015",
  minHeight: 48,
};

export function ThreadCanvas(props: ThreadCanvasProps): ReactElement {
  const {
    conversationId,
    tabs,
    activeUri,
    onActivateTab,
    onCloseTab,
    transport,
    onApprove,
    onReject,
    onSuggestChanges,
    pendingDiff,
  } = props;

  return (
    <div
      data-testid="thread-canvas"
      data-conversation-id={conversationId}
      style={gridStyle}
    >
      <div style={canvasColumnStyle}>
        <TcTabs
          tabs={tabs}
          activeUri={activeUri}
          onActivate={onActivateTab}
          onClose={onCloseTab}
        />
        <div style={canvasBodyStyle}>
          <TcSurfaceMount
            uri={activeUri}
            transport={transport}
            onApprove={onApprove}
            onReject={onReject}
            onSuggestChanges={onSuggestChanges}
            pendingDiff={pendingDiff}
          />
        </div>
      </div>
      <div data-testid="tc-chat-slot" style={chatSlotStyle} />
      <div data-testid="swimlanes-slot" style={swimlanesSlotStyle} />
    </div>
  );
}
