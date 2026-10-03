"use client";

import { Menu, Search, SlidersHorizontal, X } from "lucide-react";
import { VIEW_TABS, type WorkspaceView } from "../lib/views";

interface TopBarProps {
  activeView: WorkspaceView;
  onSwitch: (v: WorkspaceView) => void;
  provenanceOpen: boolean;
  onToggleProvenance: () => void;
  onOpenAuthor: () => void;
  sideOpen: boolean;
  onToggleSidebar: () => void;
}

/**
 * Calm top chrome: brand, ⌘K context pill, the view switcher, the provenance
 * toggle, and the researcher avatar.
 *
 * The view switcher is a labelled `<nav>` with `aria-current="page"` rather
 * than a `tablist`: these eight controls swap the region below them rather
 * than selecting among peer tab panels, so a tab role would promise a
 * tabpanel that does not exist. Every control is also a real focus stop, which
 * is what keyboard users expect from view navigation.
 */
export function TopBar({
  activeView,
  onSwitch,
  provenanceOpen,
  onToggleProvenance,
  onOpenAuthor,
  sideOpen,
  onToggleSidebar,
}: TopBarProps) {
  return (
    <header className="topbar">
      <button
        type="button"
        className="menu-btn topbar-menu"
        data-menu-toggle
        onClick={onToggleSidebar}
        aria-label={sideOpen ? "Close research library" : "Open research library"}
        aria-expanded={sideOpen}
        aria-controls="research-library"
      >
        {sideOpen ? <X size={17} aria-hidden /> : <Menu size={17} aria-hidden />}
      </button>

      <button type="button" className="topbar-brand" onClick={() => onSwitch("answer")} title="Back to research brief">
        <span className="brand-mark topbar-logo" aria-hidden>
          <img src="/logo.png" alt="" width={28} height={28} />
        </span>
        <span className="topbar-brand-name">Bibliometrics AI</span>
      </button>

      <span className="topbar-context" aria-hidden title="Fast context — DOI lookup">
        <Search size={13} />
        <span className="mono">10.1016/j.cell.2023.01.002</span>
        <kbd>⌘K</kbd>
      </span>

      <nav className="topbar-tabs" aria-label="Workspace views">
        {VIEW_TABS.map((t) => {
          const current = activeView === t.view;
          return (
            <button
              key={t.id}
              id={t.id}
              type="button"
              className="topbar-tab"
              data-active={current}
              aria-current={current ? "page" : undefined}
              onClick={() => onSwitch(t.view)}
              title={t.tab}
            >
              {t.tab}
            </button>
          );
        })}
      </nav>

      <button
        type="button"
        className="topbar-prov"
        aria-pressed={provenanceOpen}
        aria-controls={provenanceOpen ? "provenance-inspector" : undefined}
        onClick={onToggleProvenance}
        title="Toggle technical grounding provenance details"
      >
        <SlidersHorizontal size={14} aria-hidden />
        <span>Provenance Info</span>
      </button>

      <button type="button" className="topbar-avatar" onClick={onOpenAuthor} title="View researcher profile">
        <span className="topbar-avatar-mark" aria-hidden>
          RF
        </span>
        <span className="topbar-avatar-name">Dr. Foster</span>
      </button>
    </header>
  );
}