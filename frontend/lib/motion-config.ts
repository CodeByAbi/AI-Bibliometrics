// Runtime motion gates — performance + accessibility guards every
// animated component must consult before animating.
//
// Priority order (highest to lowest):
// 1. prefers-reduced-motion — disables all transforms, opacity ≤ 0.2s only
// 2. Low-end device (hardwareConcurrency <= 4) — drops non-essential motion
// 3. Design preference — everything else
//
// Never read `window`/`navigator` at module level — every access below is
// guarded for SSR safety (App Router prerenders on the server).

import { motionTokens } from "./motion-tokens";

export const motionConfig = {
  isLowEnd(): boolean {
    return (
      typeof navigator !== "undefined" &&
      typeof navigator.hardwareConcurrency === "number" &&
      navigator.hardwareConcurrency <= 4
    );
  },

  prefersReduced(): boolean {
    return (
      typeof window !== "undefined" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches
    );
  },

  shouldAnimate({ essential = false }: { essential?: boolean } = {}): boolean {
    if (this.prefersReduced()) return false;
    if (!essential && this.isLowEnd()) return false;
    return true;
  },

  duration(): number {
    return this.isLowEnd() || this.prefersReduced()
      ? motionTokens.duration.instant
      : motionTokens.duration.med;
  },
};
