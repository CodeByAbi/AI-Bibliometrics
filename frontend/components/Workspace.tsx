"use client";

import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import dynamic from "next/dynamic";
import {
  ArrowRight,
  Check,
  ChevronDown,
  Copy,
  ExternalLink,
  FileText,
  History,
  RotateCcw,
  ShieldCheck,
} from "lucide-react";
import {
  SEEDS,
  fixtureClarify,
  fixtureHybrid,
  fixtureNotFound,
  fixtureSQL,
  fixtureVector,
  parseDataKind,
  pickFixture,
  postAsk,
  resolveDataFixture,
  type AskResponse,
  type DataKind,
  type EvidenceObject,
  type SourceItem,
} from "../lib/api";
import { doiHref, formatMs, formatValue, plural } from "../lib/format";
import { Reveal } from "./Reveal";
import { ConfidenceMeter } from "./CountUp";
import { AnimatePresence } from "motion/react";
import { motionConfig } from "../lib/motion-config";
import { TopBar } from "./TopBar";
import { Sidebar } from "./Sidebar";
import { InspectorBar } from "./InspectorBar";
import { HERO_PERIODS, ResearchHero } from "./ResearchHero";
import { AnswerBrief, RouteBadge } from "./AnswerBrief";
// bundle-11: tab views mount only on user navigation — split them out of
// the initial `/` bundle. Fallback reuses .loading-card (no new CSS).
function ViewFallback({ label }: { label: string }) {
  return (
    <div className="loading-card" role="status" aria-live="polite" aria-label={`${label} loading`}>
      <div className="load-row">
        <span className="load-title">Loading {label}…</span>
      </div>
    </div>
  );
}
const ExploreView = dynamic(() => import("./ExploreView").then((m) => m.ExploreView), {
  ssr: false,
  loading: () => <ViewFallback label="explore view" />,
});
const AuthorDetailView = dynamic(() => import("./AuthorDetailView").then((m) => m.AuthorDetailView), {
  ssr: false,
  loading: () => <ViewFallback label="author view" />,
});
const PublicationDetailView = dynamic(
  () => import("./PublicationDetailView").then((m) => m.PublicationDetailView),
  {
    ssr: false,
    loading: () => <ViewFallback label="publication view" />,
  },
);
import { LoadingCard } from "./LoadingCard";
import {
  evidenceForPublication,
  publicationsForAuthor,
  selectAuthor,
  selectPublication,
  type WorkspaceView,
} from "../lib/views";

const PIPE_LABELS = ["Question", "Retrieval", "Evidence", "Answer"] as const;

function pipeStates(view: WorkspaceView): Array<"done" | "active" | "idle"> {
  if (view === "empty") return ["active", "idle", "idle", "idle"];
  if (view === "loading") return ["done", "active", "idle", "idle"];
  if (view === "clarify") return ["done", "done", "active", "idle"];
  if (view === "notfound") return ["done", "done", "done", "idle"];
  return ["done", "done", "done", "done"];
}

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

const WORKFLOW_STEPS = [  { n: "01", t: "Ask a research question", d: "Indonesian or English, with optional year or institution scope." },
  { n: "02", t: "Read the grounded answer", d: "Narrative generated only from retrieved database records." },
  { n: "03", t: "Inspect evidence", d: "Metric, value, period, and confidence per evidence object." },
  { n: "04", t: "Inspect sources", d: "Title, year, DOI, and relevance for every cited publication." },
] as const;

