"use client";

import { useCallback, useEffect, useRef } from "react";
import type { KeyboardEvent as ReactKeyboardEvent } from "react";
import { animate, motion, useReducedMotion, useMotionValue } from "motion/react";
import { Bookmark, Compass, History, Plus, Settings, X } from "lucide-react";
import { SEEDS } from "../lib/api";
import { motionTokens } from "../lib/motion-tokens";
import { useMediaQuery } from "../hooks/use-media-query";
import type { WorkspaceView } from "../lib/views";
import type { SessionListItem } from "../lib/sessions";

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
  /** Recent sessions from the backend. Undefined hides the section. */
  sessions?: SessionListItem[];
  /** Currently open session id, highlighted with aria-current. */
  activeSessionId?: string | null;
  /** Backend has session persistence off (503) — say so, do not fake a list. */
  sessionsUnavailable?: boolean;
  /** Open an existing session rather than re-running its question. */
  onOpenSession?: (id: string) => void;
}

const NAV: Array<{ view: WorkspaceView; icon: typeof Compass; label: string; id: string }> = [
  { view: "explore", icon: Compass, label: "Explore", id: "nav-explore" },
  { view: "answer", icon: History, label: "Research History", id: "nav-history" },
  { view: "answer", icon: Bookmark, label: "Saved Research", id: "nav-saved" },
];

const DISMISS_OFFSET_X = -100;
const DISMISS_VELOCITY_X = -600;
const DRAWER_HIDDEN_X = "-102%";

const FOCUSABLE =
  'a[href], button:not([disabled]), input, textarea, select, [tabindex]:not([tabindex="-1"])';

