"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { AskResponse, DataKind } from "../../lib/api";
import type { WorkspaceView } from "../../lib/views";
import {
  evidenceForPublication,
  publicationsForAuthor,
  selectAuthor,
  selectPublication,
} from "../../lib/views";
import type { TitleRef } from "./types";

/**
 * Pure workspace state: view machine, retrieval result, and the selection
 * flags the rail and detail views read. No network calls and no dev-lab
 * parameter handling live here — those belong to use-ask and use-lab so each
 * file stays single-purpose.
 */
export function useWorkspaceStore() {
  const [view, setView] = useState<WorkspaceView>("empty");
  const [question, setQuestion] = useState("");
  const [activeQuestion, setActiveQuestion] = useState("");
  const [response, setResponse] = useState<AskResponse | null>(null);
  const [live, setLive] = useState(false);
  const [devMode, setDevMode] = useState(false);
  const [dataKind, setDataKind] = useState<DataKind>("demo");
  const [errorMsg, setErrorMsg] = useState("");
  const [highlightId, setHighlightId] = useState<string | null>(null);
  const [selectedCand, setSelectedCand] = useState<string | null>(null);
  const [sideOpen, setSideOpen] = useState(false);
  const [sourcesOpen, setSourcesOpen] = useState(true);
  const [expandedEv, setExpandedEv] = useState<Set<number>>(() => new Set([0]));
  const [copiedDoi, setCopiedDoi] = useState<string | null>(null);
  const [periodIdx, setPeriodIdx] = useState(0);
  const [provenanceOpen, setProvenanceOpen] = useState(false);
  const [selectedPubId, setSelectedPubId] = useState<string | null>(null);
  const [entityFilterLabel, setEntityFilterLabel] = useState<string | null>(null);

  const abortRef = useRef<AbortController | null>(null);
  const headingRef = useRef<HTMLHeadingElement>(null);
  // Ref mirror of dataKind: loadLab and applyDataKind are stable callbacks
  // that must observe the latest dataset without being re-created on toggle.
  const dataKindRef = useRef<DataKind>("demo");
  const copyResetRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  /** Single funnel for every retrieval result — live or fixture-sourced. */
  const showResponse = useCallback((r: AskResponse, isLive: boolean) => {
    setResponse(r);
    setLive(isLive);
    setSourcesOpen(true);
    setExpandedEv(new Set([0]));
    setSelectedPubId(r.sources.length ? (r.sources[0]?.publication_id ?? null) : null);
    if (r.status === "ok") setView("answer");
    else if (r.status === "needs_clarification") setView("clarify");
    else if (r.status === "not_found") setView("notfound");
    else setView("answer");
  }, []);

  const toggleEv = useCallback((i: number) => {
    setExpandedEv((prev) => {
      const next = new Set(prev);
      if (next.has(i)) next.delete(i);
      else next.add(i);
      return next;
    });
  }, []);

  const copyDoi = useCallback(async (doiOrId: string) => {
    try {
      // DOI strings contain "/"; bare publication IDs are copied verbatim.
      const text = doiOrId.startsWith("http")
        ? doiOrId
        : doiOrId.includes("/")
          ? `https://doi.org/${doiOrId}`
          : doiOrId;
      await navigator.clipboard.writeText(text);
      setCopiedDoi(doiOrId);
      // A newer copy supersedes the pending reset; the handle is released on
      // unmount so the timer never calls setState on a dead component.
      if (copyResetRef.current) clearTimeout(copyResetRef.current);
      copyResetRef.current = setTimeout(() => setCopiedDoi(null), 1600);
    } catch (error) {
      console.error("Clipboard copy failed:", error);
      setCopiedDoi(null);
    }
  }, []);

  useEffect(
    () => () => {
      if (copyResetRef.current) clearTimeout(copyResetRef.current);
    },
    [],
  );

  const latencyRows = useMemo(() => {
    const breakdown = response?.debug?.latency_breakdown_ms;
    if (!breakdown) return [];
    return Object.entries(breakdown);
  }, [response]);

  const selectedPub = useMemo(() => selectPublication(response, selectedPubId), [response, selectedPubId]);
  const selectedPubEvidence = useMemo(
    () => (selectedPub ? evidenceForPublication(response, selectedPub.publication_id) : []),
    [response, selectedPub],
  );
  const selectedAuthor = useMemo(() => selectAuthor(response, selectedCand), [response, selectedCand]);
  const authorPubs = useMemo(() => publicationsForAuthor(response, selectedAuthor), [response, selectedAuthor]);

  return {
    // state
    view,
    question,
    activeQuestion,
    response,
    devMode,
    dataKind,
    errorMsg,
    highlightId,
    selectedCand,
    sideOpen,
    sourcesOpen,
    expandedEv,
    copiedDoi,
    periodIdx,
    provenanceOpen,
    selectedPubId,
    entityFilterLabel,
    // refs
    abortRef,
    headingRef,
    dataKindRef,
    copyResetRef,
    // setters
    setView,
    setQuestion,
    setActiveQuestion,
    setResponse,
    setDevMode,
    setDataKind,
    setErrorMsg,
    setHighlightId,
    setSelectedCand,
    setSideOpen,
    setSourcesOpen,
    setCopiedDoi,
    setPeriodIdx,
    setProvenanceOpen,
    setSelectedPubId,
    setEntityFilterLabel,
    // behaviour
    showResponse,
    toggleEv,
    copyDoi,
    // derived
    isLive: live && view !== "empty",
    latencyRows,
    selectedPub,
    selectedPubEvidence,
    selectedAuthor,
    authorPubs,
  };
}

export type WorkspaceStore = ReturnType<typeof useWorkspaceStore>;
export type { TitleRef };