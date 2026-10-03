"use client";

import { useState } from "react";
import { SOURCES } from "../fixture";

/**
 * INSTRUMENT — axis: density, metrics-first row.
 * Tabular relevance always visible, one mono fact line, actions always on
 * as compact buttons. For scanning a long bibliography fast.
 */
export function Instrument() {
  const [highlight, setHighlight] = useState<string | null>(null);
  const [copied, setCopied] = useState<string | null>(null);

  const copyDoi = async (s: (typeof SOURCES)[number]) => {
    const text = s.doi ? `https://doi.org/${s.doi}` : s.publication_id;
    try {
      await navigator.clipboard.writeText(text);
      setCopied(s.publication_id);
      setTimeout(() => setCopied((c) => (c === s.publication_id ? null : c)), 1400);
    } catch {
      setCopied(null);
    }
  };

  return (
    <>
      <style>{INSTRUMENT_CSS}</style>
      {SOURCES.map((s, i) => {
        const href = s.doi ? `https://doi.org/${s.doi}` : null;
        const pct = Math.round(s.relevance_score * 100);
        return (
          <article
            key={s.publication_id}
            className="psi-item"
            style={{ animationDelay: `${Math.min(i * 50, 150)}ms` }}
            data-highlight={highlight === s.publication_id}
          >
            <div className="psi-row">
              <span className="psi-score" title="Relevance score">
                {s.relevance_score.toFixed(2)}
              </span>
              <div className="psi-main">
                <button
                  type="button"
                  className="psi-title"
                  onClick={() =>
                    setHighlight((h) => (h === s.publication_id ? null : s.publication_id))
                  }
                  title="Highlight linked evidence"
                >
                  {s.title}
                </button>
                <p className="psi-facts">
                  {s.source_type} · {s.year ?? "—"} · {href ? `DOI:${s.doi}` : "no-doi"} ·{" "}
                  {s.publication_id}
                </p>
              </div>
              <div className="psi-acts">
                <button type="button" className="psi-btn" onClick={() => copyDoi(s)} title="Copy">
                  {copied === s.publication_id ? "✓" : "⧉"}
                </button>
                {href && (
                  <a className="psi-btn" href={href} target="_blank" rel="noreferrer" title="Open DOI">
                    ↗
                  </a>
                )}
              </div>
            </div>
            <div className="psi-track" role="img" aria-label={`Relevance ${pct} percent`}>
              <div style={{ transform: `scaleX(${s.relevance_score})` }} />
            </div>
          </article>
        );
      })}
    </>
  );
}

const INSTRUMENT_CSS = `
.psi-item {
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.85), rgba(255, 255, 255, 0.6));
  border: 1px solid var(--liquid-border);
  border-radius: 10px;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.8);
  padding: 9px 12px 10px;
  animation: psi-in 200ms cubic-bezier(0.23, 1, 0.32, 1) both;
}
@keyframes psi-in {
  from { opacity: 0; transform: translateY(4px); }
  to { opacity: 1; transform: translateY(0); }
}
.psi-item[data-highlight="true"] { border-color: var(--accent); box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.12); }
.psi-row { display: flex; align-items: flex-start; gap: 10px; }
.psi-score {
  font-family: var(--font-mono); font-size: 14px; font-weight: 600; color: #171717;
  font-variant-numeric: tabular-nums; flex: none; min-width: 38px;
}
.psi-main { flex: 1; min-width: 0; }
.psi-title {
  background: none; border: none; padding: 0; cursor: pointer; text-align: left;
  font-size: 12.5px; font-weight: 600; line-height: 1.45; color: var(--text-primary);
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
}
.psi-title:hover { color: var(--accent-ink); }
.psi-facts {
  font-family: var(--font-mono); font-size: 10.5px; color: var(--text-muted);
  margin: 3px 0 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.psi-acts { display: flex; gap: 4px; flex: none; }
.psi-btn {
  display: grid; place-items: center; width: 26px; height: 26px;
  border: 1px solid var(--border-default); background: #fff; border-radius: 7px;
  font-size: 12px; color: var(--text-secondary); cursor: pointer; text-decoration: none;
  transition: border-color 180ms ease, color 180ms ease, transform 160ms cubic-bezier(0.23, 1, 0.32, 1);
}
.psi-btn:hover { border-color: var(--accent); color: var(--accent-ink); }
.psi-btn:active { transform: scale(0.97); }
.psi-track { height: 3px; border-radius: 999px; background: #ececea; overflow: hidden; margin-top: 8px; }
.psi-track > div { height: 100%; background: var(--accent); border-radius: 999px; opacity: 0.85; transform-origin: left center; }
@media (prefers-reduced-motion: reduce) {
  .psi-item { animation: none; }
}
`;
