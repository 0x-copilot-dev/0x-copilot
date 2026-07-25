// Workspace-stage display model (Generative Surfaces v2.1, PRD-C3 D4). 🎨
//
// This is intentionally a presentation-only, allowlisted model. A host adapts
// its durable stage snapshot to `WorkspaceStage` before rendering it; physical
// paths, permit tokens, prepared refs, and host capabilities have no place in
// this type. `projectWorkspaceStage` is a pure defensive display projection
// that validates the few values the UI is allowed to show.

export const WORKSPACE_STAGE_PLEDGE =
  "Only this revision and target will be applied.";

export type WorkspaceStageOperationKind =
  | "create"
  | "replace"
  | "delete"
  | "move"
  | "mkdir";

/** Matches ThreadCanvas' presentation slots without coupling this card to it. */
export type WorkspaceStageMode = "focus" | "studio";

export type WorkspaceStageStatus =
  | "staged"
  | "approved"
  | "rejected"
  | "applied"
  | "held"
  | "failed";

/** C3 D10 states that require honest hold/recovery treatment before a retry. */
export type WorkspaceStageResolutionState =
  | "grant_revoked"
  | "precondition_drift"
  | "reconciling"
  | "indeterminate"
  | "recovery_proposed"
  | "recovery_conflict"
  | "unsupported"
  | "upload_mismatch";

export interface WorkspaceStageOperation {
  readonly kind: WorkspaceStageOperationKind;
  /** Virtual source only; used by move stages. */
  readonly sourceVirtualPath?: string | null;
  /** A move that overwrites a destination is destructive (C3 D1/D4). */
  readonly overwrite?: boolean;
}

/** Never pass a local filesystem path as either field. */
export interface WorkspaceStageTarget {
  readonly mountLabel: string;
  readonly virtualPath: string;
}

export interface WorkspaceStageBinaryMetadata {
  readonly mediaType?: string | null;
  readonly byteSize?: number | null;
  /** A SHA-256 digest only; arbitrary refs and tokens are intentionally absent. */
  readonly sha256?: string | null;
}

/** Opt-in, hydrated preview data. Every arm is rendered as plain text. */
export type WorkspaceStagePreview =
  | {
      readonly kind: "text";
      readonly content: string;
      readonly language?: string | null;
    }
  | {
      readonly kind: "csv";
      readonly columns: readonly string[];
      readonly rows: readonly (readonly string[])[];
    }
  | {
      readonly kind: "binary";
      readonly metadata: WorkspaceStageBinaryMetadata;
    };

export type WorkspaceStageDiff =
  | {
      readonly kind: "text";
      readonly before?: string | null;
      readonly after?: string | null;
    }
  | {
      readonly kind: "csv";
      readonly beforeRows?: number | null;
      readonly afterRows?: number | null;
      readonly changedRows?: number | null;
    }
  | {
      readonly kind: "binary";
      readonly before?: WorkspaceStageBinaryMetadata | null;
      readonly after?: WorkspaceStageBinaryMetadata | null;
    };

export interface WorkspaceStageBaseline {
  readonly summary: string;
  readonly digest?: string | null;
}

export interface WorkspaceStagePrecondition {
  readonly summary: string;
}

export interface WorkspaceStageRevision {
  readonly revision: number;
  readonly author: string;
  readonly summary?: string | null;
}

export interface WorkspaceStageResolution {
  readonly state: WorkspaceStageResolutionState;
  /** A host-provided, safe display explanation; rendered only as text. */
  readonly summary?: string | null;
}

/**
 * The complete, safe-to-render view model for one workspace effect stage.
 *
 * This does not contain a physical path, grant id, permit token, prepared ref,
 * content ref, or a callable host capability. Actions retain only the opaque
 * stage id and revision supplied separately to callbacks.
 */
export interface WorkspaceStage {
  readonly stageId: string;
  readonly title: string;
  readonly operation: WorkspaceStageOperation;
  readonly target: WorkspaceStageTarget;
  readonly revision: number;
  readonly author: string;
  readonly status: WorkspaceStageStatus;
  readonly preview?: WorkspaceStagePreview | null;
  readonly diff?: WorkspaceStageDiff | null;
  readonly baseline?: WorkspaceStageBaseline | null;
  readonly precondition?: WorkspaceStagePrecondition | null;
  readonly revisionHistory?: readonly WorkspaceStageRevision[] | null;
  readonly resolution?: WorkspaceStageResolution | null;
}

export interface ProjectedWorkspaceStageRevision {
  readonly revision: number;
  readonly author: string;
  readonly summary: string | null;
}

