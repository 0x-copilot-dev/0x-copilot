/**
 * Headless popover primitive — thin wrapper around `@radix-ui/react-popover`.
 *
 * Provides accessible focus management, click-outside dismissal, Escape, and
 * portalling. The visual treatment lives in `styles.css` under `.ds-popover-content`.
 *
 * Animations honour `[data-reduce-motion="always"]` (set by app preferences).
 */

import * as RadixPopover from "@radix-ui/react-popover";
import type { ComponentProps, ReactElement, ReactNode } from "react";

import { classNames } from "./index";

export type PopoverProps = ComponentProps<typeof RadixPopover.Root>;

export function Popover(props: PopoverProps): ReactElement {
  return <RadixPopover.Root {...props} />;
}

export const PopoverTrigger = RadixPopover.Trigger;
export const PopoverClose = RadixPopover.Close;
export const PopoverAnchor = RadixPopover.Anchor;

export type PopoverContentProps = ComponentProps<
  typeof RadixPopover.Content
> & {
  children: ReactNode;
};

export function PopoverContent({
  children,
  className,
  sideOffset = 6,
  align = "end",
  ...rest
}: PopoverContentProps): ReactElement {
  return (
    <RadixPopover.Portal>
      <RadixPopover.Content
        className={classNames("ds-popover-content", className)}
        sideOffset={sideOffset}
        align={align}
        {...rest}
      >
        {children}
      </RadixPopover.Content>
    </RadixPopover.Portal>
  );
}
