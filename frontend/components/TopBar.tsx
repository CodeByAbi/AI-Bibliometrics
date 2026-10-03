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
 * Calm top chrome translated from the HTML mockup: brand + ⌘K context pill,
 * scrollable 8-state segmented switcher, provenance toggle, researcher avatar.
 * Icons are lucide (no Material Symbols); styling lives in globals.css.
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
    <header className="topbar" role="banner">
      <button
        type="button"
        className="menu-btn topbar-menu"
        onClick={onToggleSidebar}
        aria-label={sideOpen ? "Close research library" : "Open research library"}
        aria-expanded={sideOpen}
      >
        {sideOpen ? <X size={17} /> : <Menu size={17} />}
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

      <nav className="topbar-tabs" role="tablist" aria-label="Workspace views">
        {VIEW_TABS.map((t) => {
          const selected = activeView === t.view;
          return (
            <button
              key={t.id}
              type="button"
              role="tab"
              aria-selected={selected}
              className="topbar-tab"
              data-active={selected}
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
