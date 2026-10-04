"use client";

import { useCallback } from "react";
import { postAsk } from "../../lib/api";
import { getUserMessage, isAbortError, isTimeoutError } from "../../lib/errors";
import { HERO_PERIODS } from "../ResearchHero";
import type { WorkspaceStore } from "./use-workspace-store";

/**
 * Client abort budget.
 *
 * Sits just under the backend's 10 s DB statement_timeout so a genuinely slow
 * database surfaces the structured 503 `db_timeout` from the server rather
 * than an opaque client-side abort. It is NOT a budget for the LLM: the
 * backend now bounds Text-to-SQL at TEXT2SQL_TIMEOUT_S (6 s) and returns a
 * structured 504 `llm_timeout`, so the generator never runs long enough to
 * hit this.
 */
const ASK_TIMEOUT_MS = 8000;

type Filters = Record<string, string | number | null | undefined>;

/**
 * Retrieval transport: one POST /api/v1/ask per ask, one abort controller so a
 * newer question supersedes the in-flight one, and — critically — NO data
 * fallback.
 *
 * P0-A: this hook used to accept a `fallback: AskResponse` argument
 * (`pickFixture(question)`) and call `showResponse(fallback, false)` from the
 * catch block. Every backend error, 500, or timeout therefore rendered a
 * fabricated brief containing invented publication counts, citation numbers
 * and expertise scores, behind a small "Showing a prototype snapshot instead"
 * note. A user could not tell a real answer from a hardcoded one, and neither
 * could a reader of a screenshot.
 *
 * The rule now: a transport failure produces an explicit error or timeout
 * state and NOTHING else. No numbers, no sources, no evidence objects.
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
    setErrorKind,
    setResponse,
    showResponse,
  } = store;

  const post = useCallback(
    async (query: string, filters: Filters) => {
      abortRef.current?.abort();
      const ctrl = new AbortController();
      abortRef.current = ctrl;
      let timedOut = false;
      const timeout = setTimeout(() => {
        timedOut = true;
        ctrl.abort();
      }, ASK_TIMEOUT_MS);
      try {
        const r = await postAsk(query, devMode, ctrl.signal, filters);
        clearTimeout(timeout);
        setErrorMsg("");
        setErrorKind(null);
        showResponse(r, true);
      } catch (error) {
        clearTimeout(timeout);
        // Superseded by a newer ask, or the view unmounted. Nothing to report:
        // the newer request owns the view now. This is NOT a failure and must
        // not be rendered as one.
        if (isAbortError(error) && !timedOut) return;

        // P0-A: drop every trace of the previous answer before surfacing the
        // failure. Leaving a stale response mounted would let the rail, the
        // Trends chart and Paper Detail keep rendering the PREVIOUS question's
        // numbers under the new question — a silent, and much harder to spot,
        // form of the same bug.
        setResponse(null);

        const timedOutRequest = timedOut || isTimeoutError(error);
        const code =
          error instanceof Error && "code" in error
            ? String((error as { code: string }).code)
            : "INTERNAL_ERROR";
        console.error("Ask request failed:", error);
        setErrorKind(timedOutRequest ? "timeout" : "error");
        setErrorMsg(getUserMessage(code));
        setView("error");
      } finally {
        clearTimeout(timeout);
      }
    },
    [
      abortRef,
      devMode,
      setErrorMsg,
      setErrorKind,
      setResponse,
      setView,
      showResponse,
    ],
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
      // Clear the previous error so a retry never renders the stale banner
      // next to a fresh loading state.
      setErrorMsg("");
      setErrorKind(null);
      // P0-A: drop the PREVIOUS answer the moment a new question begins, not
      // only when it fails. Otherwise the old brief stays mounted behind the
      // loading and error states, so a reader who asks a second question still
      // sees question one's numbers and cannot tell which question they belong
      // to. The numbers were real, but attributing them to the wrong question
      // is its own kind of wrong answer.
      setResponse(null);
      await post(query, { ...periodFilters, ...(extraFilters ?? {}) });
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
      setErrorMsg,
      setErrorKind,
      setResponse,
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
    // No fallback argument: a failed disambiguation retry is an explicit error,
    // never the previous hybrid fixture.
    await post(activeQuestion, filters);
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