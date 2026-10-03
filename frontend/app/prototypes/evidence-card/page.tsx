"use client";

import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import { Quiet } from "./variants/Quiet";
import { Instrument } from "./variants/Instrument";
import { Disclosure } from "./variants/Disclosure";

const VARIANTS = [
  { name: "Quiet", axis: "Minimal motion, borders over shadows", render: () => <Quiet /> },
  { name: "Instrument", axis: "Density, data-grid first", render: () => <Instrument /> },
  { name: "Disclosure", axis: "Interaction model, staged press-to-expand", render: () => <Disclosure /> },
] as const;

/**
 * Prototype harness — isolated surface, nothing here imports into
 * production code. Picker markup + styles are verbatim per PICKER.md;
 * behavior is the same contract expressed with React state + keyed re-mount.
 */
export default function EvidenceCardPrototypes() {
  const [current, setCurrent] = useState(0);
  const [replayKey, setReplayKey] = useState(0);
  const [ready, setReady] = useState(false);
  const pickerRef = useRef<HTMLElement>(null);
  const highlightRef = useRef<HTMLSpanElement>(null);
  const itemRefs = useRef<Array<HTMLButtonElement | null>>([]);

  // Selection persists via ?v=N, falling back to variant 1.
  useEffect(() => {
    const v = parseInt(new URLSearchParams(window.location.search).get("v") ?? "", 10);
    if (v >= 1 && v <= VARIANTS.length) setCurrent(v - 1);
    let raf2 = 0;
    const raf1 = requestAnimationFrame(() => {
      raf2 = requestAnimationFrame(() => setReady(true));
    });
    return () => {
      cancelAnimationFrame(raf1);
      cancelAnimationFrame(raf2);
    };
  }, []);

  const setActive = useCallback((i: number) => {
    if (i < 0 || i >= VARIANTS.length) return;
    setCurrent(i);
    const url = new URL(window.location.href);
    url.searchParams.set("v", String(i + 1));
    window.history.replaceState(null, "", url);
  }, []);

  const replay = useCallback(() => setReplayKey((k) => k + 1), []);

  // Sliding highlight follows the active item; no animation until first paint.
  useLayoutEffect(() => {
    const el = itemRefs.current[current];
    const hl = highlightRef.current;
    if (el && hl) {
      hl.style.width = `${el.offsetWidth}px`;
      hl.style.transform = `translateX(${el.offsetLeft}px)`;
    }
  }, [current, ready]);

  useEffect(() => {
    const onResize = () => {
      const el = itemRefs.current[current];
      const hl = highlightRef.current;
      if (el && hl) {
        hl.style.width = `${el.offsetWidth}px`;
        hl.style.transform = `translateX(${el.offsetLeft}px)`;
      }
    };
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, [current]);

  // Keys 1–N, arrows, R. Ignored in inputs and with modifiers.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      if (t && (/^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName) || t.isContentEditable)) return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      const num = parseInt(e.key, 10);
      if (num >= 1 && num <= VARIANTS.length) setActive(num - 1);
      else if (e.key === "ArrowRight") setActive((current + 1) % VARIANTS.length);
      else if (e.key === "ArrowLeft")
        setActive((current - 1 + VARIANTS.length) % VARIANTS.length);
      else if (e.key === "r" || e.key === "R") replay();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [current, setActive, replay]);

  const active = VARIANTS[current]!;

  return (
    <div className="pev-page">
      <style>{PICKER_CSS}</style>
      <style>{STAGE_CSS}</style>

      <div className="pev-rail" aria-label="Evidence and sources (prototype context)">
        <div className="pev-rail-head">
          Verified Evidence <span className="pev-count">3</span>
        </div>
        {/* Keyed re-mount so entrances re-run on switch + replay. Swap is instant. */}
        <div className="pev-rail-body" key={`${current}-${replayKey}`}>
          {active.render()}
        </div>
      </div>
      <p className="pev-caption">
        {current + 1} / {VARIANTS.length} — {active.name}: {active.axis}
      </p>

      <nav
        className="proto-picker"
        aria-label="Prototype variants"
        ref={pickerRef}
        data-ready={ready || undefined}
      >
        <span className="proto-picker-highlight" aria-hidden="true" ref={highlightRef} />
        {VARIANTS.map((v, i) => (
          <button
            key={v.name}
            ref={(el) => {
              itemRefs.current[i] = el;
            }}
            className="proto-picker-item"
            {...(i === current
              ? { "data-active": "", "aria-current": "true" as const }
              : {})}
            onClick={() => setActive(i)}
          >
            {v.name}
          </button>
        ))}
        <span className="proto-picker-divider" aria-hidden="true" />
        <button
          className="proto-picker-item proto-picker-replay"
          aria-label="Replay animation (R)"
          onClick={replay}
        >
          ↻
        </button>
      </nav>
    </div>
  );
}