// rerender-04: rail cards are memoized on their own slice of state, so a
// highlight/copy/expand toggle re-renders only the affected card — not the
// other 49. Handlers arrive as stable callbacks from the parent.
const EvCard = memo(function EvCard({
  ev,
  index,
  requestId,
  expanded,
  highlighted,
  onToggle,
  onSourceClick,
}: {
  ev: EvidenceObject;
  index: number;
  requestId: string;
  expanded: boolean;
  highlighted: boolean;
  onToggle: (i: number) => void;
  onSourceClick: (pubId: string) => void;
}) {
  return (
    <Reveal
      as="article"
      id={`ev-${index}`}
      className="ev-card"
      data-highlight={highlighted}
      delay={Math.min(index * 50, 210) / 1000}
    >
      <div className="ev-top">
        <span className="ev-index" aria-hidden>
          E{index + 1}
        </span>
        <button
          type="button"
          className="ev-toggle"
          aria-expanded={expanded}
          aria-controls={`ev-detail-${index}`}
          onClick={() => onToggle(index)}
        >
          {expanded ? "Hide detail" : "Detail"} <ChevronDown size={13} aria-hidden />
        </button>
      </div>
      <p className="ev-claim">{ev.claim}</p>
      <dl className="ev-grid">
        <div><dt>Metric</dt><dd className="mono">{ev.metric}</dd></div>
        <div><dt>Value</dt><dd className="mono">{formatValue(ev.value)}</dd></div>
        <div><dt>Period</dt><dd className="mono">{ev.period}</dd></div>
        <ConfidenceMeter value={ev.confidence} />
      </dl>
      <div className="ev-detail" id={`ev-detail-${index}`} data-open={expanded}>
        <div className="ev-detail-inner">
          <p className="ev-provenance">
            Traced to {ev.sources.length} source{ev.sources.length === 1 ? "" : "s"}:{" "}
            {ev.sources.map((s) => s.title ?? s.publication_id).join(" · ")}
            <br />
            <span className="mono">{ev.sources.map((s) => s.publication_id).join(", ")}</span>
          </p>
        </div>
      </div>
      <div className="ev-links ev-links-row">
        {ev.sources.map((s) => (
          <button
            key={s.publication_id}
            type="button"
            className="ev-link"
            title={s.title ?? s.publication_id}
            onClick={() => onSourceClick(s.publication_id)}
          >
            {s.publication_id}
          </button>
        ))}
      </div>
    </Reveal>
  );
});

const SrcItem = memo(function SrcItem({
  s,
  highlighted,
  copied,
  animateDelay,
  onOpen,
  onCopy,
  onEvidence,
}: {
  s: SourceItem;
  highlighted: boolean;
  copied: boolean;
  animateDelay: number;
  onOpen: (pubId: string) => void;
  onCopy: (doiOrId: string) => void;
  onEvidence: (pubId: string) => void;
}) {
  const href = doiHref(s.doi);
  return (
    <Reveal
      as="article"
      id={`src-${s.publication_id}`}
      className="src-item"
      data-highlight={highlighted}
      delay={animateDelay}
    >
      <div onClick={() => onOpen(s.publication_id)} role="presentation">
        <p className="src-title">
          <button
            type="button"
            className="src-title-btn"
            onClick={() => onOpen(s.publication_id)}
            title="Open paper provenance detail"
          >
            {s.title || s.publication_id}
          </button>
        </p>
        <div className="src-meta">
          <span className="src-type" data-t={s.source_type}>{s.source_type}</span>
          <span className="mono">{s.year ?? "—"}</span>
          {typeof s.relevance_score === "number" && (
            <span className="src-score" title="Relevance score">{s.relevance_score.toFixed(2)}</span>
          )}
          {href ? (
            <a
              className="src-doi src-ext"
              href={href}
              target="_blank"
              rel="noreferrer"
              onClick={(e) => e.stopPropagation()}
            >
              DOI:{s.doi} <ExternalLink size={11} aria-hidden />
            </a>
          ) : (
            <span className="mono">no-doi</span>
          )}
        </div>
      </div>
      <div className="src-rel">
        {typeof s.relevance_score === "number" && (
          <>
            <div
              className="src-rel-track"
              role="img"
              aria-label={`Relevance ${Math.round(s.relevance_score * 100)} percent`}
            >
              <div
                className="src-rel-fill"
                style={{ width: `${Math.round(s.relevance_score * 100)}%` }}
              />
            </div>
            <span className="src-rel-val">{s.relevance_score.toFixed(2)}</span>
          </>
        )}
        <span className="src-actions">
          <button
            type="button"
            className={`src-act${copied ? " copy-ok" : ""}`}
            onClick={(e) => {
              e.stopPropagation();
              onCopy(s.doi ?? s.publication_id);
            }}
            title={href ? "Copy DOI link" : "Copy publication ID"}
          >
            {copied ? (
              <Check size={11} aria-hidden />
            ) : (
              <Copy size={11} aria-hidden />
            )}
            {copied ? "Copied" : href ? "DOI" : "ID"}
          </button>
          {href && (
            <a
              className="src-act src-ext"
              href={href}
              target="_blank"
              rel="noreferrer"
              onClick={(e) => e.stopPropagation()}
            >
              <ExternalLink size={11} aria-hidden /> Open
            </a>
          )}
          <button
            type="button"
            className="src-act"
            onClick={(e) => {
              e.stopPropagation();
              onEvidence(s.publication_id);
            }}
            title="Highlight the evidence objects traced to this source"
          >
            <ShieldCheck size={11} aria-hidden /> Evidence
          </button>
        </span>
      </div>
      {s.provenance && (
        <p className="mono src-provenance">
          {s.provenance} · {s.publication_id}
        </p>
      )}
    </Reveal>
  );
});

