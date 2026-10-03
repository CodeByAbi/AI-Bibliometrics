"use client";

import { memo } from "react";
import { Check, Copy, ExternalLink, ShieldCheck } from "lucide-react";
import type { SourceItem } from "../../lib/api";
import { doiHref } from "../../lib/format";
import { Reveal } from "../Reveal";

export interface SourceCardProps {
  source: SourceItem;
  highlighted: boolean;
  copied: boolean;
  animateDelay: number;
  onOpen: (pubId: string) => void;
  onCopy: (doiOrId: string) => void;
  onEvidence: (pubId: string) => void;
}

/**
 * One bibliography record. The title button is the single entry point to the
 * paper-detail view — no clickable wrapper div — so keyboard and pointer
 * share one path and the interactive count stays honest for screen readers.
 */
export const SourceCard = memo(function SourceCard({
  source,
  highlighted,
  copied,
  animateDelay,
  onOpen,
  onCopy,
  onEvidence,
}: SourceCardProps) {
  const href = doiHref(source.doi);
  const copyLabel = copied ? "Copied" : href ? "DOI" : "ID";

  return (
    <Reveal
      as="article"
      id={`src-${source.publication_id}`}
      className="src-item"
      data-highlight={highlighted}
      delay={animateDelay}
    >
      <h3 className="src-head">
        <button
          type="button"
          className="src-title-btn"
          onClick={() => onOpen(source.publication_id)}
          title={`Open provenance detail for ${source.title || source.publication_id}`}
        >
          {source.title || source.publication_id}
        </button>
      </h3>
      <div className="src-meta">
        <span className="src-type" data-t={source.source_type}>
          {source.source_type}
        </span>
        <span className="mono">{source.year ?? "—"}</span>
        {href ? (
          <a className="src-doi src-ext" href={href} target="_blank" rel="noreferrer">
            DOI:{source.doi} <ExternalLink size={11} aria-hidden />
          </a>
        ) : (
          <span className="mono">no-doi</span>
        )}
      </div>

      <div className="src-rel">
        {typeof source.relevance_score === "number" && (
          <>
            <div
              className="src-rel-track"
              role="progressbar"
              aria-label="Relevance to the question"
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={Math.round(source.relevance_score * 100)}
              aria-valuetext={`${source.relevance_score.toFixed(2)} of 1.00`}
            >
              <div className="src-rel-fill" style={{ width: `${Math.round(source.relevance_score * 100)}%` }} />
            </div>
            <span className="src-rel-val">{source.relevance_score.toFixed(2)}</span>
          </>
        )}
        <span className="src-actions">
          <button
            type="button"
            className={`src-act${copied ? " copy-ok" : ""}`}
            onClick={() => onCopy(source.doi ?? source.publication_id)}
          >
            {copied ? <Check size={11} aria-hidden /> : <Copy size={11} aria-hidden />}
            {copyLabel}
            <span className="sr-only">
              {copied
                ? ` — copied ${href ? "DOI link" : "publication ID"}`
                : ` — copy ${href ? "DOI link" : "publication ID"} for ${source.title || source.publication_id}`}
            </span>
          </button>
          {href && (
            <a className="src-act src-ext" href={href} target="_blank" rel="noreferrer">
              <ExternalLink size={11} aria-hidden /> Open
              <span className="sr-only"> {source.title || source.publication_id} in a new tab</span>
            </a>
          )}
          <button
            type="button"
            className="src-act"
            onClick={() => onEvidence(source.publication_id)}
          >
            <ShieldCheck size={11} aria-hidden /> Evidence
            <span className="sr-only"> traced to {source.title || source.publication_id}</span>
          </button>
        </span>
      </div>

      {source.provenance && (
        <p className="mono src-provenance">
          {source.provenance} · {source.publication_id}
        </p>
      )}
    </Reveal>
  );
});