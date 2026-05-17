import { AppIcon, Badge, Button, Card } from "@enterprise-search/design-system";
import {
  useEffect,
  useState,
  type CSSProperties,
  type KeyboardEvent,
  type ReactElement,
} from "react";

import { useRouter } from "../../providers/RouterProvider";
import { useTransport } from "../../providers/TransportProvider";
import type { ArtifactRoute } from "../../routing/router";

export type MemberRole = "owner" | "admin" | "member" | "guest";

export interface Member {
  readonly id: string;
  readonly name: string;
  readonly email: string;
  readonly role: MemberRole;
  readonly lastActiveIso: string;
  readonly workspaceId: string;
  readonly avatarColor?: string;
}

export interface TeamDestinationProps {
  readonly onInvite?: () => void;
}

interface MembersResponse {
  readonly members: readonly Member[];
}

type FetchState =
  | { readonly status: "loading" }
  | { readonly status: "error"; readonly message: string }
  | { readonly status: "ready"; readonly members: readonly Member[] };

const ROLE_LABEL: Record<MemberRole, string> = {
  owner: "Owner",
  admin: "Admin",
  member: "Member",
  guest: "Guest",
};

const ROLE_TONE: Record<
  MemberRole,
  "neutral" | "success" | "warning" | "danger" | "accent"
> = {
  owner: "accent",
  admin: "success",
  member: "neutral",
  guest: "warning",
};

const PANEL_BG = "#0E1015";
const PANEL_BORDER = "#22252E";
const TEXT_PRIMARY = "#E4E5E9";
const TEXT_SECONDARY = "#7E8492";
const ROW_HOVER = "rgba(123,155,255,0.08)";

const rootStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  height: "100%",
  overflow: "hidden",
  background: PANEL_BG,
  color: TEXT_PRIMARY,
};

const headerStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  padding: "1rem 1.5rem",
  borderBottom: `1px solid ${PANEL_BORDER}`,
  flex: "0 0 auto",
};

const titleStyle: CSSProperties = {
  margin: 0,
  fontSize: "var(--font-size-xl, 1.25rem)",
  fontWeight: "var(--font-weight-semibold, 600)",
  color: TEXT_PRIMARY,
};

const subtitleStyle: CSSProperties = {
  marginTop: "0.25rem",
  fontSize: "var(--font-size-sm, 0.875rem)",
  color: TEXT_SECONDARY,
};

const bodyStyle: CSSProperties = {
  flex: "1 1 auto",
  overflowY: "auto",
  padding: "1.5rem",
};

const tableStyle: CSSProperties = {
  width: "100%",
  borderCollapse: "separate",
  borderSpacing: 0,
  border: `1px solid ${PANEL_BORDER}`,
  borderRadius: "0.5rem",
  overflow: "hidden",
};

const theadCellStyle: CSSProperties = {
  textAlign: "left",
  padding: "0.625rem 0.875rem",
  background: "#14171E",
  borderBottom: `1px solid ${PANEL_BORDER}`,
  fontSize: "var(--font-size-xs, 0.75rem)",
  fontWeight: "var(--font-weight-medium, 500)",
  color: TEXT_SECONDARY,
  textTransform: "uppercase",
  letterSpacing: "0.05em",
};

const tbodyCellStyle: CSSProperties = {
  padding: "0.75rem 0.875rem",
  borderBottom: `1px solid ${PANEL_BORDER}`,
  fontSize: "var(--font-size-sm, 0.875rem)",
  color: TEXT_PRIMARY,
  verticalAlign: "middle",
};

const nameCellStyle: CSSProperties = {
  ...tbodyCellStyle,
  display: "flex",
  alignItems: "center",
  gap: "0.625rem",
};

const memberEmailStyle: CSSProperties = {
  color: TEXT_SECONDARY,
};

const lastActiveStyle: CSSProperties = {
  color: TEXT_SECONDARY,
  fontSize: "var(--font-size-xs, 0.75rem)",
};

const skeletonRowStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: "0.625rem",
  padding: "0.75rem 0.875rem",
  borderBottom: `1px solid ${PANEL_BORDER}`,
};

const skeletonBoxStyle: CSSProperties = {
  background: "#1A1D26",
  borderRadius: "0.25rem",
  height: "0.75rem",
};

const emptyCardStyle: CSSProperties = {
  textAlign: "center",
  padding: "2rem",
};

