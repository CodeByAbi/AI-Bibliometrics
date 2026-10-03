"use client";

import { useEffect, useRef, type RefObject } from "react";
import { animate, motion, useMotionValue, useReducedMotion, useTransform } from "motion/react";
import { motionTokens } from "../lib/motion-tokens";

// Mutable copy: motion's `ease` wants a bezier tuple while the frozen
// token stays the single source of truth — spread once here, never inline.
const EASE_OUT: [number, number, number, number] = [...motionTokens.easing.out];

interface CountUpProps {
  /** Final value. Rendered as-is on server/first paint, animated after mount. */
  to: number;
  suffix?: string;
  /** Optional bar driven in sync with the number (same flight, no drift). */
  barRef?: RefObject<HTMLDivElement | null>;
  className?: string;
}

/**
 * The animated digits are a MotionValue child, so motion — not imperative
 * `textContent` — owns that text node. Writing `textContent` from an effect
 * would replace React's child nodes with a single text node while the fiber
 * still referenced them, so a later render updated a detached node and the
 * visible number silently froze.
 */
export function CountUp({ to, suffix = "", barRef, className }: CountUpProps) {
  const reduceMotion = useReducedMotion();
  // Seeded with the final value so server render and first client paint both
  // show the real number; the effect animates from zero only after mount.
  const count = useMotionValue(to);
  const text = useTransform(count, (v) => `${Math.round(v)}${suffix}`);

  useEffect(() => {
    if (reduceMotion) {
      count.set(to);
      return;
    }
    const controls = animate(0, to, {
      duration: motionTokens.duration.fast,
      ease: EASE_OUT,
      onUpdate: (v) => {
        count.set(v);
        if (barRef?.current) barRef.current.style.transform = `scaleX(${Math.round(v) / 100})`;
      },
    });
    return () => controls.stop();
  }, [to, suffix, barRef, reduceMotion, count]);

  return (
    <motion.span className={className}>{text}</motion.span>
  );
}

/**
 * Confidence row + track for an evidence card. The number and the bar share
 * one imperative flight so they never disagree mid-animation. Both sit inside
 * one dt/dd group: a `<dl>` may only directly contain dt/dd groups, so the
 * track cannot be a sibling row of the metrics grid.
 */
export function ConfidenceMeter({ value }: { value: number }) {
  // Runtime null/NaN confidence must not print "NaN%" — unknown renders as "—" with an empty track.
  const finite = Number.isFinite(value);
  const pct = finite ? Math.round((value as number) * 100) : 0;
  const barRef = useRef<HTMLDivElement>(null);

  return (
    <div>
      <dt>Confidence</dt>
      <dd className="mono">
        {finite ? <CountUp to={pct} suffix="%" barRef={barRef} /> : "—"}
        <div
          className="conf-track"
          role="progressbar"
          aria-label="Evidence confidence"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={finite ? pct : undefined}
          aria-valuetext={finite ? `${pct} percent` : "unknown"}
        >
          <div ref={barRef} className="conf-fill" style={{ width: "100%", transform: `scaleX(${pct / 100})` }} />
        </div>
      </dd>
    </div>
  );
}