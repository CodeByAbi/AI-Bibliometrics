"use client";

import { InspectorBar } from "../InspectorBar";
import { Sidebar } from "../Sidebar";
import { TopBar } from "../TopBar";
import { ProvenanceRail } from "./ProvenanceRail";
import { StateBar } from "./StateBar";
import { WorkspaceCanvas } from "./WorkspaceCanvas";
import { useWorkspace } from "./use-workspace";

/**
 * Workspace shell — chrome, status, and the two-column grid. All behaviour
 * lives in use-workspace; the narrative column and the provenance rail are
 * separate presentational trees that each own their internals.
 */
export default function Workspace() {
  const ws = useWorkspace();
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

  const railHidden = view === "author" || view === "publication";

  return (
    <div className="app-top page-enter">
      <TopBar
        activeView={view}
        onSwitch={switchView}
        provenanceOpen={provenanceOpen}
        onToggleProvenance={() => store.setProvenanceOpen((o) => !o)}
        onOpenAuthor={() => switchView("author")}
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