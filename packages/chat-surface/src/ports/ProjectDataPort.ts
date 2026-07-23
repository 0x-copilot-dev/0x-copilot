// ProjectDataPort — the project detail's Chats + Files data seam (PRD-07).
//
// Both hosts (web `apps/frontend`, desktop `apps/desktop`) implement this ONE
// port over their own `Transport`, so the shared `ProjectDetailView` renders
// real project-scoped chats and files identically on both — desktop is not a
// copy of web. Neither implementation invents an endpoint:
//
//   * chats → `GET /v1/agent/conversations?filter[project_id]=<id>&include_archived=true`,
//     mapped by PRD-03's shared per-row projector `toChatArchiveRow` (so PRD-02's
//     status chip and PRD-10's Row apply for free — no third row projection).
//   * files → `GET /v1/library?filter[project_id]=<id>&filter[kind]=file`, mapping
//     `LibraryFile` → `ProjectFileRow`. A project file IS a library item with
//     `project_id` set — the model, index, ACL and count already exist; a second
//     `/v1/projects/{id}/files` endpoint would be a second source of truth for
//     visibility.
//
// Substrate-agnostic: no `window`/`fetch`/`localStorage` — capabilities arrive
// through the injected `Transport`. Each method resolves a `SectionResult` so
// the detail view renders the uniform 4-state machine (error / unavailable /
// empty / ready) without the port throwing.

import type {
  ChatArchiveRow,
  ProjectFileRow,
  ProjectId,
  SectionResult,
} from "@0x-copilot/api-types";

export interface ProjectDataPort {
  /** The project's chat list — the conversation list filtered by project. */
  listProjectChats(
    projectId: ProjectId,
  ): Promise<SectionResult<ReadonlyArray<ChatArchiveRow>>>;

  /** The project's files — library `kind='file'` rows scoped to the project. */
  listProjectFiles(
    projectId: ProjectId,
  ): Promise<SectionResult<ReadonlyArray<ProjectFileRow>>>;
}