export function TeamDestination(props?: TeamDestinationProps): ReactElement {
  const transport = useTransport();
  const router = useRouter<ArtifactRoute>();
  const [state, setState] = useState<FetchState>({ status: "loading" });

  useEffect(() => {
    const controller = new AbortController();
    let cancelled = false;
    transport
      .request<MembersResponse>({
        method: "GET",
        path: "/v1/workspace/members",
        signal: controller.signal,
      })
      .then((res) => {
        if (cancelled) return;
        setState({ status: "ready", members: res.members ?? [] });
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        const message =
          err instanceof Error ? err.message : "Failed to load members";
        setState({ status: "error", message });
      });
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [transport]);

  const handleInvite = (): void => {
    props?.onInvite?.();
  };

  const handleRowActivate = (member: Member): void => {
    router.navigate({ kind: "workspace", workspaceId: member.workspaceId });
  };

  const handleRowKeyDown = (
    event: KeyboardEvent<HTMLTableRowElement>,
    member: Member,
  ): void => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      handleRowActivate(member);
    }
  };

  return (
    <div style={rootStyle} data-testid="team-destination">
      <header style={headerStyle}>
        <div>
          <h1 style={titleStyle}>Team</h1>
          <div style={subtitleStyle}>Workspace members and roles</div>
        </div>
        <Button variant="primary" size="md" onClick={handleInvite}>
          Invite
        </Button>
      </header>
      <div style={bodyStyle}>
        {renderBody(state, handleRowActivate, handleRowKeyDown)}
      </div>
    </div>
  );
}

function renderBody(
  state: FetchState,
  onActivate: (member: Member) => void,
  onKeyDown: (
    event: KeyboardEvent<HTMLTableRowElement>,
    member: Member,
  ) => void,
): ReactElement {
  if (state.status === "loading") {
    return renderSkeleton();
  }
  if (state.status === "error") {
    return (
      <Card tone="danger" data-testid="team-error">
        Failed to load workspace members: {state.message}
      </Card>
    );
  }
  if (state.members.length === 0) {
    return (
      <Card tone="muted" data-testid="team-empty" style={emptyCardStyle}>
        <div
          style={{
            fontSize: "var(--font-size-md, 1rem)",
            fontWeight: "var(--font-weight-medium, 500)",
            marginBottom: "0.25rem",
          }}
        >
          No members yet
        </div>
        <div
          style={{
            color: TEXT_SECONDARY,
            fontSize: "var(--font-size-sm, 0.875rem)",
          }}
        >
          Invite teammates to collaborate in this workspace.
        </div>
      </Card>
    );
  }
  return (
    <table style={tableStyle} data-testid="team-table">
      <thead>
        <tr>
          <th style={theadCellStyle} scope="col">
            Member
          </th>
          <th style={theadCellStyle} scope="col">
            Role
          </th>
          <th style={theadCellStyle} scope="col">
            Last active
          </th>
        </tr>
      </thead>
      <tbody>
        {state.members.map((member) => (
          <tr
            key={member.id}
            role="button"
            tabIndex={0}
            aria-label={`Open workspace for ${member.name}`}
            onClick={() => onActivate(member)}
            onKeyDown={(event) => onKeyDown(event, member)}
            style={{ cursor: "pointer" }}
            onMouseEnter={(event) => {
              event.currentTarget.style.background = ROW_HOVER;
            }}
            onMouseLeave={(event) => {
              event.currentTarget.style.background = "transparent";
            }}
          >
            <td style={nameCellStyle}>
              <AppIcon name={member.name} color={member.avatarColor} />
              <div style={{ display: "flex", flexDirection: "column" }}>
                <span>{member.name}</span>
                <span style={memberEmailStyle}>{member.email}</span>
              </div>
            </td>
            <td style={tbodyCellStyle}>
              <Badge tone={ROLE_TONE[member.role]}>
                {ROLE_LABEL[member.role]}
              </Badge>
            </td>
            <td style={tbodyCellStyle}>
              <span style={lastActiveStyle}>
                {formatRelative(member.lastActiveIso)}
              </span>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function renderSkeleton(): ReactElement {
  return (
    <div
      style={{
        border: `1px solid ${PANEL_BORDER}`,
        borderRadius: "0.5rem",
        overflow: "hidden",
      }}
      data-testid="team-loading"
    >
      {[0, 1, 2, 3].map((i) => (
        <div key={i} style={skeletonRowStyle}>
          <div
            style={{
              ...skeletonBoxStyle,
              width: "2rem",
              height: "2rem",
              borderRadius: "999px",
            }}
            aria-hidden="true"
          />
          <div
            style={{
              flex: 1,
              display: "flex",
              flexDirection: "column",
              gap: "0.375rem",
            }}
          >
            <div
              style={{ ...skeletonBoxStyle, width: "40%" }}
              aria-hidden="true"
            />
            <div
              style={{ ...skeletonBoxStyle, width: "60%" }}
              aria-hidden="true"
            />
          </div>
          <div
            style={{ ...skeletonBoxStyle, width: "4rem" }}
            aria-hidden="true"
          />
        </div>
      ))}
    </div>
  );
}

function formatRelative(iso: string): string {
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return "—";
  const deltaMs = Date.now() - t;
  if (deltaMs < 0) return "just now";
  const minutes = Math.floor(deltaMs / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days}d ago`;
  const weeks = Math.floor(days / 7);
  if (weeks < 4) return `${weeks}w ago`;
  const months = Math.floor(days / 30);
  if (months < 12) return `${months}mo ago`;
  const years = Math.floor(days / 365);
  return `${years}y ago`;
}
