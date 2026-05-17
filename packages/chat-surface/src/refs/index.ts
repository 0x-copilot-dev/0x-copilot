// Public surface for the ItemRef registry + <ItemLink> renderer.
// Phase 0.5 shared primitives — see cross-audit.md §3.3.

export { ItemLink, type ItemLinkProps } from "./ItemLink";
export {
  ItemRefResolverAlreadyRegistered,
  ItemRefResolverNotRegistered,
  __resetItemRefRegistryForTests,
  hasItemRefResolver,
  registerItemRefResolver,
  resolveItemRef,
  unregisterItemRefResolver,
  type ItemRefResolved,
  type ItemRefResolver,
} from "./registry";
