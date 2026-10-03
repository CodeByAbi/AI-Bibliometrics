"use client";

import { useCallback } from "react";
import { fixtureHybrid, postAsk, pickFixture, type AskResponse } from "../../lib/api";
import { getUserMessage } from "../../lib/errors";
import { HERO_PERIODS } from "../ResearchHero";
import type { WorkspaceStore } from "./use-workspace-store";

/** Backend guard: 8s client abort mirrors the backend's 10s statement_timeout. */
const ASK_TIMEOUT_MS = 8000;

type Filters = Record<string, string | number | null | undefined>;

/**
 * Retrieval transport: one POST /api/v1/ask per ask, one abort controller so a
 * newer question supersedes the in-flight one, and an honest offline
 * fallback — the fixture keeps the workspace readable while the banner tells
 * the user the answer is a snapshot rather than a live read.
 */
export function useAsk(store: WorkspaceStore) {
  const {
    view,
    devMode,
    periodIdx,
    response,
    activeQuestion,
    selectedCand,
    abortRef,
    setActiveQuestion,
    setQuestion,
    setView,
    setSelectedCand,
    setHighlightId,
    setEntityFilterLabel,
    setErrorMsg,
    showResponse,
  } = store;

  const post = useCallback(
    async (query: string, filters: Filters, fallback: AskResponse) => {
      abortRef.current?.abort();
      const ctrl = new AbortController();
      abortRef.current = ctrl;
      const timeout = setTimeout(() => ctrl.abort(), ASK_TIMEOUT_MS);
      try {
        const r = await postAsk(query, devMode, ctrl.signal, filters);
        setErrorMsg("");
        showResponse(r, true);
      } catch (error) {
        // Aborted by timeout or by a newer ask — never surface as a failure.
        if (ctrl.signal.aborted) return;
        const code =
          error instanceof Error && "code" in error
            ? String((error as { code: string }).code)
            : "INTERNAL_ERROR";
        console.error("Ask request failed:", error);
        setErrorMsg(getUserMessage(code));
        showResponse(fallback, false);
      } finally {
        clearTimeout(timeout);
      }
    },
    [abortRef, devMode, setErrorMsg, showResponse],
  );

  const runAsk = useCallback(
    async (q: string, extraFilters?: Filters) => {
      const query = q.trim();
      if (query.length < 3 || view === "loading") return;
      // The hero period chip is a real year_from/year_to filter.
      const periodFilters = HERO_PERIODS[periodIdx]?.filters ?? {};
      setActiveQuestion(query);
      setQuestion(query);
      setView("loading");
      setSelectedCand(null);
      setHighlightId(null);
      setEntityFilterLabel(null);
      await post(query, { ...periodFilters, ...(extraFilters ?? {}) }, pickFixture(query));
    },
    [
      view,
      periodIdx,
      post,
      setActiveQuestion,
      setQuestion,
      setView,
      setSelectedCand,
      setHighlightId,
      setEntityFilterLabel,
    ],
  );

  /** Re-post the original question with a disambiguated entity as a structured
   *  filter so EntityResolutionGate resolves to a single canonical ID. */
  const resolveCandidate = useCallback(async () => {
    if (!selectedCand || !response?.candidates) return;
    const cand = response.candidates.find((c) => c.id === selectedCand);
    if (!cand) return;
    const filters: Record<string, string> =
      cand.type === "author"
        ? { author_name: cand.name }
        : cand.type === "institution"
          ? { institution_name: cand.name }
          : { topic_name: cand.name };
    setEntityFilterLabel(`${cand.type}: ${cand.name}`);
    setView("loading");
    await post(activeQuestion, filters, fixtureHybrid);
  }, [
    selectedCand,
    response,
    activeQuestion,
    post,
    setEntityFilterLabel,
    setView,
  ]);

  return { runAsk, resolveCandidate };
}