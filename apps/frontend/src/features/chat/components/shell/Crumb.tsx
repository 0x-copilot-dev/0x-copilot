import type { ReactElement } from "react";

export interface CrumbProps {
  workspace: string | null;
  folder: string | null;
}

/**
 * `Workspace › Folder` topbar breadcrumb. Single line, clipped at 220px
 * via CSS. Renders nothing when neither part is known so the topbar
 * doesn't reserve empty space on a brand-new install.
 */
export function Crumb({ workspace, folder }: CrumbProps): ReactElement | null {
  const parts: string[] = [];
  if (workspace) parts.push(workspace);
  if (folder) parts.push(folder);
  if (parts.length === 0) {
    return null;
  }
  return (
    <nav className="atlas-crumb" aria-label="Breadcrumb">
      {parts.map((part, index) => (
        <span key={index} className="atlas-crumb__part">
          {part}
          {index < parts.length - 1 && (
            <span aria-hidden="true" className="atlas-crumb__sep">
              ›
            </span>
          )}
        </span>
      ))}
    </nav>
  );
}
