// ConnectorsGateway — in-destination routing for the Connectors
// destination. HashRouter only models top-level `/<destination>` slugs
// today; the deeper Connectors routes (the detail pane and the
// webhooks sub-destination) ride on local state instead of URL state.
// When the host adds sub-slug routing the gateway is the single place
// that needs to be rewired — the three child routes already accept the
// callbacks they need.
//
// The orchestrator brief asked for `/connectors`, `/connectors/<id>`,
// and `/connectors/webhooks`. Mirrors how P10-C ToolsRoute handles
// list / detail / onboard with a local `PaneMode` union.

import { useState, type ReactElement } from "react";

import type { ConnectorId } from "@0x-copilot/api-types";

import type { RequestIdentity } from "../../api/config";
import { ConnectorDetailRoute } from "./ConnectorDetailRoute";
import { ConnectorsRoute } from "./ConnectorsRoute";
import { WebhooksRoute } from "./WebhooksRoute";

interface ConnectorsGatewayProps {
  readonly identity: RequestIdentity;
  /** Forwarded into the detail view so the audit tab is gated host-side. */
  readonly isAdmin?: boolean;
  /**
   * PR-4.11 — the Tools destination's approval-policy note links to
   * Settings → Model & behavior (FR-4.25). The App shell wires this to
   * `router.navigate({ screen: "settings", section: "model-and-behavior" })`;
   * the gateway just forwards it to the list route.
   */
  readonly onOpenApprovalSettings?: () => void;
}

type PaneMode =
  | { readonly kind: "list" }
  | { readonly kind: "detail"; readonly connectorId: ConnectorId }
  | { readonly kind: "webhooks" };

export function ConnectorsGateway({
  identity,
  isAdmin = false,
  onOpenApprovalSettings,
}: ConnectorsGatewayProps): ReactElement {
  const [pane, setPane] = useState<PaneMode>({ kind: "list" });

  if (pane.kind === "detail") {
    return (
      <ConnectorDetailRoute
        identity={identity}
        connectorId={pane.connectorId}
        isAdmin={isAdmin}
        onClose={() => setPane({ kind: "list" })}
      />
    );
  }

  if (pane.kind === "webhooks") {
    return (
      <WebhooksRoute
        identity={identity}
        onClose={() => setPane({ kind: "list" })}
      />
    );
  }

  return (
    <ConnectorsRoute
      identity={identity}
      onOpenConnector={(id) => setPane({ kind: "detail", connectorId: id })}
      onOpenWebhooks={() => setPane({ kind: "webhooks" })}
      onOpenApprovalSettings={onOpenApprovalSettings}
    />
  );
}
