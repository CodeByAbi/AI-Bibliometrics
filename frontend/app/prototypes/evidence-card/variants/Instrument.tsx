"use client";

import { useState } from "react";
import { EVIDENCE } from "../fixture";

/**
 * INSTRUMENT — axis: density, data-grid first.
 * All four facts visible upfront in a tabular grid, provenance always on,
 * sources as compact ID chips. For the analyst who scans, not reads.
 */
export function Instrument() {
  const [highlight, setHighlight] = useState<string | null>(null);
  const [copied, setCopied] = useState<string | null>(null);

  const copyId = async (id: string) => {
    try {
      await navigator.clipboard.writeText(id);
      setCopied(id);
      setTimeout(() => setCopied((c) => (c === id ? null : c)), 1400);
    } catch {
      setCopied(null);
    }
  };

  return (
    <>
      <style>{INSTRUMENT_CSS}</style>
      {EVIDENCE.map((ev, i) => {
        const pct = Math.round(ev.confidence * 100);
        return (
          <article
            key={ev.metric}
            className="pevi-card"
            style={{ animationDelay: `${Math.min(i * 50, 150)}ms` }}
            data-highlight={ev.sources.some((s) => s.publication_id === highlight)}
          >
            <div className="pevi-head">
              <span className="pevi-index">E{i + 1}</span>
              <p className="pevi-claim">{ev.claim}</p>
              <span className="pevi-pct">{pct}%</span>
            </div>
            <dl className="pevi-grid">
              <div>
                <dt>Metric</dt>
                <dd>{ev.metric}</dd>
              </div>
              <div>
                <dt>Value</dt>
                <dd>{ev.value}</dd>
              </div>
              <div>
                <dt>Period</dt>
                <dd>{ev.period}</dd>
              </div>
              <div>
                <dt>Conf</dt>
                <dd>
                  <span className="pevi-bar" role="img" aria-label={`Confidence ${pct} percent`}>
                    <span style={{ transform: `scaleX(${ev.confidence})` }} />
                  </span>
                </dd>
              </div>
            </dl>
            <p className="pevi-prov">{ev.provenance}</p>
            <div className="pevi-chips">
              {ev.sources.map((s) => (
                <span
                  key={s.publication_id}
                  className="pevi-chip"
                  data-active={highlight === s.publication_id}
                  title={s.title}
                >
                  <button
                    type="button"
                    className="pevi-chip-id"
                    onClick={() =>
                      setHighlight((h) =>
                        h === s.publication_id ? null : s.publication_id,
                      )
                    }
                  >
                    {s.publication_id}
                  </button>
                  <button
                    type="button"
                    className="pevi-chip-copy"
                    onClick={() => copyId(s.publication_id)}
                    title="Copy publication ID"
                  >
                    {copied === s.publication_id ? "Copied" : "Copy"}
                  </button>
                </span>
              ))}
            </div>
          </article>
        );
      })}
    </>
  );
}

const INSTRUMENT_CSS = `
.pevi-card {
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.85), rgba(255, 255, 255, 0.6));
  border: 1px solid var(--liquid-border);
  border-radius: 12px;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.8), 0 1px 4px rgba(23, 23, 23, 0.05);
  padding: 10px 12px;
  animation: pevi-in 200ms cubic-bezier(0.23, 1, 0.32, 1) both;
}
@keyframes pevi-in {
  from { opacity: 0; transform: translateY(4px); }
  to { opacity: 1; transform: translateY(0); }
}
.pevi-card[data-highlight="true"] { border-color: var(--accent); box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.12); }
.pevi-head { display: flex; align-items: baseline; gap: 8px; }
.pevi-index {
  font-family: var(--font-mono); font-size: 10.5px; font-weight: 600;
  color: var(--text-secondary); flex: none;
}
.pevi-claim { font-size: 12.5px; font-weight: 600; line-height: 1.45; margin: 0; color: var(--text-primary); flex: 1; }
.pevi-pct {
  font-family: var(--font-mono); font-size: 13px; font-weight: 600; color: #171717;
  font-variant-numeric: tabular-nums; flex: none;
}
.pevi-grid {
  display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 8px;
  margin: 10px 0 0; padding: 8px 0;
  border-top: 1px solid rgba(23, 23, 23, 0.07);
  border-bottom: 1px solid rgba(23, 23, 23, 0.07);
}
.pevi-grid dt { font-size: 9.5px; text-transform: uppercase; letter-spacing: 0.07em; color: var(--text-muted); font-weight: 600; }
.pevi-grid dd { margin: 3px 0 0; font-family: var(--font-mono); font-size: 11.5px; color: var(--text-primary); overflow: hidden; text-overflow: ellipsis; }
.pevi-bar { display: block; height: 4px; border-radius: 999px; background: #e5e5e1; overflow: hidden; margin-top: 5px; }
.pevi-bar > span { display: block; height: 100%; background: var(--accent); border-radius: 999px; transform-origin: left center; }
.pevi-prov { font-family: var(--font-mono); font-size: 10.5px; color: var(--text-muted); margin: 8px 0 0; }
.pevi-chips { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 8px; }
.pevi-chip {
  display: inline-flex; align-items: center;
  border: 1px solid var(--border-default); border-radius: 7px; background: #fff;
  font-family: var(--font-mono); font-size: 10.5px; overflow: hidden;
  transition: border-color 180ms ease;
}
.pevi-chip[data-active="true"] { border-color: var(--accent); box-shadow: 0 0 0 2px rgba(37, 99, 235, 0.15); }
.pevi-chip-id {
  border: none; background: none; cursor: pointer; font: inherit;
  color: var(--text-secondary); padding: 4px 4px 4px 9px;
}
.pevi-chip-id:hover { color: var(--accent-ink); }
.pevi-chip-copy {
  border: none; border-left: 1px solid var(--border-default); background: none; cursor: pointer;
  font: inherit; color: var(--text-muted); padding: 4px 9px 4px 7px;
}
.pevi-chip-copy:hover { color: var(--text-primary); }
@media (prefers-reduced-motion: reduce) {
  .pevi-card { animation: none; }
}
`;
