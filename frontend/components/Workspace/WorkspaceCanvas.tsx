"use client";

import dynamic from "next/dynamic";
import { AnimatePresence } from "motion/react";
import { RotateCcw } from "lucide-react";
import {
  fixtureClarify,
  fixtureHybrid,
  fixtureNotFound,
  fixtureSQL,
  fixtureVector,
} from "../../lib/api";
import { HERO_PERIODS } from "../ResearchHero";
import { LoadingCard } from "../LoadingCard";
import { ResearchHero } from "../ResearchHero";
import { AnswerBrief } from "../AnswerBrief";
import { Reveal } from "../Reveal";
import { ClarifyPanel } from "./ClarifyPanel";
import { DebugInspector } from "./DebugInspector";
import { EmptyWorkspace } from "./EmptyWorkspace";
import { NotFoundPanel } from "./NotFoundPanel";
import { PipelineTrail } from "./PipelineTrail";
import { ViewFallback } from "./ViewFallback";
import type { WorkspaceController } from "./use-workspace";

// bundle-11: tab views mount only on user navigation — split out of the
// initial `/` bundle so the brief view stays cheap.
const ExploreView = dynamic(() => import("../ExploreView").then((m) => m.ExploreView), {
  ssr: false,
  loading: () => <ViewFallback label="explore view" />,
});
const PublicationDetailView = dynamic(
  () => import("../PublicationDetailView").then((m) => m.PublicationDetailView),
  { ssr: false, loading: () => <ViewFallback label="publication view" /> },
);

export interface WorkspaceCanvasProps {
  ws: WorkspaceController;
}

/**
 * The workspace narrative column: the inquiry instrument, the pipeline trail,
 * and whichever single view is active. Exactly one of empty / loading /
 * answer / clarify / not-found / detail / error is mounted at a time, so the
 * page never stacks competing answers.
 */
