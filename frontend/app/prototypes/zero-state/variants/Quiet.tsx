"use client";

import { ZERO } from "../fixture";

/**
 * QUIET — axis: minimal motion, structured calm.
 * The current zero-state language refined: bordered fact cells, static
 * layout, opacity-only entrance. A valid outcome, not an error.
 */
export function Quiet({ query, onTry }: { query: string; onTry: (q: string) => void }) {
  return (
    <>
      <style>{QUIET_CSS}</style>
      <section className="pzq-card" aria-labelledby="pzq-title">
        <h2 id="pzq-title">No supporting evidence found</h2>
        <p className="pzq-answer">{ZERO.route_reasoning}</p>
        <div className="pzq-grid">
          <div className="pzq-cell">
            <h3>What was searched</h3>
            <p>
              “{query}” via <span className="pzq-mono">[{ZERO.route}]</span> over Scopus
              publications, embedded chunks, and collaboration edges.
            </p>
          </div>
          <div className="pzq-cell">
            <h3>Why it stopped</h3>
            <p>
              The evidence gate admitted zero records, so synthesis was skipped
              deterministically — no language model was called.
            </p>
          </div>
        </div>
        <div className="pzq-actions">
          {ZERO.seeds.slice(0, 2).map((s) => (
            <button key={s.label} type="button" className="pzq-seed" onClick={() => onTry(s.question)}>
              Try: {s.label}
            </button>
          ))}
        </div>
        <p className="pzq-status">
          status: not_found · route: {ZERO.route} · short-circuit, no LLM call
        </p>
      </section>
    </>
  );
}

const QUIET_CSS = `
.pzq-card {
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.82), rgba(255, 255, 255, 0.58));
  border: 1px solid var(--liquid-border);
  border-radius: var(--radius-lg);
  box-shadow: var(--liquid-specular), var(--liquid-shadow);
  padding: 22px 24px;
  animation: pzq-in 200ms cubic-bezier(0.23, 1, 0.32, 1) both;
}
@keyframes pzq-in {
  from { opacity: 0; }
  to { opacity: 1; }
}
.pzq-card h2 { margin: 0 0 8px; font-size: 15px; font-weight: 600; letter-spacing: -0.01em; }
.pzq-answer { margin: 0 0 8px; color: var(--text-secondary); font-size: 13.5px; line-height: 1.6; max-width: 64ch; }
.pzq-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin: 16px 0 4px; }
.pzq-cell {
  border: 1px solid var(--liquid-border); border-radius: 10px;
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.75), rgba(255, 255, 255, 0.5));
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.8);
  padding: 12px 14px;
}
.pzq-cell h3 { margin: 0 0 6px; font-size: 11px; font-weight: 650; letter-spacing: 0.07em; text-transform: uppercase; color: var(--text-muted); }
.pzq-cell p { font-size: 13px; line-height: 1.55; color: var(--text-secondary); margin: 0; }
.pzq-mono { font-family: var(--font-mono); font-size: 12px; }
.pzq-actions { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 14px; }
.pzq-seed {
  border: 1px solid var(--liquid-border);
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.85), rgba(255, 255, 255, 0.55));
  border-radius: 999px; padding: 7px 13px; font-size: 12.5px; color: var(--text-secondary);
  cursor: pointer;
  transition: border-color 180ms ease, color 180ms ease;
}
.pzq-seed:hover { border-color: var(--border-strong); color: var(--text-primary); }
.pzq-seed:active { transform: scale(0.97); }
.pzq-status { font-family: var(--font-mono); color: var(--text-muted); font-size: 12px; margin: 12px 0 0; }
@media (max-width: 640px) { .pzq-grid { grid-template-columns: 1fr; } }
@media (prefers-reduced-motion: reduce) {
  .pzq-card { animation: none; }
}
`;
