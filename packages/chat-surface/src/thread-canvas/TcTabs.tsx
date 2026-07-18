import type {
  CSSProperties,
  KeyboardEvent,
  MouseEvent,
  ReactElement,
} from "react";

export interface TcTab {
  readonly uri: string;
  readonly title: string;
  readonly pinned?: boolean;
}

export interface TcTabsProps {
  readonly tabs: readonly TcTab[];
  readonly activeUri: string;
  readonly onActivate: (uri: string) => void;
  readonly onClose: (uri: string) => void;
}

const PALETTE = {
  lime: "var(--color-accent)",
  cardBg: "#181a1c",
  cardBorder: "#2a2d31",
  textHi: "#f4f5f6",
  textLo: "#9aa0a6",
} as const;

const stripStyle: CSSProperties = {
  display: "flex",
  flexDirection: "row",
  alignItems: "stretch",
  gap: 2,
  overflowX: "auto",
  overflowY: "hidden",
  borderBottom: `1px solid ${PALETTE.cardBorder}`,
  background: PALETTE.cardBg,
  padding: "0 8px",
  minHeight: 36,
  fontFamily:
    "ui-sans-serif, system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif",
  fontSize: "var(--font-size-xs)",
};

const baseTabStyle: CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: 6,
  padding: "8px 10px 6px 10px",
  borderBottom: "2px solid transparent",
  color: PALETTE.textLo,
  cursor: "pointer",
  whiteSpace: "nowrap",
  outline: "none",
};

const activeTabStyle: CSSProperties = {
  ...baseTabStyle,
  color: PALETTE.textHi,
  borderBottomColor: PALETTE.lime,
};

const titleStyle: CSSProperties = {
  display: "inline-block",
  maxWidth: 200,
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
};

const pinnedDotStyle: CSSProperties = {
  display: "inline-block",
  width: 6,
  height: 6,
  borderRadius: "50%",
  background: PALETTE.lime,
  flex: "0 0 auto",
};

const closeButtonStyle: CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  justifyContent: "center",
  width: 16,
  height: 16,
  padding: 0,
  marginLeft: 2,
  background: "transparent",
  border: "none",
  borderRadius: 3,
  color: PALETTE.textLo,
  cursor: "pointer",
  fontSize: "var(--font-size-xs)",
  lineHeight: 1,
};

export function TcTabs(props: TcTabsProps): ReactElement {
  const { tabs, activeUri, onActivate, onClose } = props;

  return (
    <div role="tablist" data-testid="tc-tabs" style={stripStyle}>
      {tabs.map((tab) => {
        const isActive = tab.uri === activeUri;
        const handleActivate = (): void => onActivate(tab.uri);
        const handleKeyDown = (event: KeyboardEvent<HTMLDivElement>): void => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            onActivate(tab.uri);
          }
        };
        const handleClose = (event: MouseEvent<HTMLButtonElement>): void => {
          event.stopPropagation();
          onClose(tab.uri);
        };
        return (
          <div
            key={tab.uri}
            role="tab"
            tabIndex={0}
            aria-selected={isActive}
            aria-current={isActive ? "page" : undefined}
            data-uri={tab.uri}
            data-active={isActive ? "true" : "false"}
            data-pinned={tab.pinned ? "true" : "false"}
            onClick={handleActivate}
            onKeyDown={handleKeyDown}
            style={isActive ? activeTabStyle : baseTabStyle}
          >
            {tab.pinned ? (
              <span aria-hidden="true" style={pinnedDotStyle} />
            ) : null}
            <span style={titleStyle}>{tab.title}</span>
            {tab.pinned ? null : (
              <button
                type="button"
                aria-label={`Close ${tab.title}`}
                data-testid={`tc-tabs-close-${tab.uri}`}
                onClick={handleClose}
                style={closeButtonStyle}
              >
                ×
              </button>
            )}
          </div>
        );
      })}
    </div>
  );
}
