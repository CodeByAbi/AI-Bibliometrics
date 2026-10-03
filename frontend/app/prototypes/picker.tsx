"use client";

import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react";

/**
 * Shared prototype harness (PICKER.md contract, React idiom).
 * Prototype-only: nothing in production code imports this.
 * Per run, only the variant names + count change.
 */
export function usePrototypePicker(count: number) {
  const [current, setCurrentState] = useState(0);
  const [replayKey, setReplayKey] = useState(0);
  const [ready, setReady] = useState(false);

  const setActive = useCallback(
    (i: number) => {
      if (i < 0 || i >= count) return;
      setCurrentState(i);
      const url = new URL(window.location.href);
      url.searchParams.set("v", String(i + 1));
      window.history.replaceState(null, "", url);
    },
    [count],
  );

  const replay = useCallback(() => setReplayKey((k) => k + 1), []);

  useEffect(() => {
    const v = parseInt(new URLSearchParams(window.location.search).get("v") ?? "", 10);
    if (v >= 1 && v <= count) setCurrentState(v - 1);
    let raf2 = 0;
    const raf1 = requestAnimationFrame(() => {
      raf2 = requestAnimationFrame(() => setReady(true));
    });
    return () => {
      cancelAnimationFrame(raf1);
      cancelAnimationFrame(raf2);
    };
  }, [count]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      if (t && (/^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName) || t.isContentEditable)) return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      const num = parseInt(e.key, 10);
      if (num >= 1 && num <= count) setActive(num - 1);
      else if (e.key === "ArrowRight") setActive((current + 1) % count);
      else if (e.key === "ArrowLeft") setActive((current - 1 + count) % count);
      else if (e.key === "r" || e.key === "R") replay();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [current, count, setActive, replay]);

  return { current, setActive, replayKey, replay, ready };
}

interface PickerProps {
  names: readonly string[];
  current: number;
  ready: boolean;
  onSelect: (i: number) => void;
  onReplay: () => void;
}

export function Picker({ names, current, ready, onSelect, onReplay }: PickerProps) {
  const highlightRef = useRef<HTMLSpanElement>(null);
  const itemRefs = useRef<Array<HTMLButtonElement | null>>([]);

  useLayoutEffect(() => {
    const el = itemRefs.current[current];
    const hl = highlightRef.current;
    if (el && hl) {
      hl.style.width = `${el.offsetWidth}px`;
      hl.style.transform = `translateX(${el.offsetLeft}px)`;
    }
  }, [current, ready, names.length]);

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

  return (
    <>
      <style>{PICKER_CSS}</style>
      <nav
        className="proto-picker"
        aria-label="Prototype variants"
        data-ready={ready || undefined}
      >
        <span className="proto-picker-highlight" aria-hidden="true" ref={highlightRef} />
        {names.map((name, i) => (
          <button
            key={name}
            ref={(el) => {
              itemRefs.current[i] = el;
            }}
            className="proto-picker-item"
            {...(i === current
              ? { "data-active": "", "aria-current": "true" as const }
              : {})}
            onClick={() => onSelect(i)}
          >
            {name}
          </button>
        ))}
        <span className="proto-picker-divider" aria-hidden="true" />
        <button
          className="proto-picker-item proto-picker-replay"
          aria-label="Replay animation (R)"
          onClick={onReplay}
        >
          ↻
        </button>
      </nav>
    </>
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
