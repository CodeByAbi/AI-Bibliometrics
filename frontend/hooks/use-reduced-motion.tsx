"use client";

import { useReducedMotion } from "motion/react";

// Accessibility-safe entrance preset for App Router client components.
//
// - When the OS requests reduced motion, transforms are disabled and only
//   an opacity fade remains (≤ 0.2s per foundation rules).
// - `initial` is transform-free whenever reduced motion is on, so there is
//   no SSR/hydration divergence to guard: server and client agree on the
//   resting state, and motion only enhances after mount.
export function useSafeMotion(fullY: number = 16) {
  const reduce = useReducedMotion();
  return {
    initial: { opacity: 0, transform: reduce ? "none" : `translateY(${fullY}px)` },
    animate: { opacity: 1, transform: "translateY(0px)" },
    exit: { opacity: 0, transform: reduce ? "none" : `translateY(${-fullY}px)` },
  };
}
