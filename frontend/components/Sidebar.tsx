"use client";

import { useEffect } from "react";
import { animate, motion, useReducedMotion, useMotionValue } from "motion/react";
import { Bookmark, Compass, History, Plus, Settings, User, X } from "lucide-react";
import { SEEDS } from "../lib/api";
import { motionTokens } from "../lib/motion-tokens";
import { useMediaQuery } from "../hooks/use-media-query";
import type { WorkspaceView } from "../lib/views";

interface SidebarProps {
  activeView: WorkspaceView;
  live: boolean;
  devMode: boolean;
  sideOpen: boolean;
  onNavigate: (v: WorkspaceView) => void;
  onNewResearch: () => void;
  onAsk: (q: string) => void;
  onDevToggle: () => void;
  onClose: () => void;
}

const NAV: Array<{ view: WorkspaceView; icon: typeof Compass; label: string; id: string }> = [
  { view: "explore", icon: Compass, label: "Explore", id: "nav-explore" },
  { view: "answer", icon: History, label: "Research History", id: "nav-history" },
  { view: "answer", icon: Bookmark, label: "Saved Research", id: "nav-saved" },
  { view: "author", icon: User, label: "Author Index", id: "nav-author" },
];

/**
 * Quiet sidebar translated from the HTML mockup: New Research anchor,
 * navigation, recent sessions (bound to real fixture-triggering questions),
 * and the corpus connection footer.
 */
export function Sidebar({
  activeView,
  live,
  devMode,
  sideOpen,
  onNavigate,
  onNewResearch,
  onAsk,
  onDevToggle,
  onClose,
}: SidebarProps) {
  // Mobile drawer (≤899px) is a left sheet, 300px wide. Dismiss past ~1/3 of
  // its width, or with a decisive leftward fling — both are checked (Rule 3):
  // offset alone misfires on slow drifts, velocity alone on jitters.
  const DISMISS_OFFSET_X = -100;
  const DISMISS_VELOCITY_X = -600;
  const DRAWER_HIDDEN_X = "-102%";

  // Motion owns the drawer transform on mobile only. Desktop keeps the
  // untouched static layout; SSR renders motion-free (CSS hides the drawer).
  const isMobile = useMediaQuery("(max-width: 899px)");
  const reduceMotion = useReducedMotion();
  const x = useMotionValue<number | string>(0);
  const motionOwned = isMobile;

  useEffect(() => {
    if (!motionOwned) return;
    if (reduceMotion) {
      x.set(sideOpen ? 0 : DRAWER_HIDDEN_X);
      return;
    }
    const controls = animate(x, sideOpen ? 0 : DRAWER_HIDDEN_X, {
      duration: motionTokens.duration.med,
      ease: [...motionTokens.easing.drawer] as [number, number, number, number],
    });
    return () => controls.stop();
  }, [sideOpen, motionOwned, reduceMotion, x]);

  // Esc dismisses the drawer — the TopBar hamburger hides underneath the open
  // sheet on mobile, and swipe/scrim are undiscoverable to some users.
  useEffect(() => {
    if (!sideOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [sideOpen, onClose]);

  const sessions: Array<{ label: string; meta: string; warn?: boolean; run: () => void }> = [
    {
      label: "MSC therapy trend — Indonesian institutions",
      meta: "4 sources · HybridRoute",
      run: () => onAsk(SEEDS[0].question),
    },
    {
      label: 'Author: "Rahman" collaboration query',
      meta: "Disambig · GraphRoute",
      warn: true,
      run: () => onAsk("Which Rahman collaborates with Bandung labs?"),
    },
    {
      label: "Quantum-dot yields in deep-sea fisheries",
      meta: "0 records · not_found",
      run: () => onAsk("Quantum-dot yields in deep-sea fisheries after 2020?"),
    },
  ];

  return (
    <motion.aside
      className="sidebar"
      data-open={sideOpen}
      data-motion={motionOwned || undefined}
      aria-label="Research library"
      drag={motionOwned && sideOpen ? "x" : false}
      dragConstraints={{ right: 0 }}
      dragElastic={0.1}
      dragMomentum={false}
      style={motionOwned ? { x, touchAction: "pan-y" } : undefined}
      onDragEnd={(_, info) => {
        if (info.offset.x < DISMISS_OFFSET_X || info.velocity.x < DISMISS_VELOCITY_X) onClose();
      }}
    >
      <div className="side-top">
        <span className="side-top-label">Library</span>
        <button type="button" className="side-close" onClick={onClose} aria-label="Close research library">
          <X size={15} aria-hidden />
        </button>
      </div>
      <button type="button" className="btn-new" onClick={() => { onNewResearch(); onClose(); }}>
        <Plus size={16} strokeWidth={2.4} aria-hidden /> New Research
        <kbd className="btn-new-kbd" aria-hidden>
          ⌘N
        </kbd>
      </button>

      <nav className="side-list" aria-label="Workspace sections">
        {NAV.map((n) => (
          <button
            key={n.id}
            id={n.id}
            type="button"
            className="side-item"
            aria-current={activeView === n.view}
            onClick={() => { onNavigate(n.view); onClose(); }}
          >
            <n.icon size={15} aria-hidden className="icon-muted" />
            <span>{n.label}</span>
          </button>
        ))}
        <button type="button" className="side-item" onClick={onDevToggle} aria-pressed={devMode}>
          <Settings size={15} aria-hidden className="icon-muted" />
          <span>
            Settings
            <small>Developer inspector {devMode ? "on" : "off"}</small>
          </span>
        </button>
      </nav>

      <p className="side-label">
        Recent Sessions <span className="mono">3</span>
      </p>
      <div className="side-list" role="list">
        {sessions.map((s) => (
          <button key={s.label} type="button" role="listitem" className="side-item side-session" onClick={() => { s.run(); onClose(); }} title={s.label}>
            <span className="side-text">
              <span className="truncate">{s.label}</span>
              <small className={s.warn ? "side-warn" : "mono"}>{s.meta}</small>
            </span>
          </button>
        ))}
      </div>

      <div className="side-foot">
        <span className="side-label side-foot-label">Corpus Connection</span>
        <span className="mono side-db">PostgreSQL Scopus Prototype</span>
        <span className="side-db-sub">Silver &amp; Gold Provenance Layer</span>
        <span className="db-pill" data-live={live}>
          <span className="pulse" aria-hidden />
          {live ? "Live · prototype DB" : "Prototype snapshot · fixtures ready"}
        </span>
      </div>
    </motion.aside>
  );
}
