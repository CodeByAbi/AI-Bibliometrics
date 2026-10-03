"use client";

import type { Ref } from "react";
import { ArrowLeft } from "lucide-react";
import { doiHref, formatValue } from "../lib/format";
import type { EvidenceObject, SourceItem } from "../lib/api";

interface PublicationDetailViewProps {
  publication: SourceItem | null;
  evidence: EvidenceObject[];
  onBack: () => void;
  titleRef: Ref<HTMLHeadingElement>;
}

/**
 * Publication provenance detail (HTML mockup §8). Metadata is restricted to
 * what the API returns (year, DOI, source type, relevance, provenance);
 * the narrative is the set of evidence claims traced to this record —
 * never an invented abstract.
 */
export function PublicationDetailView({ publication, evidence, onBack, titleRef }: PublicationDetailViewProps) {
  if (!publication) {
    return (
      <section className="panel-card" aria-labelledby="pub-empty-title">
        <h2 id="pub-empty-title" ref={titleRef} tabIndex={-1}>
          No publication selected
        </h2>
        <p className="sub">Answer a question first — then open any bibliography card to inspect its provenance.</p>
        <div className="resolve-row">
          <button type="button" className="ask-btn btn-resolve" onClick={onBack}>
            <ArrowLeft size={15} aria-hidden /> Return to Brief
          </button>
        </div>
      </section>
    );
  }

  const href = doiHref(publication.doi);

  return (
    <div className="pub" aria-labelledby="pub-title">
      <div className="panel-card pub-card">
        <div className="pub-top">
          <span className="verified-pill">Corpus Verified Record</span>
          <span className="mono pub-year">{publication.year ?? "year unknown"}</span>
        </div>
        <h1 id="pub-title" ref={titleRef} tabIndex={-1} className="pub-title">
          {publication.title}
        </h1>
        <p className="mono pub-idline">
          {publication.publication_id}
          {publication.provenance ? ` · ${publication.provenance}` : ""}
        </p>

        <div className="metric-row mono pub-metrics">
          <div className="metric-cell">
            <span className="metric-label">Relevance</span>
            <span className="metric-value">
              {typeof publication.relevance_score === "number" ? publication.relevance_score.toFixed(2) : "—"}
            </span>
            <span className="metric-sub">Answer-set match</span>
          </div>
          <div className="metric-cell">
            <span className="metric-label">Source Type</span>
            <span className="metric-value sm">{publication.source_type}</span>
            <span className="metric-sub">Retrieval layer</span>
          </div>
          <div className="metric-cell">
            <span className="metric-label">Linked Evidence</span>
            <span className="metric-value">{evidence.length}</span>
            <span className="metric-sub">Objects traced here</span>
          </div>
          <div className="metric-cell pub-doi-cell">
            <span className="metric-label">Permanent DOI</span>
            {href ? (
              <a className="metric-value sm metric-link" href={href} target="_blank" rel="noreferrer">
                {publication.doi}
              </a>
            ) : (
              <span className="metric-value sm">no-doi</span>
            )}
            <span className="metric-sub">{href ? "DOI resolver" : "No DOI registered"}</span>
          </div>
        </div>

        <div className="pub-summary">
          <h3 className="pub-h">Grounded Summary</h3>
          {evidence.length === 0 ? (
            <p className="pub-empty">
              No evidence objects trace to this record in the current answer set. It was retrieved as context, not as
              the basis of a measured claim.
            </p>
          ) : (
            <ul className="pub-claims">
              {evidence.map((ev, i) => (
                <li key={`${ev.metric}-${i}`}>
                  <span className="mono pub-claim-metric">
                    {ev.metric} · {formatValue(ev.value)} · {ev.period} · {Math.round(ev.confidence * 100)}%
                  </span>
                  <span className="pub-claim-text">{ev.claim}</span>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="pub-foot mono">
          <span>
            {publication.publication_id} · {publication.source_type}
          </span>
          <button type="button" className="pub-back" onClick={onBack}>
            <ArrowLeft size={13} aria-hidden />
            <span>Return to Brief</span>
          </button>
        </div>
      </div>
    </div>
  );
}
