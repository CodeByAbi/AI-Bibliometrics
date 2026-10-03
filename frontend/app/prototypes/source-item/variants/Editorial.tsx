"use client";

import { useState } from "react";
import { SOURCES } from "../fixture";

function band(score: number): string {
  if (score >= 0.85) return "Strong match";
  if (score >= 0.7) return "Partial match";
  return "Weak tie";
}

/**
 * EDITORIAL — axis: typography-led, relevance in words.
 * Large Newsreader titles, generous whitespace, relevance as a plain-language
 * band instead of a number. For reading the bibliography, not scanning it.
 */
export function Editorial() {
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
      <style>{EDITORIAL_CSS}</style>
      {SOURCES.map((s, i) => {
        const href = s.doi ? `https://doi.org/${s.doi}` : null;
        return (
          <article
            key={s.publication_id}
            className="pse-item"
            style={{ animationDelay: `${Math.min(i * 60, 180)}ms` }}
            data-highlight={highlight === s.publication_id}
          >
            <p className="pse-band">{band(s.relevance_score)}</p>
            <button
              type="button"
              className="pse-title"
              onClick={() =>
                setHighlight((h) => (h === s.publication_id ? null : s.publication_id))
              }
              title="Highlight linked evidence"
            >
              {s.title}
            </button>
            <p className="pse-byline">
              {s.source_type} · {s.year ?? "no year"} · {s.publication_id}
            </p>
            <p className="pse-prov">{s.provenance}</p>
            <div className="pse-links">
              {href ? (
                <a href={href} target="_blank" rel="noreferrer">
                  DOI:{s.doi} →
                </a>
              ) : (
                <span className="pse-nodoi">No DOI registered</span>
              )}
              <button type="button" onClick={() => copyDoi(s)}>
                {copied === s.publication_id ? "Copied" : "Copy reference"}
              </button>
            </div>
          </article>
        );
      })}
    </>
  );
}

const EDITORIAL_CSS = `
.pse-item {
  padding: 18px 4px;
  border-bottom: 1px solid var(--border-default);
  animation: pse-in 220ms cubic-bezier(0.23, 1, 0.32, 1) both;
}
.pse-item:last-child { border-bottom: none; }
@keyframes pse-in {
  from { opacity: 0; transform: translateY(6px); }
  to { opacity: 1; transform: translateY(0); }
}
.pse-item[data-highlight="true"] .pse-title { color: var(--accent-ink); }
.pse-band {
  font-family: var(--font-mono); font-size: 10.5px; font-weight: 600;
  letter-spacing: 0.08em; text-transform: uppercase; color: var(--accent-ink);
  margin: 0 0 8px;
}
.pse-title {
  background: none; border: none; padding: 0; cursor: pointer; text-align: left;
  font-family: var(--font-display); font-size: 19px; line-height: 1.35;
  letter-spacing: -0.01em; font-weight: 500; color: var(--text-primary);
  text-wrap: balance;
  transition: color 180ms ease;
}
.pse-title:hover { color: var(--accent-ink); }
.pse-byline { font-size: 12.5px; color: var(--text-secondary); margin: 8px 0 0; }
.pse-prov { font-family: var(--font-mono); font-size: 11px; color: var(--text-muted); margin: 6px 0 0; }
.pse-links { display: flex; gap: 16px; align-items: center; margin-top: 10px; font-size: 12.5px; }
.pse-links a { color: var(--accent-ink); text-decoration: none; font-family: var(--font-mono); font-size: 11.5px; }
.pse-links a:hover { text-decoration: underline; text-underline-offset: 3px; }
.pse-links button {
  background: none; border: none; padding: 0; cursor: pointer;
  font-size: 12.5px; color: var(--text-muted);
  transition: color 180ms ease;
}
.pse-links button:hover { color: var(--text-primary); }
.pse-nodoi { font-family: var(--font-mono); font-size: 11.5px; color: var(--text-muted); }
@media (prefers-reduced-motion: reduce) {
  .pse-item { animation: none; }
}
`;
