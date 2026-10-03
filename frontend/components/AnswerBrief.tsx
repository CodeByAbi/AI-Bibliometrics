"use client";

import { Database, Layers, Network, Search, ShieldCheck, TriangleAlert } from "lucide-react";
import type { AskResponse, RouteKind } from "../lib/api";
import { matchCitationToSource, renderAnswerParts, shortId, splitLead, statusLabel } from "../lib/format";
import type { TitleRef } from "./Workspace/types";

const ROUTE_ICON: Record<RouteKind, typeof Database> = {
  SQLRoute: Database,
  VectorRoute: Search,
  GraphRoute: Network,
  HybridRoute: Layers,
};

export function RouteBadge({ route, fallback }: { route: RouteKind; fallback: boolean }) {
  const Icon = ROUTE_ICON[route];
  return (
    <span className="route-badge" data-route={route} title="Retrieval route selected by the Question Router">
      <Icon size={13} strokeWidth={2.2} aria-hidden />
      [{route}
      {fallback ? "*" : ""}]
    </span>
  );
}

interface AnswerBriefProps {
  response: AskResponse;
  live: boolean;
  highlightId: string | null;
  onCite: (pubId: string) => void;
  titleRef: TitleRef;
}

/**
 * Editorial synthesized brief (HTML mockup §4): conclusion-first narrative
 * with clickable citations. Clicking a citation jumps to the bibliography
 * card; clicking a bibliography card lights the citations that trace to it —
 * one highlight id drives both directions.
 */
export function AnswerBrief({ response, live, highlightId, onCite, titleRef }: AnswerBriefProps) {
  const [lead, rest] = splitLead(response.answer);
  const leadParts = renderAnswerParts(lead);
  const restParts = renderAnswerParts(rest);

  const renderParts = (parts: Array<{ kind: "text" | "cite"; value: string }>, keyPrefix: string) =>
    parts.map((p, i) => {
      if (p.kind !== "cite") return <span key={`${keyPrefix}-${i}`}>{p.value}</span>;
      const pubId = matchCitationToSource(p.value, response.sources);
      const linked = response.sources.find((s) => s.publication_id === pubId);
      return (
        <button
          key={`${keyPrefix}-${i}`}
          type="button"
          className="cite cite-anchor"
          data-active={pubId !== null && pubId === highlightId}
          onClick={() => {
            if (pubId) onCite(pubId);
          }}
          disabled={!pubId}
          title={linked ? `Jump to source: ${linked.title || linked.publication_id}` : "Citation has no on-screen source"}
        >
          {p.value}
        </button>
      );
    });

  const verified = [...leadParts, ...restParts].filter((p) => p.kind === "cite").length;
  const total = verified + response.unverified_citations.length;
  const groundingPct = total > 0 ? Math.round((verified / total) * 100) : 100;

  return (
    <article className="answer-card" aria-labelledby="answer-title">
      <div className="answer-head">
        <h2 id="answer-title" ref={titleRef} tabIndex={-1} className="answer-heading">
          Grounded answer
        </h2>
        <RouteBadge route={response.route} fallback={response.answered_via_fallback} />
        <span className="conf-note">
          {statusLabel(response, live)} · {response.sources.length} source{response.sources.length === 1 ? "" : "s"} ·{" "}
          {response.evidence_objects.length} evidence object{response.evidence_objects.length === 1 ? "" : "s"}
        </span>
      </div>
      <div className="answer-body">
        <p className="answer-lead answer-stage answer-stage-1">
          {renderParts(leadParts, "lead")}
        </p>
        {rest && <div className="answer-rest answer-stage answer-stage-2"><p>{renderParts(restParts, "rest")}</p></div>}
        <div className="ev-pointer answer-stage answer-stage-3">
          <ShieldCheck size={14} aria-hidden className="icon-muted" />
          <span>
            <span className="mono">{response.evidence_objects.length} evidence</span> support this conclusion — inspect
            values on the right.
          </span>
        </div>
      </div>
      {(response.filters_ignored.length > 0 || response.unverified_citations.length > 0) && (
        <div className="warn-strip" role="note">
          <TriangleAlert size={15} aria-hidden className="icon-muted" />
          <span>
            {response.filters_ignored.length > 0 && <>Filters ignored: {response.filters_ignored.join(", ")}. </>}
            {response.unverified_citations.length > 0 && (
              <>
                {response.unverified_citations.length} citation{response.unverified_citations.length === 1 ? "" : "s"}{" "}
                stripped by CitationVerifier and listed under debug.
              </>
            )}
          </span>
        </div>
      )}
      <div className="answer-foot answer-foot-ground">
        <span className="mono">
          Grounding: {groundingPct}% verified against Scopus DOIs
        </span>
        <span aria-hidden>·</span>
        <span className="mono">{response.sources.length} primary nodes cited</span>
        <span aria-hidden>·</span>
        <span className="mono">request {response.request_id ? `${shortId(response.request_id)}…` : "—"}</span>
      </div>
    </article>
  );
}
