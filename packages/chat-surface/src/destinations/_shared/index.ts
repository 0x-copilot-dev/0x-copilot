// Shared list-surface primitives (Frontend parity v3 · PRD-G FR-G.1).
// The design row anatomy (`.pg-lead` / `.sect-h` / `.rowlist` / `.lrow`) defined
// once, so Activity / Chats / Projects can't drift.

export { Page, type PageProps } from "./Page";
export { PageLead, type PageLeadProps } from "./PageLead";
export { BackLink, type BackLinkProps } from "./BackLink";
export {
  ProjectIconTile,
  projectHueRamp,
  projectHueSwatchColor,
  type ProjectHueRamp,
  type ProjectIconTileProps,
} from "./ProjectIconTile";
export { SectionHeader, type SectionHeaderProps } from "./SectionHeader";
export { RowList, type RowListProps } from "./RowList";
export { Row, type RowProps } from "./Row";
