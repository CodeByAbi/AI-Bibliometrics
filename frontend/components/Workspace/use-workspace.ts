"use client";

import { useCallback, useEffect } from "react";
import { SEEDS } from "../../lib/api";
import { motionConfig } from "../../lib/motion-config";
import type { WorkspaceView } from "../../lib/views";
import { useAsk } from "./use-ask";
import { useLab } from "./use-lab";
import { useWorkspaceStore } from "./use-workspace-store";

const scrollBehavior = (): ScrollBehavior =>
  motionConfig.shouldAnimate({ essential: true }) ? "smooth" : "auto";

/**
 * Workspace controller: composes state, retrieval, and dev tooling, then adds
 * the cross-cutting behaviour that needs all three — provenance highlighting,
 * scroll navigation, top-bar view switching, and the ⌘N / ⌘K shortcuts.
 */
export function useWorkspace() {
  const store = useWorkspaceStore();
  const { runAsk, resolveCandidate } = useAsk(store);
  const { loadLab, applyDataKind } = useLab(store, runAsk);

  const {
    view,
    question,
    activeQuestion,
    response,
    sideOpen,
    headingRef,
    abortRef,
    setView,
    setQuestion,
    setActiveQuestion,
    setResponse,
    setSelectedCand,
    setSelectedPubId,
    setHighlightId,
    setEntityFilterLabel,
    setSideOpen,
    setSourcesOpen,
  } = store;

  /** Evidence→source jump. The rail is always mounted, so no rAF wait needed. */
  const focusSource = useCallback(
    (pubId: string) => {
      setHighlightId(pubId);
      setSourcesOpen(true);
      document
        .getElementById(`src-${pubId}`)
        ?.scrollIntoView({ behavior: scrollBehavior(), block: "center" });
    },
    [setHighlightId, setSourcesOpen],
  );

  /** Answer-citation→bibliography jump: the rail must expand first. */
  const citeFromAnswer = useCallback(
    (pubId: string) => {
      setHighlightId(pubId);
      setSourcesOpen(true);
      requestAnimationFrame(() => {
        document
          .getElementById(`src-${pubId}`)
          ?.scrollIntoView({ behavior: scrollBehavior(), block: "center" });
      });
    },
    [setHighlightId, setSourcesOpen],
  );

  /** Source→evidence jump: highlight every object traced to this publication. */
  const scrollToEvidence = useCallback(
    (pubId: string) => {
      const idx =
        response?.evidence_objects.findIndex((ev) => ev.sources.some((s) => s.publication_id === pubId)) ?? -1;
      setHighlightId(pubId);
      if (idx >= 0) {
        document
          .getElementById(`ev-${idx}`)
          ?.scrollIntoView({ behavior: scrollBehavior(), block: "center" });
      }
    },
    [response, setHighlightId],
  );

  const openPublication = useCallback(
    (pubId: string) => {
      setSelectedPubId(pubId);
      setHighlightId(pubId);
      setView("publication");
      window.scrollTo({ top: 0, behavior: scrollBehavior() });
    },
    [setSelectedPubId, setHighlightId, setView],
  );

  const newResearch = useCallback(() => {
    abortRef.current?.abort();
    setQuestion("");
    setActiveQuestion("");
    setResponse(null);
    setView("empty");
    setSelectedCand(null);
    setHighlightId(null);
    setSelectedPubId(null);
    setEntityFilterLabel(null);
    setSideOpen(false);
  }, [
    abortRef,
    setQuestion,
    setActiveQuestion,
    setResponse,
    setView,
    setSelectedCand,
    setHighlightId,
    setSelectedPubId,
    setEntityFilterLabel,
    setSideOpen,
  ]);

  /** Top-bar view navigation — detail views read the current answer set. */
  const switchView = useCallback(
    (v: WorkspaceView) => {
      if (v === view) {
        window.scrollTo({ top: 0, behavior: scrollBehavior() });
        return;
      }
      if (v === "loading") {
        void runAsk(activeQuestion.trim() || question.trim() || SEEDS[0].question);
        return;
      }
      if (v === "answer") {
        if (!response) {
          setView("empty");
          return;
        }
        if (response.status === "ok") setView("answer");
        else if (response.status === "needs_clarification") setView("clarify");
        else setView("notfound");
        window.scrollTo({ top: 0, behavior: scrollBehavior() });
        return;
      }
      if (v === "error") {
        loadLab("error");
        return;
      }
      setSelectedCand(null);
      setView(v);
      window.scrollTo({ top: 0, behavior: scrollBehavior() });
    },
    [view, response, question, activeQuestion, runAsk, loadLab, setView, setSelectedCand],
  );

  // ⌘N starts fresh research, ⌘K focuses the inquiry box.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!(e.metaKey || e.ctrlKey)) return;
      const key = e.key.toLowerCase();
      if (key === "n") {
        e.preventDefault();
        newResearch();
      } else if (key === "k") {
        e.preventDefault();
        document.getElementById("hero-input")?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [newResearch]);

  // Move focus to the result heading on view change so keyboard and
  // screen-reader users are oriented instead of stranded at the top.
  useEffect(() => {
    if (view !== "empty" && view !== "loading" && view !== "error") {
      headingRef.current?.focus({ preventScroll: false });
    }
  }, [view, headingRef]);

  return {
    store,
    runAsk,
    resolveCandidate,
    loadLab,
    applyDataKind,
    focusSource,
    citeFromAnswer,
    scrollToEvidence,
    openPublication,
    switchView,
    newResearch,
    toggleSidebar: useCallback(() => setSideOpen((o) => !o), [setSideOpen]),
  };
}

export type WorkspaceController = ReturnType<typeof useWorkspace>;