export function WorkspaceCanvas({ ws }: WorkspaceCanvasProps) {
  const {
    store,
    runAsk,
    resolveCandidate,
    citeFromAnswer,
    openPublication,
    switchView,
  } = ws;
  const {
    view,
    question,
    activeQuestion,
    response,
    devMode,
    errorMsg,
    errorKind,
    highlightId,
    selectedCand,
    periodIdx,
    entityFilterLabel,
    headingRef,
    isLive,
    latencyRows,
    selectedPub,
    selectedPubEvidence,
    setPeriodIdx,
    setSelectedCand,
    setActiveQuestion,
    showResponse,
  } = store;

  const ask = (q: string) => void runAsk(q);
  // P0-A: the "Load example" affordances inject a hardcoded answer, so they are
  // developer tooling. Gated on devMode (default off) — a user in the default
  // UI is never one click away from invented publication counts.
  const clarify = (
    <ClarifyPanel
      response={response}
      selectedCand={selectedCand}
      onSelect={setSelectedCand}
      onResolve={resolveCandidate}
      onLoadExample={
        devMode
          ? () => {
              setActiveQuestion("Which Rahman collaborates with Bandung labs?");
              showResponse(fixtureClarify, false);
            }
          : undefined
      }
      titleRef={headingRef}
    />
  );
  const notFound = (
    <NotFoundPanel
      response={response}
      activeQuestion={activeQuestion}
      onTrySeed={ask}
      onLoadExample={
        devMode
          ? () => {
              setActiveQuestion("Quantum-dot yields in deep-sea fisheries after 2020?");
              showResponse(fixtureNotFound, false);
            }
          : undefined
      }
      titleRef={headingRef}
    />
  );

  return (
    <div className="center-col">
      <h1>Research Intelligence Workspace</h1>
      <p className="lede">
        Ask in Indonesian or English. Every figure below is traced to database-backed evidence — narrative is
        generated, numbers are not.
      </p>

      <ResearchHero
        question={question}
        onSynthesize={ask}
        loading={view === "loading"}
        periodIdx={periodIdx}
        onCyclePeriod={() => setPeriodIdx((i) => (i + 1) % HERO_PERIODS.length)}
        entityFilterLabel={entityFilterLabel}
        onAddFilter={() => switchView("clarify")}
        live={isLive}
        devMode={devMode}
      />

      <PipelineTrail
        view={view}
        activeQuestion={activeQuestion}
        route={response && view !== "loading" && view !== "error" ? response.route : undefined}
      />

      {/* P0-A: the "Showing a prototype snapshot instead" banner is gone.
          A failed request no longer swaps in placeholder data, so there is
          nothing to disclaim — the error view below is the whole story. */}

      {view === "empty" && <EmptyWorkspace onAsk={ask} />}

      {view === "loading" && <LoadingCard activeQuestion={activeQuestion} />}

      <AnimatePresence mode="wait">
        {view === "answer" && response && (
          <Reveal key={response.request_id} aria-live="polite" delay={0}>
            {response.status === "ok" && (
              <AnswerBrief
                response={response}
                live={isLive}
                highlightId={highlightId}
                onCite={citeFromAnswer}
                titleRef={headingRef}
              />
            )}
            {response.status === "needs_clarification" && clarify}
            {response.status === "not_found" && notFound}
            {(devMode || response.debug) && (
              <DebugInspector response={response} latencyRows={latencyRows} defaultOpen={devMode} />
            )}
          </Reveal>
        )}
        {view === "clarify" && response && (
          <Reveal key={`${response.request_id}-clarify`} aria-live="polite" delay={0}>
            {clarify}
          </Reveal>
        )}
        {view === "notfound" && response && (
          <Reveal key={`${response.request_id}-notfound`} aria-live="polite" delay={0}>
            {notFound}
          </Reveal>
        )}
      </AnimatePresence>

      {view === "clarify" && !response && clarify}
      {view === "notfound" && !response && notFound}

      {view === "explore" && (
        <Reveal key="explore" delay={0}>
          <ExploreView response={response} onOpenPublication={openPublication} titleRef={headingRef} />
        </Reveal>
      )}

      {view === "publication" && (
        <Reveal key="publication" delay={0}>
          <PublicationDetailView
            publication={selectedPub}
            evidence={selectedPubEvidence}
            onBack={() => switchView("answer")}
            titleRef={headingRef}
          />
        </Reveal>
      )}

      {view === "error" && (
        <section className="error-card" aria-labelledby="err-title" role="alert">
          <h2 id="err-title">
            {errorKind === "timeout"
              ? "The request timed out"
              : "The request could not be completed"}
          </h2>
          <p>{errorMsg}</p>
          {/*
            P0-A: state plainly that no data is shown. Without this line the
            empty space below reads as "zero results", which is a different
            and wrong claim — the backend never got to answer.
          */}
          <p className="mono error-note">
            No data is displayed for this request — nothing was returned by the research backend.
          </p>
          <button type="button" className="btn-retry" onClick={() => ask(activeQuestion || question)}>
            <RotateCcw size={15} aria-hidden /> Retry query
          </button>
        </section>
      )}

      {/* P0-A: the Replay strip renders hardcoded answers containing invented
          publication and citation counts. Developer tooling, so it only
          appears with devMode on (default off). In the default UI the only way
          to see numbers is to ask the backend for them. */}
      {devMode && response && view !== "empty" && view !== "loading" && (
        <div className="seeds" role="group" aria-label="Replay a sample route">
          <span className="seeds-label" aria-hidden>
            Replay
          </span>
          {[fixtureSQL, fixtureVector, fixtureHybrid].map((f) => (
            <button
              key={f.request_id}
              type="button"
              className="seed"
              onClick={() => showResponse(f, false)}
              title={`Sample ${f.route} answer`}
            >
              [{f.route}]
            </button>
          ))}
        </div>
      )}
    </div>
  );
}