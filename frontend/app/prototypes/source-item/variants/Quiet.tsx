"use client";

import { useState } from "react";
import { SOURCES } from "../fixture";

/**
 * QUIET — axis: minimal motion, text-first restraint.
 * Title + one mono meta line; actions appear on hover/focus only;
 * opacity entrances, no lift. The bibliography that stays out of the way.
 */
export function Quiet() {
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
      <style>{QUIET_CSS}</style>
      {SOURCES.map((s, i) => {
        const href = s.doi ? `https://doi.org/${s.doi}` : null;
        return (
          <article
            key={s.publication_id}
            className="psq-item"
            style={{ animationDelay: `${Math.min(i * 50, 100)}ms` }}
            data-highlight={highlight === s.publication_id}
          >
            <button
              type="button"
              className="psq-title"
              onClick={() =>
                setHighlight((h) => (h === s.publication_id ? null : s.publication_id))
              }
              title="Highlight linked evidence"
            >
              {s.title}
            </button>
            <p className="psq-meta">
              <span className="psq-type">{s.source_type}</span>
              <span>{s.year ?? "—"}</span>
              <span>{s.relevance_score.toFixed(2)}</span>
              {href ? <span className="psq-doi">DOI:{s.doi}</span> : <span>no-doi</span>}
            </p>
            <div className="psq-actions">
              <button
                type="button"
                className="psq-act"
                onClick={() => copyDoi(s)}
                title={href ? "Copy DOI link" : "Copy publication ID"}
              >
                {copied === s.publication_id ? "Copied" : href ? "DOI" : "ID"}
              </button>
              {href && (
                <a
                  className="psq-act"
                  href={href}
                  target="_blank"
                  rel="noreferrer"
                  onClick={(e) => e.stopPropagation()}
                >
                  Open
                </a>
              )}
              <span className="psq-prov">{s.provenance}</span>
            </div>
          </article>
        );
      })}
    </>
  );
}

const QUIET_CSS = `
.psq-item {
  background: #fff;
  border: 1px solid var(--border-default);
  border-radius: 10px;
  padding: 12px 14px;
  animation: psq-in 180ms cubic-bezier(0.23, 1, 0.32, 1) both;
}
@keyframes psq-in {
  from { opacity: 0; }
  to { opacity: 1; }
}
.psq-item[data-highlight="true"] { border-color: var(--accent); box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.12); }
.psq-title {
  background: none; border: none; padding: 0; cursor: pointer; text-align: left;
  font-size: 13.5px; font-weight: 600; line-height: 1.5; color: var(--text-primary);
}
.psq-title:hover { color: var(--accent-ink); text-decoration: underline; text-underline-offset: 3px; }
.psq-meta {
  display: flex; gap: 8px; flex-wrap: wrap; align-items: center;
  font-family: var(--font-mono); font-size: 11.5px; color: var(--text-muted);
  margin: 6px 0 0;
}
.psq-type {
  font-size: 10.5px; font-weight: 600; border: 1px solid var(--border-default);
  border-radius: 6px; padding: 1px 7px; color: var(--text-secondary); text-transform: lowercase;
}
.psq-doi { color: var(--accent-ink); }
.psq-actions {
  display: flex; gap: 6px; align-items: center; margin-top: 9px;
  opacity: 0;
  transition: opacity 180ms ease;
}
.psq-item:hover .psq-actions, .psq-item:focus-within .psq-actions { opacity: 1; }
.psq-act {
  display: inline-flex; align-items: center;
  border: 1px solid var(--border-default); background: #fff; border-radius: 7px;
  font-size: 11px; color: var(--text-secondary); padding: 4px 9px; cursor: pointer;
  text-decoration: none;
  transition: border-color 180ms ease, color 180ms ease;
}
.psq-act:hover { border-color: var(--accent); color: var(--accent-ink); }
.psq-prov { font-family: var(--font-mono); font-size: 10.5px; color: var(--text-muted); margin-left: auto; }
@media (hover: none) { .psq-actions { opacity: 1; } }
@media (prefers-reduced-motion: reduce) {
  .psq-item { animation: none; }
  .psq-actions { transition: none; }
}
`;
