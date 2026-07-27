// Sources v2 presentation model.
//
// The ledger projection is intentionally security-shaped: it carries only
// allowlisted provenance facts and opaque identifiers. This module is the
// single presentation boundary that turns those facts into the grouped,
// human-readable rows used by the Run rail. It never dereferences a fact and
// never copies refs, paths, origins, ledger ids, arguments, or bodies into UI.

import type {
  SourceFactKindV2,
  SourceFactV2,
  SourcesProjectionV2,
} from "@0x-copilot/api-types";

import { humanizeConnector } from "../citations/connectorLabel";

export interface SourceRowPresentationV2 {
  readonly id: string;
  readonly iconText: string;
  readonly title: string;
  readonly metadata: string;
  readonly openable: boolean;
}

export interface SourceGroupPresentationV2 {
  readonly key: string;
  readonly label: string;
  readonly rows: readonly SourceRowPresentationV2[];
}

export interface SourcesPresentationV2 {
  readonly total: number;
  readonly groups: readonly SourceGroupPresentationV2[];
}

interface MutableSourceGroup {
  readonly key: string;
  readonly label: string;
  readonly rows: SourceRowPresentationV2[];
}

/** Pure, stable first-seen grouping over the safe Sources v2 projection. */
export function presentSourcesV2(
  sources: SourcesProjectionV2,
): SourcesPresentationV2 {
  const groups = new Map<string, MutableSourceGroup>();

  for (const fact of sources.facts) {
    const group = groupFor(fact);
    const existing = groups.get(group.key);
    const row = rowFor(fact);
    if (existing === undefined) {
      groups.set(group.key, { ...group, rows: [row] });
    } else {
      existing.rows.push(row);
    }
  }

  return {
    total: sources.facts.length,
    groups: [...groups.values()],
  };
}

function groupFor(fact: SourceFactV2): Omit<MutableSourceGroup, "rows"> {
  if (fact.kind === "connector") {
    const connector = usableLabel(fact.connector) ?? "connector";
    return {
      key: `connector:${connector.toLowerCase()}`,
      label: humanizeConnector(connector),
    };
  }

  const label = groupLabel(fact.kind);
  return {
    key: `kind:${fact.kind}`,
    label,
  };
}

function rowFor(fact: SourceFactV2): SourceRowPresentationV2 {
  switch (fact.kind) {
    case "connector": {
      const connector = usableLabel(fact.connector);
      const tool = usableLabel(fact.tool);
      return {
        id: fact.source_id,
        iconText: initialsFor(
          connector === null ? "Connector" : humanizeConnector(connector),
        ),
        title:
          tool === null
            ? `${connector === null ? "Connector" : humanizeConnector(connector)} activity`
            : humanizeIdentifier(tool),
        metadata: `Activity · step ${fact.sequence_no}`,
        openable: false,
      };
    }
    case "artifact":
      return {
        id: fact.source_id,
        iconText: initialsFor("Artifact"),
        title: "Generated Artifact",
        metadata:
          fact.artifact_revision === null
            ? `Created · step ${fact.sequence_no}`
            : `Revision ${fact.artifact_revision} · step ${fact.sequence_no}`,
        openable: fact.artifact_id !== null && fact.artifact_revision !== null,
      };
    case "workspace":
      return {
        id: fact.source_id,
        iconText: initialsFor("Workspace"),
        title: usableLabel(fact.workspace_grant_label) ?? "Workspace change",
        metadata: `Workspace · step ${fact.sequence_no}`,
        openable: false,
      };
    case "browser":
      return {
        id: fact.source_id,
        iconText: initialsFor("Browser"),
        title: "Browser activity",
        metadata: `Browser · step ${fact.sequence_no}`,
        openable: false,
      };
    case "sandbox":
      return {
        id: fact.source_id,
        iconText: initialsFor("Sandbox"),
        title:
          usableLabel(fact.sandbox_operation) === null
            ? "Sandbox operation"
            : humanizeIdentifier(fact.sandbox_operation ?? ""),
        metadata: `Sandbox · step ${fact.sequence_no}`,
        openable: false,
      };
    case "subagent":
      return {
        id: fact.source_id,
        iconText: initialsFor("Subagent"),
        title: usableLabel(fact.subagent_task) ?? "Subagent work",
        metadata: `Subagent · step ${fact.sequence_no}`,
        openable: false,
      };
    case "external_receipt":
      return {
        id: fact.source_id,
        iconText: initialsFor("Receipt"),
        title: "External action receipt",
        metadata: `Receipt · step ${fact.sequence_no}`,
        openable: false,
      };
  }
}

function groupLabel(kind: Exclude<SourceFactKindV2, "connector">): string {
  switch (kind) {
    case "artifact":
      return "Artifacts";
    case "workspace":
      return "Workspace";
    case "browser":
      return "Browser";
    case "sandbox":
      return "Sandbox";
    case "subagent":
      return "Subagents";
    case "external_receipt":
      return "Receipts";
  }
}

function usableLabel(value: string | null): string | null {
  const trimmed = value?.trim() ?? "";
  return trimmed.length === 0 ? null : trimmed;
}

function humanizeIdentifier(value: string): string {
  const normalized = value
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .replace(/[_:.-]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  if (normalized.length === 0) return "Activity";
  return normalized.charAt(0).toUpperCase() + normalized.slice(1).toLowerCase();
}

function initialsFor(value: string): string {
  const words = value.trim().split(/\s+/).filter(Boolean);
  if (words.length >= 2) {
    return `${words[0]?.charAt(0) ?? ""}${words[1]?.charAt(0) ?? ""}`;
  }
  const word = words[0] ?? "S";
  return `${word.charAt(0).toUpperCase()}${word.charAt(1).toLowerCase()}`;
}
