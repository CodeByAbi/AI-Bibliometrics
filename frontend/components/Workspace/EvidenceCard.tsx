"use client";

import { memo } from "react";
import { ChevronDown } from "lucide-react";
import type { EvidenceObject } from "../../lib/api";
import { formatValue } from "../../lib/format";
import { ConfidenceMeter } from "../CountUp";
import { Reveal } from "../Reveal";

export interface EvidenceCardProps {
  ev: EvidenceObject;
  index: number;
  expanded: boolean;
  highlighted: boolean;
  onToggle: (i: number) => void;
  onSourceClick: (pubId: string) => void;
}

/**
 * One verified evidence object: claim, metric, value, period, confidence, and
 * an expandable provenance trail. Memoized on its own slice of state so a
 * highlight, copy, or expand toggle re-renders only the affected card and not
 * the other 49 — handlers arrive as stable callbacks from the workspace hook.
 */
export const EvidenceCard = memo(function EvidenceCard({
  ev,
  index,
  expanded,
  highlighted,
  onToggle,
  onSourceClick,
}: EvidenceCardProps) {
  const sourceIds = ev.sources.map((s) => s.publication_id);

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
        <div>
          <dt>Metric</dt>
          <dd className="mono">{ev.metric}</dd>
        </div>
        <div>
          <dt>Value</dt>
          <dd className="mono">{formatValue(ev.value)}</dd>
        </div>
        <div>
          <dt>Period</dt>
          <dd className="mono">{ev.period}</dd>
        </div>
        <ConfidenceMeter value={ev.confidence} />
      </dl>
      <div className="ev-detail" id={`ev-detail-${index}`} data-open={expanded}>
        <div className="ev-detail-inner">
          <p className="ev-provenance">
            Traced to {ev.sources.length} source{ev.sources.length === 1 ? "" : "s"}:{" "}
            {ev.sources.map((s) => s.title ?? s.publication_id).join(" · ")}
            <br />
            <span className="mono">{sourceIds.join(", ")}</span>
          </p>
        </div>
      </div>
      {ev.sources.length > 0 && (
        <div className="ev-links ev-links-row">
          {ev.sources.map((s) => (
            <button
              key={s.publication_id}
              type="button"
              className="ev-link"
              title={s.title ?? s.publication_id}
              onClick={() => onSourceClick(s.publication_id)}
            >
              <span className="sr-only">Show source </span>
              {s.publication_id}
            </button>
          ))}
        </div>
      )}
    </Reveal>
  );
});