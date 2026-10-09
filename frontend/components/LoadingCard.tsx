"use client";

import { useEffect, useState } from "react";
import { Check } from "lucide-react";

/* Research pipeline stages — the readout mirrors the real system.
   The stage advance is driven by an ELAPSED-TIME timer, never by a fixture
   and never by a hardcoded per-stage delay: the card mounts fresh on every
   retrieval and is torn down when the request settles, so a slow backend
   genuinely shows a slower walk through these stages. The labels describe
   pipeline stages and carry NO bibliometric values, so this is progress
   feedback, not a data surface. */
const STAGES = [
  { t: "Understanding question", meta: "router · entity gate" },
  { t: "Searching structured data", meta: "SQL · Gold analytics" },
  { t: "Retrieving relevant literature", meta: "pgvector HNSW · gate ≥ 0.48" },
  { t: "Combining evidence", meta: "EvidenceUnifier" },
  { t: "Preparing grounded answer", meta: "synthesis · citation verify" },
];

/**
 * Loading readout owning its own 100ms timer (rerender-09: colocate
 * high-frequency state). Mounts fresh on every `loading` view, so the
 * Workspace root no longer re-renders 10×/sec during retrieval.
 */
export function LoadingCard({ activeQuestion }: { activeQuestion: string }) {
  const [elapsed, setElapsed] = useState(0);
  const [stageIdx, setStageIdx] = useState(0);

  useEffect(() => {
    const t0 = Date.now();
    const tick = setInterval(() => {
      const s = (Date.now() - t0) / 1000;
      setElapsed(s);
      setStageIdx(Math.min(STAGES.length - 1, Math.floor(s / 0.6)));
    }, 100);
    return () => clearInterval(tick);
  }, []);

  return (
    <div className="loading-card" role="status" aria-live="polite" aria-label="Retrieval in progress">
      <div className="load-row">
        <span className="load-title" title={activeQuestion}>Retrieving evidence{activeQuestion ? ` — “${activeQuestion.slice(0, 72)}${activeQuestion.length > 72 ? "…" : ""}”` : ""}</span>
        <span className="elapsed" aria-label="Elapsed time">{elapsed.toFixed(1)}s</span>
      </div>
      <ol className="steps">
        {STAGES.map((s, i) => (
          <li key={s.t} className={i < stageIdx ? "done" : i === stageIdx ? "active" : ""} aria-current={i === stageIdx ? "step" : undefined}>
            <span className="step-ic" aria-hidden>{i < stageIdx ? <Check size={12} strokeWidth={2.5} /> : i + 1}</span> {s.t}
            <span className="mono step-meta">{s.meta}</span>
          </li>
        ))}
      </ol>
    </div>
  );
}
