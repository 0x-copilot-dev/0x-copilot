// SettingsGateway — P12 in-destination router for the new settings
// sub-paths. Mirrors `TeamGateway` / `ConnectorsGateway`.
//
// The gateway is a thin layer over `SettingsRoute` that picks the
// active panel from the host-supplied sub-path. It does not own URL
// state itself — HashRouter is the single source of truth for that.

import type { ReactElement } from "react";

import type { RequestIdentity } from "../../api/config";
import { SettingsRoute, type SettingsP12SubPath } from "./SettingsRoute";

interface SettingsGatewayProps {
  readonly identity: RequestIdentity;
  readonly isAdmin: boolean;
  readonly subPath: SettingsP12SubPath;
  readonly onBackToChat: () => void;
}

export function SettingsGateway({
  identity,
  isAdmin,
  subPath,
  onBackToChat,
}: SettingsGatewayProps): ReactElement {
  return (
    <SettingsRoute
      identity={identity}
      isAdmin={isAdmin}
      subPath={subPath}
      onBackToChat={onBackToChat}
    />
  );
}
