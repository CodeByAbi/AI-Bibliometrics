"use client";

import { useState } from "react";
import { Picker, usePrototypePicker } from "../picker";
import { Quiet } from "./variants/Quiet";
import { Instrument } from "./variants/Instrument";
import { Editorial } from "./variants/Editorial";
import { ZERO } from "./fixture";

const VARIANTS = [
  { name: "Quiet", axis: "Minimal motion, structured calm", render: (q: string, t: (s: string) => void) => <Quiet query={q} onTry={t} /> },
  { name: "Instrument", axis: "Diagnostic readout first", render: (q: string, t: (s: string) => void) => <Instrument query={q} onTry={t} /> },
  { name: "Editorial", axis: "Narrative weight, one way forward", render: (q: string, t: (s: string) => void) => <Editorial query={q} onTry={t} /> },
] as const;

export default function ZeroStatePrototypes() {
  const { current, setActive, replayKey, replay, ready } = usePrototypePicker(VARIANTS.length);
  const [query, setQuery] = useState(ZERO.question);
  const active = VARIANTS[current]!;

  return (
    <div className="pzz-page">
      <style>{STAGE_CSS}</style>

      <div className="pzz-col" aria-label="Zero-state (prototype context)">
        <p className="pzz-asked">
          <span aria-hidden="true">Q</span> “{query}”
        </p>
        <div key={`${current}-${replayKey}`}>{active.render(query, setQuery)}</div>
      </div>
      <p className="pzz-caption">
        {current + 1} / {VARIANTS.length} — {active.name}: {active.axis}
      </p>

      <Picker
        names={VARIANTS.map((v) => v.name)}
        current={current}
        ready={ready}
        onSelect={setActive}
        onReplay={replay}
      />
    </div>
  );
}

const STAGE_CSS = `
.pzz-page {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 48px 20px 120px;
}
.pzz-col { width: 100%; max-width: 720px; }
.pzz-asked {
  display: flex; gap: 10px; align-items: baseline;
  font-size: 13px; color: var(--text-secondary);
  border-top: 1px solid rgba(23, 23, 23, 0.07);
  padding-top: 12px; margin: 0 0 20px;
}
.pzz-asked span {
  font-family: var(--font-mono); font-size: 11px; color: var(--text-muted);
}
.pzz-caption {
  margin-top: 16px; font-size: 12px; color: var(--text-muted);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}
`;
