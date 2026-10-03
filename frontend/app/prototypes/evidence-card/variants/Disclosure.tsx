"use client";

import { useState } from "react";
import { EVIDENCE } from "../fixture";

/**
 * DISCLOSURE — axis: interaction model, staged press-to-expand.
 * Collapsed to claim + confidence; expanding stages metrics (50ms),
 * then provenance (120ms), then sources (185ms). A different motion story
 * for the same facts.
 */
export function Disclosure() {
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
      <style>{DISCLOSURE_CSS}</style>
      {EVIDENCE.map((ev, i) => {
        const pct = Math.round(ev.confidence * 100);
        const isOpen = open.has(i);
        return (
          <article
            key={ev.metric}
            className="pevd-card"
            style={{ animationDelay: `${Math.min(i * 50, 150)}ms` }}
            data-open={isOpen}
            data-highlight={ev.sources.some((s) => s.publication_id === highlight)}
          >
            <button
              type="button"
              className="pevd-head"
              aria-expanded={isOpen}
              onClick={() => toggle(i)}
            >
              <span className="pevd-ring" role="img" aria-label={`Confidence ${pct} percent`}>
                <svg viewBox="0 0 28 28" aria-hidden="true">
                  <circle cx="14" cy="14" r="11" className="pevd-ring-bg" />
                  <circle
                    cx="14"
                    cy="14"
                    r="11"
                    className="pevd-ring-fg"
                    style={{ strokeDashoffset: `${69.1 * (1 - ev.confidence)}` }}
                  />
                </svg>
                <span>{pct}</span>
              </span>
              <span className="pevd-head-text">
                <span className="pevd-kicker">
                  E{i + 1} · {ev.metric}
                </span>
                <span className="pevd-claim">{ev.claim}</span>
              </span>
              <span className="pevd-chev" aria-hidden="true">
                <svg width="14" height="14" viewBox="0 0 14 14">
                  <path
                    d="M3 5l4 4 4-4"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="1.8"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  />
                </svg>
              </span>
            </button>
            <div className="pevd-body" data-open={isOpen}>
              <div className="pevd-body-inner">
                <div className="pevd-stage pevd-stage-1">
                  <span>
                    Value <strong>{ev.value}</strong>
                  </span>
                  <span>
                    Period <strong>{ev.period}</strong>
                  </span>
                </div>
                <p className="pevd-stage pevd-stage-2">{ev.provenance}</p>
                <div className="pevd-stage pevd-stage-3">
                  {ev.sources.map((s) => (
                    <button
                      key={s.publication_id}
                      type="button"
                      className="pevd-src"
                      data-active={highlight === s.publication_id}
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

const DISCLOSURE_CSS = `
.pevd-card {
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.85), rgba(255, 255, 255, 0.6));
  border: 1px solid var(--liquid-border);
  border-radius: 12px;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.8), 0 1px 4px rgba(23, 23, 23, 0.05);
  overflow: hidden;
  transform-origin: top center;
  animation: pevd-in 220ms cubic-bezier(0.23, 1, 0.32, 1) both;
}
@keyframes pevd-in {
  from { opacity: 0; transform: translateY(7px); filter: blur(2px); }
  to { opacity: 1; transform: translateY(0); filter: blur(0); }
}
.pevd-card[data-highlight="true"] { border-color: var(--accent); }
.pevd-head {
  display: flex; align-items: center; gap: 12px; width: 100%;
  border: none; background: none; cursor: pointer; text-align: left;
  padding: 12px 14px;
  transition: transform 160ms cubic-bezier(0.23, 1, 0.32, 1);
}
.pevd-head:active { transform: scale(0.97); }
.pevd-ring { position: relative; width: 40px; height: 40px; flex: none; display: grid; place-items: center; }
.pevd-ring svg { position: absolute; inset: 0; width: 100%; height: 100%; transform: rotate(-90deg); }
.pevd-ring-bg { fill: none; stroke: #e5e5e1; stroke-width: 3.5; }
.pevd-ring-fg {
  fill: none; stroke: #171717; stroke-width: 3.5; stroke-linecap: round;
  stroke-dasharray: 69.1;
  transition: stroke-dashoffset 220ms cubic-bezier(0.23, 1, 0.32, 1);
}
.pevd-ring > span { font-family: var(--font-mono); font-size: 10.5px; font-weight: 600; color: var(--text-primary); }
.pevd-head-text { display: flex; flex-direction: column; gap: 2px; min-width: 0; flex: 1; }
.pevd-kicker { font-family: var(--font-mono); font-size: 10.5px; color: var(--text-muted); }
.pevd-claim { font-size: 13px; font-weight: 600; line-height: 1.45; color: var(--text-primary); }
.pevd-chev { color: var(--text-muted); flex: none; transition: transform 220ms cubic-bezier(0.23, 1, 0.32, 1); }
.pevd-card[data-open="true"] .pevd-chev { transform: rotate(180deg); }
.pevd-body {
  display: grid; grid-template-rows: 0fr; opacity: 0;
  transition: grid-template-rows 150ms cubic-bezier(0.23, 1, 0.32, 1), opacity 150ms ease;
}
.pevd-body[data-open="true"] {
  grid-template-rows: 1fr; opacity: 1;
  transition: grid-template-rows 220ms cubic-bezier(0.23, 1, 0.32, 1), opacity 180ms ease;
}
.pevd-body-inner { overflow: hidden; min-height: 0; padding: 0 14px; }
.pevd-body[data-open="true"] .pevd-body-inner { padding-bottom: 12px; }
.pevd-stage { opacity: 0; transform: translateY(4px); }
.pevd-body[data-open="true"] .pevd-stage {
  opacity: 1; transform: none;
  transition: opacity 180ms ease, transform 220ms cubic-bezier(0.23, 1, 0.32, 1);
}
.pevd-body[data-open="true"] .pevd-stage-1 { transition-delay: 50ms; }
.pevd-body[data-open="true"] .pevd-stage-2 { transition-delay: 120ms; }
.pevd-body[data-open="true"] .pevd-stage-3 { transition-delay: 185ms; }
.pevd-stage-1 {
  display: flex; gap: 16px; font-size: 12px; color: var(--text-secondary);
  padding-top: 10px; border-top: 1px solid rgba(23, 23, 23, 0.07);
}
.pevd-stage-1 strong { font-family: var(--font-mono); font-size: 12px; color: var(--text-primary); }
.pevd-stage-2 { font-family: var(--font-mono); font-size: 11px; color: var(--text-muted); margin: 8px 0 0; }
.pevd-stage-3 { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 8px; }
.pevd-src {
  font-family: var(--font-mono); font-size: 11px;
  border: 1px solid var(--liquid-border);
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.9), rgba(255, 255, 255, 0.6));
  border-radius: 999px; padding: 4px 11px; cursor: pointer; color: var(--text-secondary);
  transition: border-color 180ms ease, color 180ms ease;
}
.pevd-src:hover { border-color: var(--accent); color: var(--accent-ink); }
.pevd-src[data-active="true"] { border-color: var(--accent); color: var(--accent-ink); box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.12); }
@media (prefers-reduced-motion: reduce) {
  .pevd-card { animation: none; }
  .pevd-body, .pevd-body[data-open="true"],
  .pevd-body[data-open="true"] .pevd-stage { transition: opacity 150ms ease; transform: none; }
}
`;
