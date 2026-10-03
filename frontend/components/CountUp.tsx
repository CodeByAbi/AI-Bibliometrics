"use client";

import { useEffect, useRef, type RefObject } from "react";
import { animate, useReducedMotion } from "motion/react";
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

export function CountUp({ to, suffix = "", barRef, className }: CountUpProps) {
  const nodeRef = useRef<HTMLSpanElement>(null);
  const reduceMotion = useReducedMotion();

  useEffect(() => {
    if (reduceMotion) return;
    const controls = animate(0, to, {
      duration: motionTokens.duration.fast,
      ease: EASE_OUT,
      onUpdate(v) {
        const n = Math.round(v);
        if (nodeRef.current) nodeRef.current.textContent = `${n}${suffix}`;
        if (barRef?.current) barRef.current.style.transform = `scaleX(${n / 100})`;
      },
    });
    return () => controls.stop();
  }, [to, suffix, barRef, reduceMotion]);

  return (
    <span ref={nodeRef} className={className}>
      {Math.round(to)}
      {suffix}
    </span>
  );
}

/**
 * Confidence row + track for an evidence card. The number and the bar share
 * one imperative flight so they never disagree mid-animation. The track lives
 * inside the metrics grid as a spanning row (valid `div`-in-`dl`) instead of
 * below it, so the component owns both ends without restructuring the card.
 */
export function ConfidenceMeter({ value }: { value: number }) {
  // Runtime null/NaN confidence must not print "NaN%" — unknown renders as "—" with an empty track.
  const finite = Number.isFinite(value);
  const pct = finite ? Math.round((value as number) * 100) : 0;
  const barRef = useRef<HTMLDivElement>(null);

  return (
    <>
      <div>
        <dt>Confidence</dt>
        <dd className="mono">
          {finite ? <CountUp to={pct} suffix="%" barRef={barRef} /> : "—"}
        </dd>
      </div>
      <div className="conf-track conf-track-span" role="img" aria-label={finite ? `Confidence ${pct} percent` : "Confidence unknown"}>
        <div ref={barRef} className="conf-fill" style={{ width: "100%", transform: `scaleX(${pct / 100})` }} />
      </div>
    </>
  );
}
