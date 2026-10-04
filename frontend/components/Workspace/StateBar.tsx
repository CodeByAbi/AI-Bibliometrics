"use client";

import type { DataKind } from "../../lib/api";
import type { WorkspaceView } from "../../lib/views";
import { LAB_DATA_LIST, LAB_VIEW_LIST } from "./use-lab";

export interface StateBarProps {
  view: WorkspaceView;
  isLive: boolean;
  devMode: boolean;
  dataKind: DataKind;
  onToggleDevMode: () => void;
  onLoadLab: (v: WorkspaceView) => void;
  onApplyDataKind: (k: DataKind) => void;
}

/**
 * Workspace status strip: is this answer live or a snapshot, plus the dev-only
 * state lab. A labelled `<section>` makes it a discoverable landmark — a bare
 * `role="toolbar"` would imply arrow-key navigation that is not implemented,
 * and would leave the content outside any landmark. Every chip is a real
 * button with `aria-pressed` — never colour-only state.
 */
export function StateBar({
  view,
  isLive,
  devMode,
  dataKind,
  onToggleDevMode,
  onLoadLab,
  onApplyDataKind,
}: StateBarProps) {
  return (
    <section className="statebar" aria-label="Workspace status">
      <span className="provenance-note" data-live={isLive}>
        <i aria-hidden />
        {/*
          P0-A: "Prototype snapshot" is no longer true of anything a user can
          reach. A live-path answer is either grounded data or an explicit
          error; the only place a fixture is still shown on purpose is the
          developer state lab, and that says so.
        */}
        {isLive ? "Live database" : devMode ? "Lab dataset (not live data)" : "No live data"}
        <span className="mono mono-xs">· Scopus · ID/EN</span>
      </span>

      {devMode && (
        <span className="statebar-group" role="group" aria-label="State lab — preview every workspace state">
          <span className="statebar-hint">State lab</span>
          {LAB_VIEW_LIST.map((s) => (
            <button
              key={s}
              type="button"
              className="chip-state"
              aria-pressed={view === s}
              onClick={() => onLoadLab(s)}
            >
              {s === "notfound" ? "not-found" : s}
            </button>
          ))}
        </span>
      )}

      {devMode && (
        <span className="statebar-group" role="group" aria-label="Break-ui dataset — swap the answer fixture">
          <span className="statebar-hint">Data</span>
          {LAB_DATA_LIST.map((k) => (
            <button
              key={k}
              type="button"
              className="chip-state"
              aria-pressed={dataKind === k}
              onClick={() => onApplyDataKind(k)}
              title={k === "demo" ? "Normal fixtures" : `Adversarial fixture: ${k}`}
            >
              {k}
            </button>
          ))}
        </span>
      )}

      <button type="button" className="dev-toggle" aria-pressed={devMode} onClick={onToggleDevMode}>
        Dev inspector <span className="switch" aria-hidden />
      </button>
    </section>
  );
}