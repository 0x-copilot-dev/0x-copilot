// SkillsGateway — in-destination routing for the Skills destination
// (PR-E.3 Decision D2). Mirrors `features/connectors/ConnectorsGateway`:
// HashRouter only models top-level `/<destination>` slugs, so the deeper
// "manage" pane (the create / edit / delete skill editor previously reached
// via legacy Settings → Skills) rides on local state instead of URL state.
//
//   catalog → the existing `SkillsRoute` binder (chat-surface
//             `<SkillsDestination>` catalog; Run / Edit / New callbacks).
//   manage  → the self-contained `SkillsSettings` editor
//             (`features/settings/sections/SkillsSettings.tsx`) + a back
//             affordance to the catalog.
//
// Data ownership matches the App-shell pattern (each mounted binder owns its
// own hook call): `SkillsRoute` keeps its internal `useSkills`, and the
// manage pane is a tiny wrapper that calls `useSkills` only while mounted —
// the panes are exclusive, so there is exactly one live `useSkills` per
// pane, and no double-fetch on the default catalog view.

import { useState, type ReactElement } from "react";

import { Button } from "@0x-copilot/design-system";

import type { RequestIdentity } from "../../api/config";
import { SkillsSettings } from "../settings/sections/SkillsSettings";
import { SkillsRoute } from "./SkillsRoute";
import { useSkills } from "./useSkills";

interface SkillsGatewayProps {
  readonly identity: RequestIdentity;
  /** Forwarded to the catalog route: open the run cockpit for a started run. */
  readonly onOpenRun?: (conversationId: string) => void;
}

type PaneMode = "catalog" | "manage";

export function SkillsGateway({
  identity,
  onOpenRun,
}: SkillsGatewayProps): ReactElement {
  const [pane, setPane] = useState<PaneMode>("catalog");

  if (pane === "manage") {
    return <ManagePane identity={identity} onBack={() => setPane("catalog")} />;
  }

  return (
    <SkillsRoute
      identity={identity}
      onOpenRun={onOpenRun}
      // Both Edit-a-skill and New-skill land on the manage pane — the
      // editor there is the full list with inline create / edit / delete,
      // so per-skill deep-linking is not needed.
      onOpenSkillEditor={() => setPane("manage")}
    />
  );
}

// ---------------------------------------------------------------------------
// Manage pane — owns the `useSkills` call (mounted exclusively), renders the
// self-contained SkillsSettings editor with a back affordance.
// ---------------------------------------------------------------------------

function ManagePane({
  identity,
  onBack,
}: {
  readonly identity: RequestIdentity;
  readonly onBack: () => void;
}): ReactElement {
  const skills = useSkills(identity);

  return (
    <section
      aria-label="Manage skills"
      data-testid="skills-manage-pane"
      style={{
        height: "100%",
        width: "100%",
        overflow: "auto",
        boxSizing: "border-box",
        padding: 16,
      }}
    >
      <div style={{ marginBottom: 12 }}>
        <Button
          type="button"
          variant="ghost"
          title="Back to the skills catalog"
          data-testid="skills-manage-back"
          onClick={onBack}
        >
          ← Back to catalog
        </Button>
      </div>
      <SkillsSettings skills={skills} />
    </section>
  );
}
