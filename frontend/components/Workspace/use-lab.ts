"use client";

import { useCallback, useEffect } from "react";
import {
  fixtureClarify,
  fixtureHybrid,
  fixtureNotFound,
  parseDataKind,
  resolveDataFixture,
  SEEDS,
  type DataKind,
} from "../../lib/api";
import type { WorkspaceView } from "../../lib/views";
import type { WorkspaceStore } from "./use-workspace-store";

const LAB_VIEWS: WorkspaceView[] = ["empty", "loading", "answer", "clarify", "notfound", "explore", "publication", "error"];

/** Active-question label per break-ui dataset — dev-only copy, never user-facing. */
function dataQuestion(kind: DataKind): string {
  switch (kind) {
    case "worst":
      return "Break-ui worst-case dataset: unbounded text, runtime nulls, edge years";
    case "empty":
      return "Empty evidence set probe — rail empty states";
    case "one":
      return "Single-evidence probe — singular labels";
    case "huge":
      return "LIMIT-50 ceiling probe — fifty evidence objects";
    default:
      return SEEDS[0].question;
  }
}

export const LAB_VIEW_LIST = LAB_VIEWS;
export const LAB_DATA_LIST: DataKind[] = ["demo", "worst", "empty", "one", "huge"];

/**
 * State lab and break-ui dataset switch — dev-only tooling that forces any
 * workspace state without a backend. `?lab=<state>` runs once on first load;
 * `?data=<worst|empty|one|huge>` swaps the answer fixture and is honored ONLY
 * alongside `?lab=`.
 *
 * P0-A: `?lab=` was previously honored in production, so `?lab=answer` on a
 * deployed build forced `fixtureHybrid` — a brief full of invented
 * publication counts and expertise scores — with no dev-mode toggle and no
 * indication it was not real. Both the state override and the fixture
 * datasets are now gated behind `NODE_ENV !== "production"`, so a deployed
 * build ignores the query string entirely. The in-app StateBar controls stay
 * available (they are behind the dev-mode toggle) for local testing.
 */
function labEnabled(): boolean {
  return process.env.NODE_ENV !== "production";
}

export function useLab(store: WorkspaceStore, runAsk: (q: string) => Promise<void>) {
  const {
    question,
    activeQuestion,
    response,
    abortRef,
    dataKindRef,
    setView,
    setResponse,
    setActiveQuestion,
    setSelectedCand,
    setSelectedPubId,
    setHighlightId,
    setEntityFilterLabel,
    setErrorMsg,
    setDevMode,
    setDataKind,
    showResponse,
  } = store;

  const loadLab = useCallback(
    (v: WorkspaceView) => {
      abortRef.current?.abort();
      setSelectedCand(null);
      setHighlightId(null);
      if (v === "empty") {
        setView("empty");
        setResponse(null);
        return;
      }
      if (v === "loading") {
        void runAsk(activeQuestion.trim() || question.trim() || SEEDS[0].question);
        return;
      }
      if (v === "answer") {
        const fixture = resolveDataFixture(dataKindRef.current) ?? fixtureHybrid;
        setActiveQuestion(dataQuestion(dataKindRef.current));
        showResponse(fixture, false);
        return;
      }
      if (v === "clarify") {
        setActiveQuestion("Which Rahman collaborates with Bandung labs?");
        showResponse(fixtureClarify, false);
        return;
      }
      if (v === "notfound") {
        setActiveQuestion("Quantum-dot yields in deep-sea fisheries after 2020?");
        showResponse(fixtureNotFound, false);
        return;
      }
      if (v === "explore" || v === "publication") {
        if (!response) {
          setActiveQuestion(SEEDS[0].question);
          setResponse(fixtureHybrid);
          setSelectedPubId(fixtureHybrid.sources[0]?.publication_id ?? null);
        }
        setView(v);
        return;
      }
      setActiveQuestion(question.trim() || SEEDS[0].question);
      setErrorMsg(
        "The database query exceeded its 10-second limit. Narrow the year range, or add an institution or author filter, then retry.",
      );
      setView("error");
      setResponse(null);
    },
    [
      question,
      activeQuestion,
      response,
      runAsk,
      showResponse,
      abortRef,
      dataKindRef,
      setView,
      setResponse,
      setActiveQuestion,
      setSelectedCand,
      setSelectedPubId,
      setHighlightId,
      setErrorMsg,
    ],
  );

  const applyDataKind = useCallback(
    (k: DataKind) => {
      abortRef.current?.abort();
      dataKindRef.current = k;
      setDataKind(k);
      setSelectedCand(null);
      setHighlightId(null);
      // P0-A: never serve an adversarial dataset outside development.
      const fixture = labEnabled() ? resolveDataFixture(k) : null;
      if (fixture) {
        setActiveQuestion(dataQuestion(k));
        showResponse(fixture, false);
      } else {
        setActiveQuestion(SEEDS[0].question);
        showResponse(fixtureHybrid, false);
      }
    },
    [
      abortRef,
      dataKindRef,
      setDataKind,
      setSelectedCand,
      setHighlightId,
      setActiveQuestion,
      showResponse,
    ],
  );

  useEffect(() => {
    // P0-A: production builds ignore ?lab= and ?data= entirely.
    if (!labEnabled()) return;
    const params = new URLSearchParams(window.location.search);
    const lab = params.get("lab");
    if (!lab || !LAB_VIEWS.includes(lab as WorkspaceView)) return;
    setDevMode(true);
    const kind = parseDataKind(params.get("data"));
    dataKindRef.current = kind;
    setDataKind(kind);
    loadLab(lab as WorkspaceView);
    // First-load only: later navigation must not be overridden by the URL.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return { loadLab, applyDataKind };
}