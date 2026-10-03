"use client";

import { SEEDS } from "../../lib/api";

const WORKFLOW_STEPS = [
  { n: "01", t: "Ask a research question", d: "Indonesian or English, with optional year or institution scope." },
  { n: "02", t: "Read the grounded answer", d: "Narrative generated only from retrieved database records." },
  { n: "03", t: "Inspect evidence", d: "Metric, value, period, and confidence per evidence object." },
  { n: "04", t: "Inspect sources", d: "Title, year, DOI, and relevance for every cited publication." },
] as const;

export interface EmptyWorkspaceProps {
  onAsk: (question: string) => void;
}

/**
 * Idle state with real, answerable example questions — no lorem ipsum. The
 * suggestions run the live retrieval path, so a first click already exercises
 * routing, evidence unification, and citation verification.
 */
export function EmptyWorkspace({ onAsk }: EmptyWorkspaceProps) {
  return (
    <>
      <div className="seeds" role="group" aria-label="Example questions">
        <span className="seeds-label" aria-hidden>
          Try
        </span>
        {SEEDS.map((s) => (
          <button
            key={s.id}
            type="button"
            className="seed"
            onClick={() => onAsk(s.question)}
            aria-label={`Example question: ${s.question}`}
          >
            {s.label}
          </button>
        ))}
      </div>

      <section className="empty-hero" aria-labelledby="empty-hero-title">
        <h2 id="empty-hero-title">Start with a question the database can answer</h2>
        <p>
          Try “{SEEDS[0].question}” — the workspace selects a retrieval route, unifies evidence objects, then
          synthesizes a cited narrative. Ambiguous names pause for clarification; empty evidence ends in a calm stop,
          never an invented answer.
        </p>
        <ol className="workflow-steps" aria-label="How to use the workspace">
          {WORKFLOW_STEPS.map((w) => (
            <li key={w.n}>
              <span className="step-n">{w.n}</span>
              <span className="step-t">{w.t}</span>
              <span className="step-d">{w.d}</span>
            </li>
          ))}
        </ol>
      </section>
    </>
  );
}