"use client";

import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { motion, useReducedMotion } from "motion/react";
import { motionTokens } from "../lib/motion-tokens";
import { useSafeMotion } from "../hooks/use-reduced-motion";
import { motionConfig } from "../lib/motion-config";

interface RevealProps {
  children: ReactNode;
  /** Entrance delay in seconds. */
  delay?: number;
  as?: "div" | "article" | "section";
  className?: string;
  id?: string;
  tabIndex?: number;
  "aria-labelledby"?: string;
  "aria-live"?: "polite" | "assertive" | "off";
  "data-highlight"?: boolean | string;
}

// Mount-guarded entrance wrapper (motion-foundations end-to-end pattern).
//
// - Server + first client render output a plain resting-state tag, so
//   `initial` always matches the server and hydration never mismatches.
// - After mount, content enters via tokens + named springs only.
// - Reduced motion: transforms off, opacity-only fade at instant duration.
// - Low-end devices (non-essential): no animation, plain tag.
export function Reveal({ children, delay = 0, as = "div", ...rest }: RevealProps) {
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);

  const safeMotion = useSafeMotion(motionTokens.distance.sm);
  const reduce = useReducedMotion();

  if (!mounted || !motionConfig.shouldAnimate()) {
    const Tag = as;
    return <Tag {...rest}>{children}</Tag>;
  }

  const MotionTag = motion[as] as typeof motion.div;
  const EASE_OUT: [number, number, number, number] = [...motionTokens.easing.out];
  return (
    <MotionTag
      initial={safeMotion.initial}
      animate={safeMotion.animate}
      exit={{ ...safeMotion.exit, transition: { duration: motionTokens.duration.fast } }}
      transition={
        reduce
          ? { duration: motionTokens.duration.instant }
          : { duration: motionTokens.duration.med, ease: EASE_OUT, delay }
      }
      {...rest}
    >
      {children}
    </MotionTag>
  );
}
