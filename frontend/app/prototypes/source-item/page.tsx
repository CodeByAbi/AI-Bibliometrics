"use client";

import { Picker, usePrototypePicker } from "../picker";
import { Quiet } from "./variants/Quiet";
import { Instrument } from "./variants/Instrument";
import { Editorial } from "./variants/Editorial";

const VARIANTS = [
  { name: "Quiet", axis: "Minimal motion, text-first restraint", render: () => <Quiet /> },
  { name: "Instrument", axis: "Density, metrics-first row", render: () => <Instrument /> },
  { name: "Editorial", axis: "Typography-led, relevance in words", render: () => <Editorial /> },
] as const;

export default function SourceItemPrototypes() {
  const { current, setActive, replayKey, replay, ready } = usePrototypePicker(VARIANTS.length);
  const active = VARIANTS[current]!;

  return (
    <div className="pss-page">
      <style>{STAGE_CSS}</style>

      <div className="pss-rail" aria-label="Sources (prototype context)">
        <div className="pss-rail-head">
          Sources <span className="pss-count">3</span>
        </div>
        <div className="pss-rail-body" key={`${current}-${replayKey}`}>
          {active.render()}
        </div>
      </div>
      <p className="pss-caption">
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
.pss-page {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 48px 20px 120px;
}
.pss-rail {
  width: 100%;
  max-width: 420px;
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.8), rgba(255, 255, 255, 0.55));
  border: 1px solid var(--liquid-border);
  border-radius: var(--radius-lg);
  box-shadow: var(--liquid-specular), var(--liquid-shadow);
  overflow: hidden;
}
.pss-rail-head {
  display: flex; align-items: center; gap: 8px;
  padding: 12px 16px;
  border-bottom: 1px solid rgba(23, 23, 23, 0.07);
  font-weight: 600; font-size: 13.5px; color: var(--text-primary);
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.5), rgba(255, 255, 255, 0));
}
.pss-count {
  margin-left: auto; font-family: var(--font-mono); font-size: 11.5px;
  font-weight: 600; color: var(--text-secondary);
}
.pss-rail-body { padding: 12px; display: flex; flex-direction: column; gap: 12px; }
.pss-caption {
  margin-top: 16px; font-size: 12px; color: var(--text-muted);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}
`;
