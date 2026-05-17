// Placeholder mounted inside <ChatShell> for Phase 1-A. Once Agent 1-B's
// shell-layout work lands, the layout's DestinationOutlet takes the
// children slot; this component disappears at integration. Until then,
// it's a single visible signal that the renderer mounted successfully.
export function DesktopPlaceholder(): React.ReactElement {
  return (
    <div
      data-testid="desktop-placeholder"
      style={{
        color: "#dadcde",
        fontFamily: "system-ui, -apple-system, Segoe UI, sans-serif",
        fontSize: 14,
        padding: 24,
      }}
    >
      Atlas desktop · phase 1
    </div>
  );
}
