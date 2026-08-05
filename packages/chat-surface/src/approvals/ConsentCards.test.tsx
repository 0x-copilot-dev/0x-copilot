// The design's consent surfaces: three approval shapes + the four-state
// connector card, plus the parsers that feed them.
//
// The assertions that matter most here are the negative ones. A consent card
// that omits a clause it cannot back is correct; one that fills the gap with a
// plausible default is the failure this whole path exists to prevent.

import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import { ConnectorConsentCard } from "./ConnectorConsentCard";
import {
  EMPTY_CONNECTOR_TRUST,
  accessLabel,
  parseApprovalPresentation,
  parseConnectorTrust,
  type ApprovalPresentation,
} from "./presentation";

const REASSURANCE =
  "You're always asked before Copilot acts outside this chat.";

function presentation(
  overrides: Partial<ApprovalPresentation> = {},
): ApprovalPresentation {
  return {
    layout: "params",
    approveLabel: null,
    rejectLabel: null,
    provenance: null,
    rows: [],
    preview: null,
    ...overrides,
  };
}

describe("ConnectorConsentCard — four states", () => {
  const LINEAR_TRUST = {
    accessMode: "read" as const,
    authHost: "linear.app",
    sourceTool: "suggest_mcp_connector",
  };

  it("asks with the full trust line and its provenance", () => {
    render(
      <ConnectorConsentCard
        displayName="Linear"
        purpose="to read LW-142's real dependencies"
        state="pending"
        trust={LINEAR_TRUST}
        testId="cc"
      />,
    );
    expect(screen.getByText("Connect Linear?")).toBeInTheDocument();
    expect(
      screen.getByText("Read-only · OAuth on linear.app · revoke anytime"),
    ).toBeInTheDocument();
    expect(screen.getByText("suggest_mcp_connector")).toBeInTheDocument();
    expect(screen.getByTestId("cc-deny")).toHaveTextContent("Deny");
  });

  it("omits the scope clause rather than guessing when the scope is unknown", () => {
    render(
      <ConnectorConsentCard
        displayName="Linear"
        purpose="to read issues"
        state="pending"
        trust={{ ...LINEAR_TRUST, accessMode: null }}
      />,
    );
    // The one sentence a consent card must never invent.
    expect(screen.queryByText(/Read-only/)).toBeNull();
    expect(
      screen.getByText("OAuth on linear.app · revoke anytime"),
    ).toBeInTheDocument();
  });

  it("omits the host clause when no auth session was issued", () => {
    render(
      <ConnectorConsentCard
        displayName="Linear"
        purpose="to read issues"
        state="pending"
        trust={{ ...LINEAR_TRUST, authHost: null }}
      />,
    );
    expect(screen.queryByText(/OAuth on/)).toBeNull();
    expect(screen.getByText("Read-only · revoke anytime")).toBeInTheDocument();
  });

  it("names the real host while waiting for sign-in", () => {
    render(
      <ConnectorConsentCard
        displayName="Linear"
        purpose={null}
        state="connecting"
        trust={LINEAR_TRUST}
      />,
    );
    expect(screen.getByText("Waiting for Linear sign-in…")).toBeInTheDocument();
    expect(
      screen.getByText(/A browser tab opened at linear\.app/),
    ).toBeInTheDocument();
    expect(screen.getByTestId("cc-cancel")).toBeInTheDocument();
  });

  it("confirms with a tool count when the host knows one", () => {
    render(
      <ConnectorConsentCard
        displayName="Linear"
        purpose={null}
        state="connected"
        trust={LINEAR_TRUST}
        toolCount={14}
      />,
    );
    expect(screen.getByText("Linear connected")).toBeInTheDocument();
    expect(
      screen.getByText("· 14 tools available to this run"),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("stays silent about tool count when the host has none", () => {
    // "0 tools available" after a successful connect reads as a failure.
    render(
      <ConnectorConsentCard
        displayName="Linear"
        purpose={null}
        state="connected"
        trust={LINEAR_TRUST}
        toolCount={0}
      />,
    );
    expect(screen.queryByText(/tools available/)).toBeNull();
  });

  it("keeps a declined connector visible and reversible", () => {
    const onReconsider = vi.fn();
    render(
      <ConnectorConsentCard
        displayName="Linear"
        purpose={null}
        state="denied"
        trust={LINEAR_TRUST}
        onReconsider={onReconsider}
      />,
    );
    expect(screen.getByText("Linear not connected")).toBeInTheDocument();
    expect(
      screen.getByText("· the run continues without it"),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("cc-reconsider"));
    expect(onReconsider).toHaveBeenCalled();
  });

  it("renders an inert gate when no port is wired, rather than hiding it", () => {
    render(
      <ConnectorConsentCard
        displayName="Linear"
        purpose="to read issues"
        state="pending"
        trust={LINEAR_TRUST}
        actionable={false}
      />,
    );
    expect(screen.getByTestId("cc-connect")).toBeDisabled();
    expect(screen.getByTestId("cc-deny")).toBeDisabled();
  });
});

describe("presentation parsers", () => {
  it("corrects a layout that has nothing to draw", () => {
    expect(parseApprovalPresentation({ layout: "rows", rows: [] })).toBeNull();
    expect(
      parseApprovalPresentation({ layout: "preview", approve_label: "Send" })
        ?.layout,
    ).toBe("params");
  });

  it("drops a row that cannot say who or how much", () => {
    const parsed = parseApprovalPresentation({
      layout: "rows",
      rows: [
        { label: "mira.eth", value: "10 USDC", row_id: "p1", decidable: true },
        { label: "orphan" },
        { value: "5 USDC" },
      ],
    });
    expect(parsed?.rows.map((r) => r.label)).toEqual(["mira.eth"]);
  });

  it("refuses a per-row decision with no id to send it under", () => {
    const parsed = parseApprovalPresentation({
      layout: "rows",
      rows: [{ label: "mira.eth", value: "10 USDC", decidable: true }],
    });
    expect(parsed?.rows[0]?.decidable).toBe(false);
  });

  it("returns null for junk rather than an all-default object", () => {
    expect(parseApprovalPresentation(null)).toBeNull();
    expect(parseApprovalPresentation("rows")).toBeNull();
    expect(parseApprovalPresentation({})).toBeNull();
  });

  it("reads only the scopes the server can actually mean", () => {
    expect(parseConnectorTrust({ access_mode: "read" }).accessMode).toBe(
      "read",
    );
    expect(parseConnectorTrust({ access_mode: "read_act" }).accessMode).toBe(
      "read_act",
    );
    // An unrecognised scope is not a scope.
    expect(parseConnectorTrust({ access_mode: "admin" }).accessMode).toBeNull();
    expect(parseConnectorTrust({ auth_host: "" }).authHost).toBeNull();
    expect(parseConnectorTrust(undefined)).toEqual(EMPTY_CONNECTOR_TRUST);
  });

  it("has no label for an unknown scope", () => {
    expect(accessLabel(EMPTY_CONNECTOR_TRUST)).toBeNull();
    expect(accessLabel({ ...EMPTY_CONNECTOR_TRUST, accessMode: "read" })).toBe(
      "Read-only",
    );
  });
});
