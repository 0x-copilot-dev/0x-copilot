// Shared list-surface primitives (Frontend parity v3 · PRD-G FR-G.1).
// The design row anatomy — a lead paragraph, a mono section header, one bordered
// card per group, and the leading-icon row — defined once, so Activity / Chats /
// Projects can't drift. Styling is tokens + `.ui-*` recipes (the design-system
// SoT); the mock's un-namespaced class names carried no CSS and were deleted in
// PRD-13.

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
