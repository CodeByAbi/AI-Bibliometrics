"use client";

import { useState } from "react";
import { ZERO } from "../fixture";

/**
 * INSTRUMENT — axis: diagnostic readout first.
 * A gate report table (route, cosine gate, chunks scanned, admitted, LLM
 * calls = 0) above recovery. For understanding exactly where retrieval stopped.
 */
const ROWS: Array<[string, string]> = [
  ["route", ZERO.route],
  ["cosine gate", "≥ 0.65"],
  ["chunks scanned", "1,284"],
  ["evidence admitted", "0"],
  ["LLM calls", "0"],
];

export function Instrument({ query, onTry }: { query: string; onTry: (q: string) => void }) {
  const [copied, setCopied] = useState(false);

  const copyId = async () => {
    try {
      await navigator.clipboard.writeText(ZERO.request_id);
      setCopied(true);
      setTimeout(() => setCopied(false), 1400);
    } catch {
      setCopied(false);
    }
  };

  return (
    <>
      <style>{INSTRUMENT_CSS}</style>
      <section className="pzi-card" aria-labelledby="pzi-title">
        <div className="pzi-head">
          <h2 id="pzi-title">Gate report — zero records admitted</h2>
          <button type="button" className="pzi-copy" onClick={copyId} title="Copy request ID">
            {copied ? "Copied" : ZERO.request_id}
          </button>
        </div>
        <table className="pzi-table" aria-label="Retrieval gate diagnostics">
          <tbody>
            {ROWS.map(([k, v]) => (
              <tr key={k}>
                <th scope="row">{k}</th>
                <td>{v}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="pzi-q">
          “{query}” — {ZERO.route_reasoning}
        </p>
        <div className="pzi-actions">
          {ZERO.seeds.map((s) => (
            <button key={s.label} type="button" className="pzi-seed" onClick={() => onTry(s.question)}>
              {s.label}
            </button>
          ))}
        </div>
      </section>
    </>
  );
}

const INSTRUMENT_CSS = `
.pzi-card {
  background: #0f0f0e; color: #e8e8e4;
  border: 1px solid #2c2c29; border-radius: var(--radius-lg);
  padding: 20px 22px;
  animation: pzi-in 200ms cubic-bezier(0.23, 1, 0.32, 1) both;
}
@keyframes pzi-in {
  from { opacity: 0; transform: translateY(4px); }
  to { opacity: 1; transform: translateY(0); }
}
.pzi-head { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.pzi-head h2 { margin: 0; font-size: 14px; font-weight: 600; letter-spacing: -0.01em; }
.pzi-copy {
  margin-left: auto; font-family: var(--font-mono); font-size: 11.5px;
  background: #1a1a18; color: #a1a19c; border: 1px solid #2c2c29;
  border-radius: 7px; padding: 4px 10px; cursor: pointer;
  transition: border-color 180ms ease, color 180ms ease;
}
.pzi-copy:hover { border-color: #4a4a45; color: #e8e8e4; }
.pzi-copy:active { transform: scale(0.97); }
.pzi-table { width: 100%; border-collapse: collapse; font-family: var(--font-mono); font-size: 12.5px; margin-top: 14px; }
.pzi-table th, .pzi-table td { text-align: left; padding: 7px 4px; border-bottom: 1px solid #232320; }
.pzi-table th { color: #a1a19c; font-weight: 500; width: 45%; }
.pzi-table td { text-align: right; font-variant-numeric: tabular-nums; }
.pzi-table tr:last-child th, .pzi-table tr:last-child td { border-bottom: none; }
.pzi-q { font-size: 12.5px; line-height: 1.6; color: #a1a19c; margin: 14px 0 0; max-width: 64ch; }
.pzi-actions { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 14px; }
.pzi-seed {
  border: 1px solid #3a3a36; background: #1a1a18; color: #e8e8e4;
  border-radius: 999px; padding: 7px 14px; font-size: 12.5px; cursor: pointer;
  transition: border-color 180ms ease, background 180ms ease, transform 160ms cubic-bezier(0.23, 1, 0.32, 1);
}
.pzi-seed:hover { border-color: #6b6b64; background: #232320; }
.pzi-seed:active { transform: scale(0.97); }
@media (prefers-reduced-motion: reduce) {
  .pzi-card { animation: none; }
}
`;
