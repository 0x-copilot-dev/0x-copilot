// MemoryGateway — in-destination router for the Memory destination.
// HashRouter only models top-level `/<destination>` slugs today; the
// per-row detail (`/memory/<id>`) and proposals queue (`/memory/proposals`)
// surfaces ride on local state. Mirrors `TeamGateway` /
// `ConnectorsGateway`.

import { useEffect, useState, type ReactElement } from "react";

import type { MemoryItemId } from "@enterprise-search/api-types";

import type { RequestIdentity } from "../../api/config";
import { MemoryDetailRoute } from "./MemoryDetailRoute";
import { MemoryProposalsRoute } from "./MemoryProposalsRoute";
import { MemoryRoute } from "./MemoryRoute";

interface MemoryGatewayProps {
  readonly identity: RequestIdentity;
  /** Initial sub-path slug from the URL parser; `null` = list. */
  readonly initialSubPath?: string | null;
  readonly onSubPathChange?: (subPath: string | null) => void;
}

type PaneMode =
  | { readonly kind: "list" }
  | { readonly kind: "detail"; readonly memoryItemId: MemoryItemId }
  | { readonly kind: "proposals" };

function paneFromSubPath(subPath: string | null | undefined): PaneMode {
  if (!subPath) return { kind: "list" };
  if (subPath === "proposals") return { kind: "proposals" };
  return { kind: "detail", memoryItemId: subPath as MemoryItemId };
}

function subPathFromPane(pane: PaneMode): string | null {
  switch (pane.kind) {
    case "list":
      return null;
    case "proposals":
      return "proposals";
    case "detail":
      return pane.memoryItemId as string;
  }
}

export function MemoryGateway({
  identity,
  initialSubPath,
  onSubPathChange,
}: MemoryGatewayProps): ReactElement {
  const [pane, setPane] = useState<PaneMode>(() =>
    paneFromSubPath(initialSubPath),
  );

  useEffect(() => {
    setPane(paneFromSubPath(initialSubPath));
  }, [initialSubPath]);

  function go(next: PaneMode): void {
    setPane(next);
    onSubPathChange?.(subPathFromPane(next));
  }

  if (pane.kind === "detail") {
    return (
      <MemoryDetailRoute
        identity={identity}
        memoryItemId={pane.memoryItemId}
        onClose={() => go({ kind: "list" })}
        onDeleted={() => go({ kind: "list" })}
      />
    );
  }

  if (pane.kind === "proposals") {
    return (
      <MemoryProposalsRoute
        identity={identity}
        onClose={() => go({ kind: "list" })}
      />
    );
  }

  return (
    <MemoryRoute
      identity={identity}
      onOpenItem={(id) => go({ kind: "detail", memoryItemId: id })}
      onOpenProposals={() => go({ kind: "proposals" })}
    />
  );
}