/** The rendered subset of a stage. It contains no host-only source fields. */
export interface ProjectedWorkspaceStage {
  readonly title: string;
  readonly operationLabel: string;
  readonly operationKind: WorkspaceStageOperationKind;
  readonly destructive: boolean;
  readonly mountLabel: string;
  readonly virtualPath: string | null;
  readonly sourceVirtualPath: string | null;
  readonly revision: number | null;
  readonly author: string;
  readonly statusLabel: string;
  readonly baselineSummary: string;
  readonly baselineDigest: string | null;
  readonly preconditionSummary: string;
  readonly resolutionLabel: string | null;
  readonly resolutionSummary: string | null;
  readonly history: readonly ProjectedWorkspaceStageRevision[];
  readonly compact: boolean;
  readonly canDecide: boolean;
  readonly canRestore: boolean;
  readonly canEdit: boolean;
}

const MAX_TITLE_LENGTH = 240;
const MAX_MOUNT_LABEL_LENGTH = 120;
const MAX_SUMMARY_LENGTH = 600;
const MAX_VIRTUAL_PATH_LENGTH = 1_024;

const STATUS_LABELS: Record<WorkspaceStageStatus, string> = {
  staged: "Awaiting review",
  approved: "Approved",
  rejected: "Rejected",
  applied: "Applied",
  held: "Held",
  failed: "Failed",
};

const RESOLUTION_COPY: Record<
  WorkspaceStageResolutionState,
  { label: string; summary: string }
> = {
  grant_revoked: {
    label: "Workspace access changed",
    summary: "This stage is held until the workspace mount is reconnected.",
  },
  precondition_drift: {
    label: "Conflict",
    summary:
      "The baseline changed. Nothing was applied; regenerate or rebase this revision.",
  },
  reconciling: {
    label: "Reconciling",
    summary:
      "Checking an interrupted workspace operation before any retry. No blind retry will run.",
  },
  indeterminate: {
    label: "Outcome unknown",
    summary:
      "The host outcome is indeterminate. Reconciliation is required before another apply.",
  },
  recovery_proposed: {
    label: "Recovery proposed",
    summary: "A recovery proposal is ready for review.",
  },
  recovery_conflict: {
    label: "Recovery conflict",
    summary: "Recovery found a conflict. Nothing was forced.",
  },
  unsupported: {
    label: "Workspace unavailable",
    summary:
      "This platform cannot apply the workspace change. The artifact remains available.",
  },
  upload_mismatch: {
    label: "Content verification failed",
    summary: "Content verification failed before any workspace effect.",
  },
};

/** `delete` and overwrite-move are the destructive C3 operation classes. */
export function isDestructiveWorkspaceOperation(
  operation: WorkspaceStageOperation,
): boolean {
  return (
    operation.kind === "delete" ||
    (operation.kind === "move" && operation.overwrite === true)
  );
}

export function workspaceStageOperationLabel(
  operation: WorkspaceStageOperation,
): string {
  if (operation.kind === "move" && operation.overwrite === true) {
    return "move · overwrite";
  }
  return operation.kind;
}

/**
 * A UI path is valid only when it stays in the virtual `/workspace` namespace.
 * The host keeps the actual root handle/path private; invalid values are not
 * rendered as a fallback because that could expose a host filesystem path.
 */
export function safeWorkspaceVirtualPath(
  value: string | null | undefined,
): string | null {
  if (typeof value !== "string") return null;
  const path = value.trim();
  if (
    (path !== "/workspace" && !path.startsWith("/workspace/")) ||
    path.length > MAX_VIRTUAL_PATH_LENGTH ||
    path.includes("\\") ||
    path.includes("\u0000")
  ) {
    return null;
  }
  const segments = path.split("/");
  if (segments.some((segment) => segment === "." || segment === "..")) {
    return null;
  }
  return path;
}

/** SHA-256 is the only digest format this display surface exposes. */
export function safeWorkspaceDigest(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const digest = value.trim();
  return /^(?:sha256:)?[a-f0-9]{64}$/i.test(digest) ? digest : null;
}

/**
 * Pure display projection used by the surface. It is deliberately fail-closed:
 * malformed identities disable decision actions; untrusted physical-looking
 * labels/paths fall back to non-sensitive copy rather than being displayed.
 */
