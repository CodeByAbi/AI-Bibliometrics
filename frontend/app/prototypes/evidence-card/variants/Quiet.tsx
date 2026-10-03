"use client";

import { useState } from "react";
import { EVIDENCE } from "../fixture";

/**
 * QUIET — axis: minimal motion, borders over shadows.
 * Flat paper cards, no hover lift, opacity-only entrances, detail expands
 * in place. For the daily-use instrument that should never shout.
 */
export function Quiet() {
  const [open, setOpen] = useState<Set<number>>(new Set([0]));
  const [highlight, setHighlight] = useState<string | null>(null);

  const toggle = (i: number) =>
    setOpen((prev) => {
      const next = new Set(prev);
      if (next.has(i)) next.delete(i);
      else next.add(i);
      return next;
    });

  return (
    <>
      <style>{QUIET_CSS}</style>
      {EVIDENCE.map((ev, i) => {
        const pct = Math.round(ev.confidence * 100);
        const isOpen = open.has(i);
        return (
          <article
            key={ev.metric}
            className="pevq-card"
            style={{ animationDelay: `${Math.min(i * 50, 100)}ms` }}
            data-highlight={ev.sources.some((s) => s.publication_id === highlight)}
          >
            <div className="pevq-top">
              <span className="pevq-index">E{i + 1}</span>
              <span className="pevq-metric">{ev.metric}</span>
              <button
                type="button"
                className="pevq-toggle"
                aria-expanded={isOpen}
                onClick={() => toggle(i)}
              >
                {isOpen ? "Hide" : "Detail"}
              </button>
            </div>
            <p className="pevq-claim">{ev.claim}</p>
            <div className="pevq-conf">
              <div
                className="pevq-track"
                role="img"
                aria-label={`Confidence ${pct} percent`}
              >
                <div
                  className="pevq-fill"
                  style={{ transform: `scaleX(${ev.confidence})` }}
                />
              </div>
              <span className="pevq-pct">{pct}%</span>
            </div>
            <div className="pevq-detail" data-open={isOpen}>
              <div className="pevq-detail-inner">
                <dl className="pevq-facts">
                  <div>
                    <dt>Value</dt>
                    <dd>{ev.value}</dd>
                  </div>
                  <div>
                    <dt>Period</dt>
                    <dd>{ev.period}</dd>
                  </div>
                </dl>
                <p className="pevq-prov">{ev.provenance}</p>
                <div className="pevq-links">
                  {ev.sources.map((s) => (
                    <button
                      key={s.publication_id}
                      type="button"
                      className="pevq-link"
                      title={s.title}
                      onClick={() =>
                        setHighlight((h) =>
                          h === s.publication_id ? null : s.publication_id,
                        )
                      }
                    >
                      {s.publication_id}
                    </button>
                  ))}
                </div>
              </div>
            </div>
          </article>
        );
      })}
    </>
  );
}

const QUIET_CSS = `
.pevq-card {
  background: #fff;
  border: 1px solid var(--border-default);
  border-radius: 10px;
  padding: 12px 14px;
  animation: pevq-in 180ms cubic-bezier(0.23, 1, 0.32, 1) both;
}
@keyframes pevq-in {
  from { opacity: 0; }
  to { opacity: 1; }
}
.pevq-card[data-highlight="true"] { border-color: var(--accent); box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.12); }
.pevq-top { display: flex; align-items: center; gap: 8px; }
.pevq-index {
  font-family: var(--font-mono); font-size: 11px; font-weight: 600;
  border: 1px solid var(--border-default); border-radius: 6px;
  padding: 1px 7px; color: var(--text-secondary);
}
.pevq-metric { font-family: var(--font-mono); font-size: 11.5px; color: var(--text-muted); }
.pevq-toggle {
  margin-left: auto; border: 1px solid var(--border-default); background: #fff;
  border-radius: 999px; font-size: 11.5px; color: var(--text-secondary);
  padding: 3px 10px; cursor: pointer;
  transition: border-color 180ms ease, color 180ms ease;
}
.pevq-toggle:hover { border-color: var(--border-strong); color: var(--text-primary); }
.pevq-toggle:active { transform: scale(0.97); }
.pevq-claim { font-size: 13.5px; font-weight: 600; line-height: 1.5; margin: 8px 0 10px; color: var(--text-primary); }
.pevq-conf { display: flex; align-items: center; gap: 10px; }
.pevq-track { flex: 1; height: 4px; border-radius: 999px; background: #ececea; overflow: hidden; }
.pevq-fill { height: 100%; background: #171717; border-radius: 999px; transform-origin: left center; }
.pevq-pct { font-family: var(--font-mono); font-size: 12px; color: var(--text-primary); font-variant-numeric: tabular-nums; }
.pevq-detail {
  display: grid; grid-template-rows: 0fr; opacity: 0;
  transition: grid-template-rows 150ms cubic-bezier(0.23, 1, 0.32, 1), opacity 150ms ease;
}
.pevq-detail[data-open="true"] {
  grid-template-rows: 1fr; opacity: 1;
  transition: grid-template-rows 220ms cubic-bezier(0.23, 1, 0.32, 1), opacity 180ms ease;
}
.pevq-detail-inner { overflow: hidden; min-height: 0; }
.pevq-facts { display: flex; gap: 20px; margin: 10px 0 0; padding-top: 10px; border-top: 1px solid var(--border-default); }
.pevq-facts dt { font-size: 10.5px; text-transform: uppercase; letter-spacing: 0.07em; color: var(--text-muted); font-weight: 600; }
.pevq-facts dd { margin: 2px 0 0; font-family: var(--font-mono); font-size: 12.5px; color: var(--text-primary); }
.pevq-prov { font-size: 11.5px; color: var(--text-muted); margin: 8px 0 0; }
.pevq-links { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 8px; }
.pevq-link {
  font-family: var(--font-mono); font-size: 11px;
  border: 1px solid var(--border-default); background: #fff; border-radius: 999px;
  padding: 3px 10px; cursor: pointer; color: var(--text-secondary);
  transition: border-color 180ms ease, color 180ms ease;
}
.pevq-link:hover { border-color: var(--accent); color: var(--accent-ink); }
@media (prefers-reduced-motion: reduce) {
  .pevq-card { animation: none; }
  .pevq-detail, .pevq-detail[data-open="true"] { transition: opacity 150ms ease; }
}
`;