/**
 * Quiet sidebar: New Research anchor, navigation, recent sessions, and the
 * corpus connection footer. On ≤899px it becomes a modal left sheet — it then
 * takes dialog semantics, traps Tab, and returns focus to the hamburger that
 * opened it. On desktop it is a plain complementary landmark.
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
  sessions,
  activeSessionId,
  sessionsUnavailable,
  onOpenSession,
}: SidebarProps) {
  const isMobile = useMediaQuery("(max-width: 899px)");
  const reduceMotion = useReducedMotion();
  const x = useMotionValue<number | string>(0);
  const motionOwned = isMobile;
  const asideRef = useRef<HTMLElement | null>(null);
  const closeRef = useRef<HTMLButtonElement | null>(null);
  const restoreFocusRef = useRef(false);

  // Motion owns the drawer transform on mobile only. Desktop keeps the
  // untouched static layout; SSR renders motion-free (CSS hides the drawer).
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

  const asModal = motionOwned && sideOpen;

  // Focus in on open; focus back to the hamburger on close. Esc dismisses
  // because the toggle hides underneath the sheet and swipe/scrim are
  // undiscoverable to some keyboard and screen-reader users.
  useEffect(() => {
    if (!motionOwned) return;
    if (sideOpen) {
      restoreFocusRef.current = true;
      closeRef.current?.focus();
      return;
    }
    if (restoreFocusRef.current) {
      restoreFocusRef.current = false;
      document.querySelector<HTMLElement>("[data-menu-toggle]")?.focus();
    }
  }, [sideOpen, motionOwned]);

  const onKeyDown = useCallback(
    (e: ReactKeyboardEvent<HTMLElement>) => {
      if (!asModal) return;
      if (e.key === "Escape") {
        e.preventDefault();
        onClose();
        return;
      }
      if (e.key !== "Tab") return;
      const nodes = Array.from(asideRef.current?.querySelectorAll<HTMLElement>(FOCUSABLE) ?? []).filter(
        (n) => n.offsetParent !== null || n === document.activeElement,
      );
      if (!nodes.length) return;
      const first = nodes[0]!;
      const last = nodes[nodes.length - 1]!;
      // Tab from the last control wraps to the first, and Shift+Tab from the
      // first wraps back — focus never escapes the modal sheet.
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    },
    [asModal, onClose],
  );

  /**
   * Recent Sessions, backed by `GET /api/v1/sessions`.
   *
   * Previously three hardcoded entries whose `run` re-issued a canned question.
   * That was worse than a placeholder: it looked like restored history while
   * actually spending a fresh retrieval, and the advertised outcomes
   * ("4 sources · HybridRoute") were hardcoded text that no query had produced.
   * A sidebar entry must now be a real session the backend already holds.
   *
   * Rendering rules that follow from the backend being authoritative:
   *   - the count is the list length, never a literal;
   *   - an empty session shows "no questions yet", not a fake route;
   *   - the meta line is source count + route, and a session with no answer yet
   *     says so rather than implying one;
   *   - `not_found` is surfaced as its own state, because a conversation that
   *     found nothing is a real outcome and not an error.
   *
   * When `sessions` is undefined the section is omitted entirely, so the
   * stateless route shows no misleading history at all.
   */
  const sessionRows = (sessions ?? []).map((s) => {
    const unanswered = s.message_count === 0;
    const noSources = s.source_count === 0;
    let meta: string;
    if (unanswered) {
      meta = "no questions yet";
    } else if (noSources) {
      meta = s.last_route ? `no sources · ${s.last_route}` : "no sources";
    } else {
      meta = `${s.source_count} source${s.source_count === 1 ? "" : "s"}`;
      if (s.last_route) meta += ` · ${s.last_route}`;
    }
    return {
      id: s.id,
      label: s.title,
      meta,
      active: s.id === activeSessionId,
      open: () => onOpenSession?.(s.id),
    };
  });

  const showSessions = sessions !== undefined;

  return (
    <motion.aside
      ref={asideRef}
      id="research-library"
      className="sidebar"
      data-open={sideOpen}
      data-motion={motionOwned || undefined}
      aria-label="Research library"
      role={asModal ? "dialog" : undefined}
      aria-modal={asModal ? true : undefined}
      onKeyDown={onKeyDown}
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
        <button
          ref={closeRef}
          type="button"
          className="side-close"
          onClick={onClose}
          aria-label="Close research library"
        >
          <X size={15} aria-hidden />
        </button>
      </div>

      <button
        type="button"
        className="btn-new"
        onClick={() => {
          onNewResearch();
          onClose();
        }}
      >
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
            onClick={() => {
              onNavigate(n.view);
              onClose();
            }}
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

      {showSessions && (
        <>
          <p className="side-label" id="side-sessions-label">
            Recent Sessions <span className="mono">{sessionRows.length}</span>
          </p>
          <ul className="side-list side-sessions" aria-labelledby="side-sessions-label">
            {sessionsUnavailable && (
              <li>
                <p className="side-sessions-note">
                  Session persistence is off on the server.
                </p>
              </li>
            )}
            {!sessionsUnavailable && sessionRows.length === 0 && (
              <li>
                <p className="side-sessions-note">
                  No saved sessions yet. Start one with New Research.
                </p>
              </li>
            )}
            {sessionRows.map((s) => (
              <li key={s.id}>
                <button
                  type="button"
                  className="side-item side-session"
                  data-active={s.active || undefined}
                  // aria-current is what tells a screen reader which session is
                  // open; the visual highlight alone does not.
                  aria-current={s.active ? "page" : undefined}
                  onClick={() => {
                    s.open();
                    onClose();
                  }}
                  title={s.label}
                >
                  <span className="side-text">
                    <span className="truncate">{s.label}</span>
                    <small className="mono">{s.meta}</small>
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </>
      )}

      <div className="side-foot">
        <span className="side-label side-foot-label">Corpus Connection</span>
        <span className="mono side-db">PostgreSQL Scopus Prototype</span>
        <span className="side-db-sub">Silver &amp; Gold Provenance Layer</span>
        <span className="db-pill" data-live={live}>
          <span className="pulse" aria-hidden />
          {/*
            P0-A: this read "Prototype snapshot · fixtures ready" whenever
            `live` was false, which is the idle state AND the error state AND
            the timeout state. It told the user fixtures were standing in for
            the backend when no fixture is served on any live path any more.
            Now it names the actual condition.
          */}
          {live
            ? "Live · prototype DB"
            : devMode
              ? "Lab dataset · not live"
              : "Not connected · no live data"}
        </span>
      </div>
    </motion.aside>
  );
}