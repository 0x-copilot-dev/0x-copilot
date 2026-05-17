import { createContext, useContext, type ReactNode } from "react";

export interface SwimlaneScrubState {
  readonly scrubbedTo: number | "now";
}

const DEFAULT_VALUE: SwimlaneScrubState = { scrubbedTo: "now" };

const SwimlaneScrubContext = createContext<SwimlaneScrubState>(DEFAULT_VALUE);
SwimlaneScrubContext.displayName = "SwimlaneScrubContext";

export interface SwimlaneScrubProviderProps {
  readonly value: SwimlaneScrubState;
  readonly children: ReactNode;
}

export function SwimlaneScrubProvider(
  props: SwimlaneScrubProviderProps,
): ReactNode {
  return (
    <SwimlaneScrubContext.Provider value={props.value}>
      {props.children}
    </SwimlaneScrubContext.Provider>
  );
}

export function useSwimlaneScrub(): SwimlaneScrubState {
  return useContext(SwimlaneScrubContext);
}