// Verbatim picker styles per PICKER.md — not a design decision, never restyled.
const PICKER_CSS = `
.proto-picker {
  position: fixed;
  bottom: 24px;
  left: 50%;
  transform: translateX(-50%);
  z-index: 2147483647;
  display: flex;
  align-items: center;
  gap: 2px;
  padding: 4px;
  border-radius: 999px;
  background: rgba(10, 10, 10, 0.82);
  -webkit-backdrop-filter: blur(12px) saturate(1.4);
  backdrop-filter: blur(12px) saturate(1.4);
  box-shadow:
    0 0 0 1px rgba(255, 255, 255, 0.08) inset,
    0 8px 24px rgba(0, 0, 0, 0.24),
    0 2px 6px rgba(0, 0, 0, 0.12);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  font-size: 13px;
  line-height: 1;
  -webkit-font-smoothing: antialiased;
  user-select: none;
  -webkit-user-select: none;
}
.proto-picker-highlight {
  position: absolute;
  top: 4px;
  left: 0;
  height: 28px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.12);
  will-change: transform;
}
.proto-picker[data-ready] .proto-picker-highlight {
  transition:
    transform 250ms cubic-bezier(0.23, 1, 0.32, 1),
    width 250ms cubic-bezier(0.23, 1, 0.32, 1);
}
@media (prefers-reduced-motion: reduce) {
  .proto-picker[data-ready] .proto-picker-highlight { transition: none; }
}
.proto-picker-item {
  position: relative;
  display: flex;
  align-items: center;
  height: 28px;
  padding: 0 12px;
  border: 0;
  border-radius: 999px;
  background: transparent;
  color: rgba(255, 255, 255, 0.55);
  font: inherit;
  cursor: pointer;
  transition: color 150ms ease-out;
}
.proto-picker-item:hover { color: rgba(255, 255, 255, 0.85); }
.proto-picker-item:active { transform: scale(0.97); }
.proto-picker-item:focus-visible {
  outline: 2px solid rgba(255, 255, 255, 0.4);
  outline-offset: 2px;
}
.proto-picker-item[data-active] { color: #fff; }
.proto-picker-divider {
  width: 1px;
  height: 16px;
  margin: 0 4px;
  background: rgba(255, 255, 255, 0.12);
}
.proto-picker-replay { padding: 0 10px; font-size: 14px; }
.proto-picker[data-position="top"] { bottom: auto; top: 24px; }
`;

// Realistic surrounding context: the provenance rail shell the card ships in.
const STAGE_CSS = `
.pev-page {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 48px 20px 120px;
}
.pev-rail {
  width: 100%;
  max-width: 420px;
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.8), rgba(255, 255, 255, 0.55));
  border: 1px solid var(--liquid-border);
  border-radius: var(--radius-lg);
  box-shadow: var(--liquid-specular), var(--liquid-shadow);
  overflow: hidden;
}
.pev-rail-head {
  display: flex; align-items: center; gap: 8px;
  padding: 12px 16px;
  border-bottom: 1px solid rgba(23, 23, 23, 0.07);
  font-weight: 600; font-size: 13.5px; color: var(--text-primary);
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.5), rgba(255, 255, 255, 0));
}
.pev-count {
  margin-left: auto; font-family: var(--font-mono); font-size: 11.5px;
  font-weight: 600; color: var(--text-secondary);
}
.pev-rail-body { padding: 12px; display: flex; flex-direction: column; gap: 12px; }
.pev-caption {
  margin-top: 16px; font-size: 12px; color: var(--text-muted);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}
`;
