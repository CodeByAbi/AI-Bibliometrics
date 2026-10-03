import type { RefObject } from "react";

/**
 * Heading ref every view-level panel attaches so the workspace can move focus
 * when the view changes — keyboard and screen-reader users otherwise land on
 * an unchanged DOM and hear nothing.
 *
 * Typed as `RefObject<HTMLHeadingElement>` (not `| null`) because that is the
 * shape React 18's `ref` prop accepts; `useRef<HTMLHeadingElement>(null)`
 * produces exactly this.
 */
export type TitleRef = RefObject<HTMLHeadingElement>;