// Shared motion foundation — single source of truth for every duration,
// easing curve, travel distance, scale, and spring in the app.
//
// Rules (motion-foundations):
// - All token values come from `motionTokens`. Hardcoded durations/easings
//   in component files are forbidden.
// - All spring configs come from `springs`. Inline stiffness/damping is
//   forbidden.
// - Units: duration in seconds (motion/react convention), distance in px,
//   easing as cubic-bezier tuples.

export const motionTokens = {
  duration: {
    instant: 0.08,
    // Mirrors --dur-exit in globals.css; exits stay quieter than entrances.
    exit: 0.15,
    fast: 0.18,
    med: 0.22,
    slow: 0.32,
  },
  easing: {
    out: [0.23, 1, 0.32, 1],
    inOut: [0.77, 0, 0.175, 1],
    drawer: [0.32, 0.72, 0, 1],
    linear: [0, 0, 1, 1],
  },
  distance: {
    xs: 4,
    sm: 8,
  },
  scale: {
    press: 0.97,
  },
} as const;

export const springs = {
  snappy: { type: "spring", duration: 0.35, bounce: 0.15 },
  gentle: { type: "spring", duration: 0.5, bounce: 0.1 },
  release: { type: "spring", duration: 0.4, bounce: 0 },
  drag: { type: "spring", duration: 0.4, bounce: 0.2 },
} as const;