export function projectWorkspaceStage(
  stage: WorkspaceStage,
  mode: WorkspaceStageMode = "studio",
): ProjectedWorkspaceStage {
  const revision = safeRevision(stage.revision);
  const resolution = stage.resolution ?? null;
  const virtualPath = safeWorkspaceVirtualPath(stage.target.virtualPath);
  const sourceVirtualPath = safeWorkspaceVirtualPath(
    stage.operation.sourceVirtualPath,
  );
  const hasReviewableTarget =
    virtualPath !== null &&
    (stage.operation.kind !== "move" || sourceVirtualPath !== null);
  const decisionBlocked = resolution !== null || stage.status !== "staged";
  const resolutionCopy =
    resolution === null ? null : RESOLUTION_COPY[resolution.state];
  const history = projectHistory(stage.revisionHistory, revision, stage.author);

  return {
    title: safeWorkspaceDisplayText(
      stage.title,
      "Workspace change",
      MAX_TITLE_LENGTH,
    ),
    operationLabel: workspaceStageOperationLabel(stage.operation),
    operationKind: stage.operation.kind,
    destructive: isDestructiveWorkspaceOperation(stage.operation),
    mountLabel: safeMountLabel(stage.target.mountLabel),
    virtualPath,
    sourceVirtualPath,
    revision,
    author: safeWorkspaceDisplayText(
      stage.author,
      "Unknown author",
      MAX_TITLE_LENGTH,
    ),
    statusLabel: STATUS_LABELS[stage.status] ?? "Held",
    baselineSummary: safeSummary(
      stage.baseline?.summary,
      defaultBaselineSummary(stage.operation.kind),
    ),
    baselineDigest: safeWorkspaceDigest(stage.baseline?.digest),
    preconditionSummary: safeSummary(
      stage.precondition?.summary,
      "Preconditions will be checked before apply.",
    ),
    resolutionLabel: resolutionCopy?.label ?? null,
    resolutionSummary:
      resolutionCopy === null
        ? null
        : safeSummary(resolution?.summary, resolutionCopy.summary),
    history,
    compact: mode === "focus",
    canDecide:
      !decisionBlocked &&
      hasReviewableTarget &&
      revision !== null &&
      typeof stage.stageId === "string" &&
      stage.stageId.length > 0,
    canRestore:
      stage.status === "rejected" &&
      typeof stage.stageId === "string" &&
      stage.stageId.length > 0,
    canEdit:
      ((resolution === null &&
        (stage.status === "staged" ||
          stage.status === "held" ||
          stage.status === "failed")) ||
        resolution?.state === "precondition_drift" ||
        resolution?.state === "recovery_proposed" ||
        resolution?.state === "recovery_conflict" ||
        resolution?.state === "upload_mismatch") &&
      revision !== null &&
      typeof stage.stageId === "string" &&
      stage.stageId.length > 0,
  };
}

function projectHistory(
  history: readonly WorkspaceStageRevision[] | null | undefined,
  currentRevision: number | null,
  currentAuthor: string,
): readonly ProjectedWorkspaceStageRevision[] {
  const projected = (history ?? [])
    .map((entry) => ({
      revision: safeRevision(entry.revision),
      author: safeWorkspaceDisplayText(
        entry.author,
        "Unknown author",
        MAX_TITLE_LENGTH,
      ),
      summary:
        entry.summary === undefined || entry.summary === null
          ? null
          : safeSummary(entry.summary, "Revision updated."),
    }))
    .filter(
      (
        entry,
      ): entry is ProjectedWorkspaceStageRevision & { revision: number } =>
        entry.revision !== null,
    )
    .slice(-6);

  if (projected.length > 0) return projected;
  if (currentRevision === null) return [];
  return [
    {
      revision: currentRevision,
      author: safeWorkspaceDisplayText(
        currentAuthor,
        "Unknown author",
        MAX_TITLE_LENGTH,
      ),
      summary: null,
    },
  ];
}

function safeRevision(value: number): number | null {
  return Number.isSafeInteger(value) && value > 0 ? value : null;
}

function safeMountLabel(value: string): string {
  const label = safeWorkspaceDisplayText(
    value,
    "Workspace",
    MAX_MOUNT_LABEL_LENGTH,
  );
  return looksLikePhysicalPath(label) ? "Workspace" : label;
}

function safeSummary(
  value: string | null | undefined,
  fallback: string,
): string {
  const summary = safeWorkspaceDisplayText(value, fallback, MAX_SUMMARY_LENGTH);
  return looksLikeSensitiveReference(summary) ? fallback : summary;
}

/**
 * Turns untrusted host text into bounded, display-only content. Any value that
 * resembles a local filesystem reference or a host-only permit/reference field
 * is withheld wholesale rather than risking a partial secret/path disclosure.
 */
export function safeWorkspaceDisplayText(
  value: unknown,
  fallback: string,
  max: number,
): string {
  if (typeof value !== "string") return fallback;
  const text = value.replace(/[\u0000-\u001F\u007F]/g, " ").trim();
  if (
    text.length === 0 ||
    looksLikePhysicalPath(text) ||
    looksLikeSensitiveReference(text)
  ) {
    return fallback;
  }
  return text.slice(0, max);
}

function looksLikePhysicalPath(value: string): boolean {
  return /(?:file:|~[\\/]|[A-Za-z]:[\\/]|\\\\|\/(?:Users|home|private|var|tmp|Volumes|etc|usr|opt|root|mnt|Library|System)\/)/.test(
    value,
  );
}

function looksLikeSensitiveReference(value: string): boolean {
  return /(?:permit(?:[_\s-]?token|Token)?|prepared(?:[_\s-]?ref|Ref)|physical(?:[_\s-]?path|Path))[\s"']*[:=]/i.test(
    value,
  );
}

function defaultBaselineSummary(
  operation: WorkspaceStageOperationKind,
): string {
  switch (operation) {
    case "create":
    case "mkdir":
      return "The target must remain absent.";
    case "replace":
      return "The captured target identity and content must still match.";
    case "delete":
      return "The captured target identity and content must still match.";
    case "move":
      return "The source must still match and the destination must remain available.";
  }
}
