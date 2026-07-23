// ProjectDataPort — web implementation (PRD-07 Seam 3).
//
// The web host's binding of chat-surface's `ProjectDataPort` over apps/frontend's
// HTTP client. It is the twin of the desktop implementation in
// `apps/desktop/renderer/destinationBinders.tsx::createDesktopProjectDataPort`,
// so the shared `ProjectDetailView` renders identical project-scoped Chats +
// Files on both hosts — desktop is not a copy of web; both bind one port.
//
// Neither host invents an endpoint:
//   * chats → `GET /v1/agent/conversations?filter[project_id]=<id>
//     &include_archived=true`, mapped by PRD-03's SHARED per-row projector
//     `toChatArchiveRow` (so PRD-02's status chip + PRD-10's row apply for free
//     — this PRD adds NO third row projection, DoD 9c).
//   * files → `GET /v1/library?filter[project_id]=<id>&filter[kind]=file`,
//     mapping each `LibraryFile` → `ProjectFileRow`. A project file IS a library
//     item with `project_id` set; there is no `/v1/projects/{id}/files` route.
//
// Network rule (apps/frontend/CLAUDE.md): apps call the FACADE only. The facade
// reads the `filter[project_id]` axis and translates it to ai-backend's plain
// `project_id` query param (`backend-facade/app.py::list_conversations`).
//
// Each method resolves a `SectionResult` (never throws) so the detail view's
// uniform 4-state machine (error / unavailable / empty / ready) drives itself.

import { toChatArchiveRow } from "@0x-copilot/chat-surface";
import type { ProjectDataPort } from "@0x-copilot/chat-surface";
import type {
  ConversationListResponse,
  LibraryFile,
  LibraryListResponse,
  ProjectFileRow,
  ProjectId,
} from "@0x-copilot/api-types";

import type { RequestIdentity } from "../../api/config";
import { httpGet } from "../../api/http";
import { errorMessage } from "../../utils/errors";

// Human-readable file size from raw bytes (display-only sub-line). `undefined`
// for missing / zero bytes so the row omits the segment rather than showing
// "0 B".
export function fileSizeLabel(bytes: number): string | undefined {
  if (!Number.isFinite(bytes) || bytes <= 0) return undefined;
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  const rounded =
    unit === 0 || value >= 10 ? Math.round(value) : Math.round(value * 10) / 10;
  return `${rounded} ${units[unit]}`;
}

function toProjectFileRow(file: LibraryFile): ProjectFileRow {
  return {
    id: file.id,
    name: file.name,
    fileKind: file.file_kind,
    updatedAt: file.updated_at,
    sizeLabel: fileSizeLabel(file.size_bytes),
  };
}

/**
 * Build the web `ProjectDataPort` bound to a request identity. Mirrors the
 * desktop port's transport calls + projections exactly (the shared home for
 * the projection is the package's `toChatArchiveRow`; the file mapping is a
 * tiny per-host shape over `@0x-copilot/api-types`).
 */
export function createWebProjectDataPort(
  identity: RequestIdentity,
): ProjectDataPort {
  return {
    async listProjectChats(projectId: ProjectId) {
      try {
        const response = await httpGet<ConversationListResponse>(
          "/v1/agent/conversations",
          identity,
          { "filter[project_id]": projectId, include_archived: "true" },
        );
        const rows = (response?.conversations ?? [])
          .filter((conversation) => conversation.deleted_at == null)
          .map(toChatArchiveRow);
        return { status: "ok", data: rows };
      } catch (error) {
        return {
          status: "error",
          error: errorMessage(error, "Could not load chats."),
        };
      }
    },
    async listProjectFiles(projectId: ProjectId) {
      try {
        const response = await httpGet<LibraryListResponse>(
          "/v1/library",
          identity,
          {
            "filter[project_id]": projectId,
            "filter[kind]": "file",
            limit: "50",
          },
        );
        const rows = (response?.items ?? [])
          .filter((item): item is LibraryFile => item.kind === "file")
          .map(toProjectFileRow);
        return { status: "ok", data: rows };
      } catch (error) {
        return {
          status: "error",
          error: errorMessage(error, "Could not load files."),
        };
      }
    },
  };
}
