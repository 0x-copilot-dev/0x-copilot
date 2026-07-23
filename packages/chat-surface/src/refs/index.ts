// Public surface for the ItemRef route registry + <ItemLink> renderer.
// Phase 0.5 shared primitives — see cross-audit.md §3.3; reshaped by PRD-04
// (route-only registry + required-label ItemLink).

export { ItemLink, type ItemLinkProps } from "./ItemLink";
export { itemKindNoun } from "./itemKindNoun";
export {
  ItemRouteAlreadyRegistered,
  ItemRouteNotRegistered,
  __resetItemRouteRegistryForTests,
  hasItemRoute,
  registerItemRoute,
  resolveItemRoute,
  unregisterItemRoute,
  type ItemRouteResolver,
} from "./registry";
