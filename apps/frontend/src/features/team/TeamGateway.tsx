// TeamGateway — in-destination router for the Team destination.
// HashRouter only models top-level `/<destination>` slugs today; the
// per-person detail surface (`/team/<id>`) rides on local state.
// Mirrors `ConnectorsGateway` (P11-C).
//
// The gateway accepts an optional `initialPersonId` from the URL parser
// (set when the user lands directly on `/team/<id>`). On state changes
// the gateway notifies its host via `onSubPathChange` so the URL stays
// in sync without lifting routing into a separate state machine.

import { useEffect, useState, type ReactElement } from "react";

import type { UserId } from "@0x-copilot/api-types";

import type { RequestIdentity } from "../../api/config";
import { TeamDetailRoute } from "./TeamDetailRoute";
import { TeamRoute } from "./TeamRoute";

interface TeamGatewayProps {
  readonly identity: RequestIdentity;
  /** Initial sub-path slug from the URL parser; `null` = list. */
  readonly initialPersonId?: string | null;
  /** Notify host when the gateway state changes so the URL can update. */
  readonly onSubPathChange?: (subPath: string | null) => void;
}

type PaneMode =
  | { readonly kind: "list" }
  | { readonly kind: "detail"; readonly personId: UserId };

export function TeamGateway({
  identity,
  initialPersonId,
  onSubPathChange,
}: TeamGatewayProps): ReactElement {
  const [pane, setPane] = useState<PaneMode>(() =>
    initialPersonId
      ? { kind: "detail", personId: initialPersonId as UserId }
      : { kind: "list" },
  );

  // Sync external URL → internal pane state when the host's parsed
  // sub-path changes (back/forward navigation).
  useEffect(() => {
    if (initialPersonId) {
      setPane({ kind: "detail", personId: initialPersonId as UserId });
    } else {
      setPane({ kind: "list" });
    }
  }, [initialPersonId]);

  function go(next: PaneMode): void {
    setPane(next);
    onSubPathChange?.(
      next.kind === "detail" ? (next.personId as string) : null,
    );
  }

  if (pane.kind === "detail") {
    return (
      <TeamDetailRoute
        identity={identity}
        personId={pane.personId}
        onClose={() => go({ kind: "list" })}
      />
    );
  }

  return (
    <TeamRoute
      identity={identity}
      onOpenPerson={(id) => go({ kind: "detail", personId: id })}
    />
  );
}