export default function Workspace() {
  const [view, setView] = useState<WorkspaceView>("empty");
  const [question, setQuestion] = useState("");
  const [activeQuestion, setActiveQuestion] = useState("");
  const [response, setResponse] = useState<AskResponse | null>(null);
  const [live, setLive] = useState(false);
  const [devMode, setDevMode] = useState(false);
  // break-ui dataset switch — dev-only (state lab & QA). "demo" = normal behavior.
  const [dataKind, setDataKind] = useState<DataKind>("demo");
  const [errorMsg, setErrorMsg] = useState("");
  const [highlightId, setHighlightId] = useState<string | null>(null);
  const [selectedCand, setSelectedCand] = useState<string | null>(null);
  const [sideOpen, setSideOpen] = useState(false);
  const [sourcesOpen, setSourcesOpen] = useState(true);
  const [expandedEv, setExpandedEv] = useState<Set<number>>(new Set([0]));
  const [copiedDoi, setCopiedDoi] = useState<string | null>(null);
  const [periodIdx, setPeriodIdx] = useState(0);
  const [provenanceOpen, setProvenanceOpen] = useState(false);
  const [selectedPubId, setSelectedPubId] = useState<string | null>(null);
  const [entityFilterLabel, setEntityFilterLabel] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const headingRef = useRef<HTMLHeadingElement | null>(null);
  // Ref mirror of dataKind: loadLab is a stable callback that must see the
  // latest dataset without re-creating on every toggle.
  const dataKindRef = useRef<DataKind>("demo");

  // ?lab=<state> forces a workspace state on first load — state lab & QA only.
  // ?data=<worst|empty|one|huge> swaps the answer fixture — honored ONLY
  // alongside ?lab=, so production paths can never serve adversarial data.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const lab = params.get("lab");
    if (
      !lab ||
      !["empty", "loading", "answer", "clarify", "notfound", "explore", "author", "publication", "error"].includes(lab)
    )
      return;
    setDevMode(true);
    const kind = parseDataKind(params.get("data"));
    dataKindRef.current = kind;
    setDataKind(kind);
    loadLab(lab as WorkspaceView);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ⌘N starts fresh research, ⌘K focuses the inquiry box.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!(e.metaKey || e.ctrlKey)) return;
      if (e.key.toLowerCase() === "n") {
        e.preventDefault();
        newResearch();
      } else if (e.key.toLowerCase() === "k") {
        e.preventDefault();
        document.getElementById("hero-input")?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const toggleEv = useCallback((i: number) => {
    setExpandedEv((prev) => {
      const next = new Set(prev);
      if (next.has(i)) next.delete(i);
      else next.add(i);
      return next;
    });
  }, []);

  const copyDoi = useCallback(async (doi: string) => {
    try {
      // DOI strings contain "/"; bare publication IDs are copied verbatim.
      const text = doi.startsWith("http") ? doi : doi.includes("/") ? `https://doi.org/${doi}` : doi;
      await navigator.clipboard.writeText(text);
      setCopiedDoi(doi);
      setTimeout(() => setCopiedDoi((c) => (c === doi ? null : c)), 1600);
    } catch {
      setCopiedDoi(null);
    }
  }, []);

  const scrollBehavior = (): ScrollBehavior =>
    motionConfig.shouldAnimate({ essential: true }) ? "smooth" : "auto";

  const scrollToEvidence = useCallback(
    (pubId: string) => {
      const idx = response?.evidence_objects.findIndex((ev) => ev.sources.some((s) => s.publication_id === pubId)) ?? -1;
      setHighlightId(pubId);
      if (idx >= 0) {
        document.getElementById(`ev-${idx}`)?.scrollIntoView({ behavior: scrollBehavior(), block: "center" });
      }
    },
    [response],
  );

  const citeFromAnswer = useCallback(
    (pubId: string) => {
      setHighlightId(pubId);
      setSourcesOpen(true);
      // Let the rail expand before scrolling to the bibliography card.
      requestAnimationFrame(() => {
        document.getElementById(`src-${pubId}`)?.scrollIntoView({ behavior: scrollBehavior(), block: "center" });
      });
    },
    [],
  );

  const openPublication = useCallback((pubId: string) => {
    setSelectedPubId(pubId);
    setHighlightId(pubId);
    setView("publication");
    window.scrollTo({ top: 0, behavior: scrollBehavior() });
  }, []);

  // Shared evidence→source jump: the rail is always mounted, so no
  // requestAnimationFrame wait is needed (unlike citeFromAnswer).
  const focusSource = useCallback((pubId: string) => {
    setHighlightId(pubId);
    setSourcesOpen(true);
    document.getElementById(`src-${pubId}`)?.scrollIntoView({ behavior: scrollBehavior(), block: "center" });
  }, []);

  const showResponse = useCallback((r: AskResponse, isLive: boolean) => {
    setResponse(r);
    setLive(isLive);
    setSourcesOpen(true);
    setExpandedEv(new Set([0]));
    setSelectedPubId(r.sources.length ? r.sources[0]!.publication_id : null);
    if (r.status === "ok") setView("answer");
    else if (r.status === "needs_clarification") setView("clarify");
    else if (r.status === "not_found") setView("notfound");
    else setView("answer");
  }, []);

  const runAsk = useCallback(
    async (q: string, extraFilters?: Record<string, string | number | null | undefined>) => {
      const query = q.trim();
      if (query.length < 3 || view === "loading") return;
      abortRef.current?.abort();
      const ctrl = new AbortController();
      abortRef.current = ctrl;
      const timeout = setTimeout(() => ctrl.abort(), 8000);
      // The hero period chip is a real year_from/year_to filter.
      const periodFilters = (HERO_PERIODS[periodIdx] ?? HERO_PERIODS[0]!).filters;
      const filters = { ...periodFilters, ...(extraFilters ?? {}) };
      setActiveQuestion(query);
      setQuestion(query);
      setView("loading");
      setSelectedCand(null);
      setHighlightId(null);
      setEntityFilterLabel(null);
      try {
        const r = await postAsk(query, devMode, ctrl.signal, filters);
        clearTimeout(timeout);
        showResponse(r, true);
      } catch {
        clearTimeout(timeout);
        if (ctrl.signal.aborted && query.length > 0) {
          // Aborted by a newer ask; ignore.
          return;
        }
        // Honest fallback: backend unreachable in prototype — use matching fixture.
        showResponse(pickFixture(query), false);
      }
    },
    [devMode, showResponse, view, periodIdx],
  );

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
    // Re-post the original question with the disambiguated entity as a
    // structured filter so EntityResolutionGate resolves to a single ID.
    // Offline (no backend) falls back to the Hybrid fixture snapshot.
    abortRef.current?.abort();
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    const timeout = setTimeout(() => ctrl.abort(), 8000);
    setView("loading");
    try {
      const r = await postAsk(activeQuestion, devMode, ctrl.signal, filters);
      clearTimeout(timeout);
      showResponse(r, true);
    } catch {
      clearTimeout(timeout);
      if (!ctrl.signal.aborted) showResponse(fixtureHybrid, false);
    }
  }, [selectedCand, response, activeQuestion, devMode, showResponse]);

  // Focus result heading on view change for keyboard users.
  useEffect(() => {
    if (view !== "empty" && view !== "loading" && view !== "error") {
      headingRef.current?.focus({ preventScroll: false });
    }
  }, [view]);

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
        runAsk(activeQuestion.trim() || question.trim() || SEEDS[0].question);
        return;
      }
      if (v === "answer") {
        const f = resolveDataFixture(dataKindRef.current) ?? fixtureHybrid;
        setActiveQuestion(dataQuestion(dataKindRef.current));
        showResponse(f, false);
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
      if (v === "explore" || v === "author" || v === "publication") {
        if (!response) {
          setActiveQuestion(SEEDS[0].question);
          setResponse(fixtureHybrid);
          setLive(false);
          setSelectedPubId(fixtureHybrid.sources[0]?.publication_id ?? null);
        }
        setView(v);
        window.scrollTo({ top: 0, behavior: scrollBehavior() });
        return;
      }
      setActiveQuestion(question.trim() || SEEDS[0].question);
      setErrorMsg("The database query exceeded its 10-second limit. Narrow the year range, or add an institution or author filter, then retry.");
      setView("error");
      setResponse(null);
    },
    [question, activeQuestion, response, runAsk, showResponse],
  );

  /** Break-ui dataset switch — dev-only. Swaps the answer fixture instantly. */
  const applyDataKind = useCallback(
    (k: DataKind) => {
      abortRef.current?.abort();
      dataKindRef.current = k;
      setDataKind(k);
      setSelectedCand(null);
      setHighlightId(null);
      const f = resolveDataFixture(k);
      if (f) {
        setActiveQuestion(dataQuestion(k));
        showResponse(f, false);
      } else {
        setActiveQuestion(SEEDS[0].question);
        showResponse(fixtureHybrid, false);
      }
    },
    [showResponse],
  );

  /** Top-bar tab navigation — explores detail views, re-runs retrieval. */
  const switchView = useCallback(
    (v: WorkspaceView) => {
      if (v === view) {
        window.scrollTo({ top: 0, behavior: scrollBehavior() });
        return;
      }
      if (v === "loading") {
        runAsk(activeQuestion.trim() || question.trim() || SEEDS[0].question);
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
    [view, response, runAsk, loadLab, activeQuestion, question],
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
  }, []);

  const pipe = pipeStates(view);

  const latencyRows = useMemo(() => {
    const b = response?.debug?.latency_breakdown_ms;
    if (!b) return [];
    return Object.entries(b);
  }, [response]);

  const selectedPub = useMemo(() => selectPublication(response, selectedPubId), [response, selectedPubId]);
  const selectedPubEvidence = useMemo(
    () => (selectedPub ? evidenceForPublication(response, selectedPub.publication_id) : []),
    [response, selectedPub],
  );
  const selectedAuthor = useMemo(() => selectAuthor(response, selectedCand), [response, selectedCand]);
  const authorPubs = useMemo(() => publicationsForAuthor(response, selectedAuthor), [response, selectedAuthor]);

  const renderClarify = (inAnswerTab: boolean) => {
    const cands = response?.candidates ?? [];
    if (!cands.length) {
      return (
        <section className="panel-card reveal" aria-labelledby={inAnswerTab ? "clarify-latest-title" : "clarify-title"}>
          <h2 id={inAnswerTab ? "clarify-latest-title" : "clarify-title"} ref={headingRef} tabIndex={-1}>
            No ambiguous entities
          </h2>
          <p className="sub">
            The current answer set needs no disambiguation. Ask an author- or institution-scoped question — or load the
            corpus disambiguation example.
          </p>
          <div className="resolve-row">
            <button
              type="button"
              className="ask-btn btn-resolve"
              onClick={() => {
                setActiveQuestion("Which Rahman collaborates with Bandung labs?");
                showResponse(fixtureClarify, false);
              }}
            >
              Load corpus example <ArrowRight size={15} aria-hidden />
            </button>
          </div>
        </section>
      );
    }
    return (
      <section className="panel-card reveal" aria-labelledby={inAnswerTab ? "clarify-latest-title" : "clarify-title"}>
        <h2 id={inAnswerTab ? "clarify-latest-title" : "clarify-title"} ref={headingRef} tabIndex={-1}>
          Needs clarification
        </h2>
        <p className="sub">
          {response && <RouteBadge route={response.route} fallback={response.answered_via_fallback} />} {response?.answer}
        </p>
        <div className="cand-grid" role="group" aria-label="Candidate entities">
          {cands.map((c) => (
            <button
              key={c.id}
              type="button"
              className="cand"
              aria-pressed={selectedCand === c.id}
              onClick={() => setSelectedCand(c.id)}
            >
              <p className="cand-name">{c.name}</p>
              <p className="cand-aff">{c.affiliation ?? "—"}</p>
              <span className="cand-foot">
                <span className="cand-type">{c.type}</span>
                <span className="mono">{plural(c.publication_count, "pub", "pubs")}</span>
              </span>
              <span className="cand-check" aria-hidden>
                <Check size={12} strokeWidth={3} />
              </span>
            </button>
          ))}
        </div>
        <div className="resolve-row">
          <button
            type="button"
            className="ask-btn btn-resolve"
            disabled={!selectedCand}
            onClick={resolveCandidate}
          >
            Resolve with selected entity <ArrowRight size={15} aria-hidden />
          </button>
          {!selectedCand && <span className="resolve-hint">Select a card to continue retrieval.</span>}
        </div>
      </section>
    );
  };

  const renderNotFound = (inAnswerTab: boolean) => {
    if (response?.status !== "not_found" || !response) {
      return (
        <section className="notfound reveal" aria-labelledby={inAnswerTab ? "nf-latest-title" : "nf-title"}>
          <h2 id={inAnswerTab ? "nf-latest-title" : "nf-title"} ref={headingRef} tabIndex={-1}>
            No zero-state in this answer set
          </h2>
          <p>
            The current result is grounded. To inspect the deterministic zero-state, load the corpus example that
            matches nothing.
          </p>
          <div className="nf-actions">
            <button
              type="button"
              className="seed"
              onClick={() => {
                setActiveQuestion("Quantum-dot yields in deep-sea fisheries after 2020?");
                showResponse(fixtureNotFound, false);
              }}
            >
              Load corpus example
            </button>
          </div>
        </section>
      );
    }
    return (
      <section className="notfound reveal" aria-labelledby={inAnswerTab ? "nf-latest-title" : "nf-title"}>
        <h2 id={inAnswerTab ? "nf-latest-title" : "nf-title"} ref={headingRef} tabIndex={-1}>
          No supporting evidence found
        </h2>
        <p>{response.answer}</p>
        <div className="nf-grid">
          <div className="nf-cell">
            <h3>What was searched</h3>
            <p title={activeQuestion}>
              “{activeQuestion.length > 140 ? `${activeQuestion.slice(0, 140)}…` : activeQuestion}” via{" "}
              <span className="mono">[{response.route}]</span> over Scopus publications, embedded chunks,
              and collaboration edges.
            </p>
          </div>
          <div className="nf-cell">
            <h3>Why it stopped</h3>
            <p>
              {response.debug?.route_reasoning ??
                "The evidence gate admitted zero records, so synthesis was skipped deterministically — no language model was called."}
            </p>
          </div>
        </div>
        <div className="nf-actions">
          <button type="button" className="seed" onClick={() => runAsk(SEEDS[0].question)}>
            Try: {SEEDS[0].label}
          </button>
          <button type="button" className="seed" onClick={() => runAsk(SEEDS[1].question)}>
            Try: {SEEDS[1].label}
          </button>
        </div>
        <p className="mono">status: not_found · route: {response.route} · short-circuit, no LLM call</p>
      </section>
    );
  };

  const railHidden = view === "author" || view === "publication";

  return (
    <div className="app-top page-enter">
      <TopBar
        activeView={view}
        onSwitch={switchView}
        provenanceOpen={provenanceOpen}
        onToggleProvenance={() => setProvenanceOpen((o) => !o)}
        onOpenAuthor={() => switchView("author")}
        sideOpen={sideOpen}
        onToggleSidebar={() => setSideOpen((o) => !o)}
      />

      <div className="app-shell">
        {sideOpen && <div className="scrim" onClick={() => setSideOpen(false)} aria-hidden />}

        <Sidebar
          activeView={view}
          live={live && view !== "empty"}
          devMode={devMode}
          sideOpen={sideOpen}
          onNavigate={switchView}
          onNewResearch={newResearch}
          onAsk={runAsk}
          onDevToggle={() => setDevMode((d) => !d)}
          onClose={() => setSideOpen(false)}
        />

        {/* ---------------- Main ---------------- */}
        <div className="main">
          <div className="statebar" role="toolbar" aria-label="Workspace status">
            <span className="provenance-note" data-live={live && view !== "empty"}>
              <i aria-hidden />
              {live && view !== "empty" ? "Live database" : "Prototype snapshot"}
              <span className="mono" style={{ fontSize: 11 }}>· Scopus · ID/EN</span>
            </span>
            {devMode && (
              <span className="statebar-group" role="group" aria-label="State lab — preview every workspace state">
                <span className="statebar-hint">State lab</span>
                {(["empty", "loading", "answer", "clarify", "notfound", "explore", "author", "publication", "error"] as WorkspaceView[]).map((s) => (
                  <button
                    key={s}
                    type="button"
                    className="chip-state"
                    aria-pressed={view === s}
                    onClick={() => loadLab(s)}
                  >
                    {s === "notfound" ? "not-found" : s}
                  </button>
                ))}
              </span>
            )}
            {devMode && (
              <span className="statebar-group" role="group" aria-label="Break-ui dataset — swap the answer fixture">
                <span className="statebar-hint">Data</span>
                {(["demo", "worst", "empty", "one", "huge"] as DataKind[]).map((k) => (
                  <button
                    key={k}
                    type="button"
                    className="chip-state"
                    aria-pressed={dataKind === k}
                    onClick={() => applyDataKind(k)}
                    title={k === "demo" ? "Normal fixtures" : `Adversarial fixture: ${k}`}
                  >
                    {k}
                  </button>
                ))}
              </span>
            )}
            <button type="button" className="dev-toggle" aria-pressed={devMode} onClick={() => setDevMode((d) => !d)}>
              Dev inspector <span className="switch" aria-hidden />
            </button>
          </div>

          <InspectorBar
            open={provenanceOpen}
            onClose={() => setProvenanceOpen(false)}
            response={response}
            live={live && view !== "empty"}
            activeQuestion={activeQuestion}
          />

          <main className={`workspace${railHidden ? " rail-hidden" : ""}`} id="workspace-main">
            <div className="center-col">
              <h1>Research Intelligence Workspace</h1>
              <p className="lede">
                Ask in Indonesian or English. Every figure below is traced to database-backed evidence — narrative is
                generated, numbers are not.
              </p>

              <ResearchHero
                question={question}
                onSynthesize={(q) => runAsk(q)}
                loading={view === "loading"}
                periodIdx={periodIdx}
                onCyclePeriod={() => setPeriodIdx((i) => (i + 1) % HERO_PERIODS.length)}
                entityFilterLabel={entityFilterLabel}
                onAddFilter={() => switchView("clarify")}
                live={live && view !== "empty"}
                devMode={devMode}
              />

              <ol className="pipeline" aria-label="Research pipeline: question to grounded answer">
                {PIPE_LABELS.map((label, i) => (
                  <li key={label} data-state={pipe[i]}>
                    {i > 0 && <span className="pipe-bar" aria-hidden />}
                    <span className="pipe-dot" aria-hidden>
                      {pipe[i] === "done" ? <Check size={11} strokeWidth={3} /> : i + 1}
                    </span>
                    {label}
                  </li>
                ))}
              </ol>
              {activeQuestion && view !== "empty" && (
                <div className="active-q">
                  <span className="mono" aria-hidden>
                    Q
                  </span>
                  <span title={activeQuestion}>
                    <strong>“{activeQuestion.length > 110 ? `${activeQuestion.slice(0, 110)}…` : activeQuestion}”</strong>
                    {response && view !== "loading" && view !== "error" && (
                      <span className="mono"> · [{response.route}]</span>
                    )}
                  </span>
                </div>
              )}

              {view === "empty" && (
                <>
                  <div className="seeds" role="group" aria-label="Example questions">
                    <span className="seeds-label" aria-hidden>Try</span>
                    {SEEDS.map((s) => (
                      <button
                        key={s.id}
                        type="button"
                        className="seed"
                        onClick={() => runAsk(s.question)}
                        aria-label={`Example question: ${s.question}`}
                      >
                        {s.label}
                      </button>
                    ))}
                  </div>
                  <div className="empty-hero">
                    <h2>Start with a question the database can answer</h2>
                    <p>
                      Try “{SEEDS[0].question}” — the workspace selects a retrieval route, unifies evidence objects,
                      then synthesizes a cited narrative. Ambiguous names pause for clarification; empty evidence ends in a
                      calm stop, never an invented answer.
                    </p>
                    <ol className="workflow-steps" aria-label="How to use the workspace">
                      {WORKFLOW_STEPS.map((w) => (
                        <li key={w.n}>
                          <span className="step-n">{w.n}</span>
                          <span className="step-t">{w.t}</span>
                          <span className="step-d">{w.d}</span>
                        </li>
                      ))}
                    </ol>
                  </div>
                </>
              )}

              {view === "loading" && <LoadingCard activeQuestion={activeQuestion} />}

              <AnimatePresence mode="wait">
                {view === "answer" && response && (
                  <Reveal key={response.request_id} aria-live="polite" delay={0}>
                    {response.status === "ok" && (
                      <AnswerBrief
                        response={response}
                        live={live}
                        highlightId={highlightId}
                        onCite={citeFromAnswer}
                        titleRef={headingRef}
                      />
                    )}
                    {response.status === "needs_clarification" && renderClarify(true)}
                    {response.status === "not_found" && renderNotFound(true)}

                    {(devMode || response.debug) && (
                      <details className="debug" open={devMode}>
                        <summary>
                          <History size={13} aria-hidden /> Debug inspector
                          <span className="mono debug-id">{response.request_id ?? "—"}</span>
                        </summary>
                        <div className="debug-body">
                          {response.debug?.route_reasoning && (
                            <div>
                              <div className="mono debug-label">route_reasoning</div>
                              <pre>{response.debug.route_reasoning}</pre>
                            </div>
                          )}
                          {response.debug?.sql_executed && (
                            <div>
                              <div className="mono debug-label">sql_executed</div>
                              <pre>{response.debug.sql_executed}</pre>
                            </div>
                          )}
                          {latencyRows.length > 0 && (
                            <table className="lat-table" aria-label="Latency breakdown">
                              <thead><tr><th>stage</th><th>ms</th></tr></thead>
                              <tbody>
                                {latencyRows.map(([k, v]) => (
                                  <tr key={k}><td>{k}</td><td>{formatMs(v)}</td></tr>
                                ))}
                              </tbody>
                            </table>
                          )}
                          {response.unverified_citations.length > 0 && (
                            <pre>unverified_citations: {JSON.stringify(response.unverified_citations, null, 2)}</pre>
                          )}
                        </div>
                      </details>
                    )}
                  </Reveal>
                )}
                {view === "clarify" && response && (
                  <Reveal key={`${response.request_id}-clarify`} aria-live="polite" delay={0}>
                    {renderClarify(false)}
                  </Reveal>
                )}
                {view === "notfound" && response && (
                  <Reveal key={`${response.request_id}-notfound`} aria-live="polite" delay={0}>
                    {renderNotFound(false)}
                  </Reveal>
                )}
              </AnimatePresence>

              {view === "clarify" && !response && renderClarify(false)}
              {view === "notfound" && !response && renderNotFound(false)}

              {view === "explore" && (
                <Reveal key="explore" delay={0}>
                  <ExploreView response={response} onOpenPublication={openPublication} titleRef={headingRef} />
                </Reveal>
              )}

              {view === "author" && (
                <Reveal key="author" delay={0}>
                  <AuthorDetailView
                    author={selectedAuthor}
                    publications={authorPubs}
                    onOpenPublication={openPublication}
                    onResolve={() => switchView("clarify")}
                    titleRef={headingRef}
                  />
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
                  <h2 id="err-title">The request could not be completed</h2>
                  <p>{errorMsg}</p>
                  <button type="button" className="btn-retry" onClick={() => runAsk(activeQuestion || question)}>
                    <RotateCcw size={15} aria-hidden /> Retry query
                  </button>
                </section>
              )}

              {response && view !== "empty" && view !== "loading" && (
                <div className="seeds" role="group" aria-label="Try another example">
                  <span className="seeds-label" aria-hidden>Replay</span>
                  {[fixtureSQL, fixtureVector, fixtureHybrid].map((f, i) => (
                    <button
                      key={f.request_id}
                      type="button"
                      className="seed"
                      onClick={() => showResponse(f, false)}
                      title={SEEDS[i].question}
                    >
                      [{f.route}]
                    </button>
                  ))}
                </div>
              )}
            </div>

            {/* ---------------- Provenance rail ---------------- */}
            {!railHidden && (
              <div className="rail" aria-label="Evidence and sources">
                <section className="rail-section" aria-labelledby="ev-h">
                  <div className="rail-head" id="ev-h">
                    <ShieldCheck size={14} aria-hidden className="icon-muted" /> Verified Evidence
                    <span className="count">{response?.evidence_objects.length ?? 0}</span>
                  </div>
                  <div className="rail-body">
                    {!response || response.evidence_objects.length === 0 ? (
                      <p className="rail-empty">
                        {view === "clarify"
                          ? "Evidence unlocks after entity disambiguation."
                          : view === "notfound"
                            ? "No evidence objects — the gate stopped before synthesis."
                            : "Evidence objects appear here with metric, value, period, and confidence."}
                      </p>
                    ) : (
                      response.evidence_objects.map((ev, i) => (
                        <EvCard
                          key={`${ev.metric}-${i}-${response.request_id}`}
                          ev={ev}
                          index={i}
                          requestId={response.request_id}
                          expanded={expandedEv.has(i)}
                          highlighted={ev.sources.some((s) => s.publication_id === highlightId)}
                          onToggle={toggleEv}
                          onSourceClick={focusSource}
                        />
                      ))
                    )}
                  </div>
                </section>

                <section className="rail-section" aria-labelledby="src-h">
                  <div className="rail-head" id="src-h">
                    <FileText size={14} aria-hidden className="icon-muted" /> Sources
                    <span className="count">{response?.sources.length ?? 0}</span>
                  </div>
                  <div className="rail-body">
                    {!response || response.sources.length === 0 ? (
                      <p className="rail-empty">
                        Linked publications with title, year, DOI, relevance, and source type appear here.
                      </p>
                    ) : (
                      <>
                        <button
                          type="button"
                          className="chip-state disclosure-btn"
                          aria-expanded={sourcesOpen}
                          onClick={() => setSourcesOpen((o) => !o)}
                          style={{ alignSelf: "flex-start" }}
                        >
                          {sourcesOpen ? "Collapse" : "Expand"} {response.sources.length} source{response.sources.length === 1 ? "" : "s"}
                          <ChevronDown size={13} aria-hidden />
                        </button>
                        <div className="collapsible" data-collapsed={!sourcesOpen}>
                          <div className="collapsible-inner">
                            {response.sources.map((s, i) => (
                              <SrcItem
                                key={s.publication_id}
                                s={s}
                                highlighted={highlightId === s.publication_id}
                                copied={copiedDoi === (s.doi ?? s.publication_id)}
                                animateDelay={sourcesOpen ? Math.min(i * 50, 180) / 1000 : 0}
                                onOpen={openPublication}
                                onCopy={copyDoi}
                                onEvidence={scrollToEvidence}
                              />
                            ))}
                          </div>
                        </div>
                      </>
                    )}
                  </div>
                </section>
              </div>
            )}
          </main>
        </div>
      </div>
    </div>
  );
}
