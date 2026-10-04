"use client";

import { InspectorBar } from "../InspectorBar";
import { Sidebar } from "../Sidebar";
import { TopBar } from "../TopBar";
import { ProvenanceRail } from "./ProvenanceRail";
import { StateBar } from "./StateBar";
import { WorkspaceCanvas } from "./WorkspaceCanvas";
import { useWorkspace } from "./use-workspace";
import type { SessionListItem } from "@/lib/sessions";

/**
 * Workspace shell — chrome, status, and the two-column grid. All behaviour
 * lives in use-workspace; the narrative column and the provenance rail are
 * separate presentational trees that each own their internals.
 *
 * `sessionId` / `onNewResearch` / `onAskComplete` are the session-aware seams.
 * All three are optional, so the stateless `/` route renders exactly as before.
 */
export interface WorkspaceProps {
  /** When set, every ask carries this id so the turn is persisted. */
  sessionId?: string | null;
  /** Replaces the local-only New Research reset with a real session creation. */
  onNewResearch?: () => void;
  /** Fired after a completed ask, so Recent Sessions can refresh. */
  onAskComplete?: () => void;
  /** Recent sessions for the sidebar. Undefined hides the list entirely. */
  sessions?: SessionListItem[];
  /** Currently open session, highlighted in the sidebar. */
  activeSessionId?: string | null;
  /** True when the backend has session persistence switched off (503). */
  sessionsUnavailable?: boolean;
  /** Opens a session: the sidebar calls this instead of re-running a question. */
  onOpenSession?: (id: string) => void;
}

export default function Workspace(props: WorkspaceProps = {}) {
  const ws = useWorkspace(props);
  const {
    store,
    loadLab,
    applyDataKind,
    focusSource,
    scrollToEvidence,
    openPublication,
    switchView,
    newResearch,
    toggleSidebar,
    runAsk,
  } = ws;
  const {
    view,
    devMode,
    dataKind,
    highlightId,
    sideOpen,
    sourcesOpen,
    expandedEv,
    copiedDoi,
    provenanceOpen,
    headingRef,
    isLive,
    latencyRows,
    setDevMode,
    setSourcesOpen,
    toggleEv,
    copyDoi,
  } = store;

  const railHidden = view === "publication";

  return (
    <div className="app-top page-enter">
      <TopBar
        activeView={view}
        onSwitch={switchView}
        provenanceOpen={provenanceOpen}
        onToggleProvenance={() => store.setProvenanceOpen((o) => !o)}
        sideOpen={sideOpen}
        onToggleSidebar={toggleSidebar}
      />

      <div className="app-shell">
        {sideOpen && <div className="scrim" onClick={() => store.setSideOpen(false)} aria-hidden />}

        <Sidebar
          activeView={view}
          live={isLive}
          devMode={devMode}
          sideOpen={sideOpen}
          onNavigate={switchView}
          onNewResearch={newResearch}
          onAsk={(q) => void runAsk(q)}
          sessions={props.sessions}
          activeSessionId={props.activeSessionId ?? null}
          sessionsUnavailable={props.sessionsUnavailable ?? false}
          onOpenSession={props.onOpenSession}
          onDevToggle={() => setDevMode((d) => !d)}
          onClose={() => store.setSideOpen(false)}
        />

        <div className="main">
          <StateBar
            view={view}
            isLive={isLive}
            devMode={devMode}
            dataKind={dataKind}
            onToggleDevMode={() => setDevMode((d) => !d)}
            onLoadLab={loadLab}
            onApplyDataKind={applyDataKind}
          />

          <InspectorBar
            open={provenanceOpen}
            onClose={() => store.setProvenanceOpen(false)}
            response={store.response}
            live={isLive}
            activeQuestion={store.activeQuestion}
          />

          <main className={`workspace${railHidden ? " rail-hidden" : ""}`} id="workspace-main">
            <WorkspaceCanvas ws={ws} />

            {!railHidden && (
              <ProvenanceRail
                view={view}
                response={store.response}
                highlightId={highlightId}
                sourcesOpen={sourcesOpen}
                expandedEv={expandedEv}
                copiedDoi={copiedDoi}
                onToggleSources={() => setSourcesOpen((o) => !o)}
                onToggleEv={toggleEv}
                onOpenPublication={openPublication}
                onCopy={(v) => void copyDoi(v)}
                onHighlightEvidence={scrollToEvidence}
                onHighlightSource={focusSource}
              />
            )}
          </main>
        </div>
      </div>
    </div>
  );
}