"use client";

/**
 * Fallback for lazily-mounted tab views. Reuses the pipeline readout surface
 * so a slow chunk never leaves a blank column — and announces itself as a
 * status so assistive technology is not left guessing.
 */
export function ViewFallback({ label }: { label: string }) {
  return (
    <div className="loading-card" role="status" aria-live="polite" aria-label={`${label} loading`}>
      <div className="load-row">
        <span className="load-title">Loading {label}…</span>
      </div>
    </div>
  );
}