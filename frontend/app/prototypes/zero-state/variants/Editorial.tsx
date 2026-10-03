"use client";

import { useState } from "react";
import { ZERO } from "../fixture";

/**
 * EDITORIAL — axis: narrative weight, one way forward.
 * A Newsreader headline that names the outcome, a short lede, and a single
 * primary next question. For the moment that deserves gravity, not a grid.
 */
export function Editorial({ query, onTry }: { query: string; onTry: (q: string) => void }) {
  const [idx, setIdx] = useState(0);
  const next = ZERO.seeds[idx % ZERO.seeds.length]!;

  const advance = () => {
    onTry(next.question);
    setIdx((i) => i + 1);
  };

  return (
    <>
      <style>{EDITORIAL_CSS}</style>
      <section className="pze-card" aria-labelledby="pze-title">
        <p className="pze-kicker">Zero records · no invention</p>
        <h2 id="pze-title">Nothing in the corpus answers this.</h2>
        <p className="pze-lede">
          “{query}” matched nothing above the retrieval gates, so the workspace stopped
          before any language model was called. That restraint is the feature.
        </p>
        <button type="button" className="pze-primary" onClick={advance}>
          Ask instead: {next.label} →
        </button>
        <div className="pze-alt">
          {ZERO.seeds.map((s) => (
            <button key={s.label} type="button" onClick={() => onTry(s.question)}>
              {s.label}
            </button>
          ))}
        </div>
      </section>
    </>
  );
}

const EDITORIAL_CSS = `
.pze-card {
  text-align: left;
  padding: 36px 8px;
  animation: pze-in 220ms cubic-bezier(0.23, 1, 0.32, 1) both;
}
@keyframes pze-in {
  from { opacity: 0; transform: translateY(6px); }
  to { opacity: 1; transform: translateY(0); }
}
.pze-kicker {
  font-size: 11px; font-weight: 600; letter-spacing: 0.08em; text-transform: uppercase;
  color: var(--text-muted); margin: 0 0 12px;
}
.pze-card h2 {
  font-family: var(--font-display); font-size: 32px; line-height: 1.2;
  letter-spacing: -0.015em; font-weight: 500; margin: 0; text-wrap: balance;
  max-width: 20ch;
}
.pze-lede { color: var(--text-secondary); font-size: 14.5px; line-height: 1.65; margin: 14px 0 0; max-width: 52ch; }
.pze-primary {
  display: inline-flex; align-items: center; gap: 8px;
  margin-top: 22px;
  background: #171717; color: #fff; border: 1px solid #171717;
  border-radius: 12px; padding: 12px 20px; font-weight: 600; font-size: 13.5px; cursor: pointer;
  transition: background 180ms ease, transform 160ms cubic-bezier(0.23, 1, 0.32, 1);
}
.pze-primary:hover { background: #262626; }
.pze-primary:active { transform: scale(0.97); }
.pze-alt { display: flex; gap: 16px; margin-top: 16px; flex-wrap: wrap; }
.pze-alt button {
  background: none; border: none; padding: 0; cursor: pointer;
  font-size: 12.5px; color: var(--text-muted);
  transition: color 180ms ease;
}
.pze-alt button:hover { color: var(--accent-ink); text-decoration: underline; text-underline-offset: 3px; }
@media (prefers-reduced-motion: reduce) {
  .pze-card { animation: none; }
}
`;
