// useConnectorTools — the shared Tools state machine.
//
// This hook exists because the machine used to live in three copies (FTUE,
// desktop composer, web composer) and drifted. The tests therefore pin the
// BEHAVIOURS that differed between those copies, not the plumbing:
//
//   • a completed connect re-reads the list (the copy that lacked this shipped
//     the "still says Connect after OAuth" bug),
//   • completion means the host's promise RESOLVED, not that it was called,
//   • a pre-registered vendor never reaches the connect path at all,
//   • a failed connect is reported rather than swallowed.

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { McpCatalogEntry, McpServer } from "@0x-copilot/api-types";

import type { ConnectorToolsHostPort } from "./ports/ConnectorToolsHostPort";
import type { FirstRunConnectorsPort } from "./ports/FirstRunConnectorsPort";
import { useConnectorTools } from "./useConnectorTools";

function linearServer(): McpServer {
  return {
    server_id: "seed:linear",
    name: "linear",
    display_name: "Linear",
    url: "https://mcp.linear.app/mcp",
    transport: "http",
    auth_mode: "oauth2",
    auth_state: "authenticated",
    health: "healthy",
    enabled: true,
    oauth_client_configured: true,
    scopes_summary: "issues & projects",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  };
}

function catalogEntry(over: Partial<McpCatalogEntry> = {}): McpCatalogEntry {
  return {
    slug: "linear",
    display_name: "Linear",
    url: "https://mcp.linear.app/mcp",
    transport: "http",
    auth_mode: "oauth2",
    description: "issues & projects",
    requires_pre_registered_client: false,
    verified: true,
    ...over,
  };
}

function makePort(
  over: Partial<FirstRunConnectorsPort> = {},
): FirstRunConnectorsPort {
  return {
    listServers: vi.fn().mockResolvedValue([]),
    listCatalog: vi.fn().mockResolvedValue([catalogEntry()]),
    installFromCatalog: vi.fn().mockResolvedValue(linearServer()),
    beginAuth: vi.fn().mockResolvedValue(undefined),
    deleteServer: vi.fn().mockResolvedValue(undefined),
    ...over,
  };
}

/** Mounts the hook and renders only its trigger — the surface under test. */
function Harness(props: {
  readonly port?: FirstRunConnectorsPort;
  readonly host: ConnectorToolsHostPort;
  readonly onAddCustom?: () => void;
  readonly onConnectError?: (name: string, message: string) => void;
}) {
  const { toolsTrigger } = useConnectorTools({
    port: props.port,
    host: props.host,
    onAddCustom: props.onAddCustom,
    onConnectError: props.onConnectError,
  });
  return <div>{toolsTrigger ?? <span data-testid="no-pill" />}</div>;
}

describe("useConnectorTools", () => {
  it("re-reads the connector list only once the host's connect RESOLVES", async () => {
    // Before the connect the workspace has nothing; after it, Linear is
    // authenticated — the move the real backend makes.
    const listServers = vi
      .fn()
      .mockResolvedValueOnce([])
      .mockResolvedValue([linearServer()]);
    let finishOAuth: () => void = () => undefined;
    const host: ConnectorToolsHostPort = {
      connect: vi.fn(
        () =>
          new Promise<void>((resolve) => {
            finishOAuth = resolve;
          }),
      ),
    };

    render(<Harness port={makePort({ listServers })} host={host} />);
    fireEvent.click(screen.getByTestId("first-run-tools-button"));
    fireEvent.click(
      await screen.findByTestId("first-run-tools-connect-linear"),
    );

    expect(host.connect).toHaveBeenCalledTimes(1);
    // Handing the browser the URL is not completion. This is the assertion the
    // old FTUE copy failed: it treated "started" as "done".
    expect(listServers).toHaveBeenCalledTimes(1);

    finishOAuth();

    await waitFor(() => expect(listServers).toHaveBeenCalledTimes(2));
    const row = await screen.findByTestId(
      "first-run-tools-connected-seed:linear",
    );
    // Connected connectors are live by default, so it lands already ON.
    expect(row.getAttribute("aria-checked")).toBe("true");
  });

  it("routes a pre-registered vendor to the custom form without connecting", async () => {
    const onAddCustom = vi.fn();
    const host: ConnectorToolsHostPort = { connect: vi.fn() };
    render(
      <Harness
        port={makePort({
          listCatalog: vi
            .fn()
            .mockResolvedValue([
              catalogEntry({ requires_pre_registered_client: true }),
            ]),
        })}
        host={host}
        onAddCustom={onAddCustom}
      />,
    );

    fireEvent.click(screen.getByTestId("first-run-tools-button"));
    fireEvent.click(
      await screen.findByTestId("first-run-tools-connect-linear"),
    );

    // A keyless install of a pre-registered vendor 422s, so it must never be
    // attempted — the form is the whole remedy.
    expect(onAddCustom).toHaveBeenCalledTimes(1);
    expect(host.connect).not.toHaveBeenCalled();
  });

  it("reports a failed connect instead of swallowing it", async () => {
    const onConnectError = vi.fn();
    const host: ConnectorToolsHostPort = {
      connect: vi.fn().mockRejectedValue(new Error("connector 404")),
    };
    render(
      <Harness port={makePort()} host={host} onConnectError={onConnectError} />,
    );

    fireEvent.click(screen.getByTestId("first-run-tools-button"));
    fireEvent.click(
      await screen.findByTestId("first-run-tools-connect-linear"),
    );

    // The original `.catch(() => {})` turned a 404 into a button that did
    // nothing, with no error anywhere and no request in the HTTP logs.
    await waitFor(() =>
      expect(onConnectError).toHaveBeenCalledWith("Linear", "connector 404"),
    );
  });

  it("mounts no pill at all when the host supplies no connector port", () => {
    render(<Harness host={{ connect: vi.fn() }} />);
    expect(screen.getByTestId("no-pill")).not.toBeNull();
    expect(screen.queryByTestId("first-run-tools-button")).toBeNull();
  });

  it("pauses and un-pauses a connected connector", async () => {
    const host: ConnectorToolsHostPort = { connect: vi.fn() };
    render(
      <Harness
        port={makePort({
          listServers: vi.fn().mockResolvedValue([linearServer()]),
        })}
        host={host}
      />,
    );

    fireEvent.click(screen.getByTestId("first-run-tools-button"));
    const row = await screen.findByTestId(
      "first-run-tools-connected-seed:linear",
    );
    // Connected ⇒ ON with no per-run state at all. The toggle is the opt-OUT.
    expect(row.getAttribute("aria-checked")).toBe("true");

    fireEvent.click(row);
    await waitFor(() =>
      expect(
        screen
          .getByTestId("first-run-tools-connected-seed:linear")
          .getAttribute("aria-checked"),
      ).toBe("false"),
    );

    fireEvent.click(
      screen.getByTestId("first-run-tools-connected-seed:linear"),
    );
    await waitFor(() =>
      expect(
        screen
          .getByTestId("first-run-tools-connected-seed:linear")
          .getAttribute("aria-checked"),
      ).toBe("true"),
    );
  });

  // NOTE — the hook also clears a stale pause on the server id a connect just
  // returned. That path is deliberately NOT tested here: it needs a server to be
  // paused (only possible while CONNECTED) and then connected again (only
  // possible while INSTALLABLE), which no single mount can reach through the
  // public surface. An earlier draft of this test appeared to cover it by
  // toggling the row twice — it passed with the logic deleted. The behaviour is
  // carried over verbatim from the desktop original rather than re-derived.
});
