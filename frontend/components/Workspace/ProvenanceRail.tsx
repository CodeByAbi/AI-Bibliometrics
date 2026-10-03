"use client";

import { ChevronDown, FileText, ShieldCheck } from "lucide-react";
import type { AskResponse } from "../../lib/api";
import { EvidenceCard } from "./EvidenceCard";
import { SourceCard } from "./SourceCard";

export interface ProvenanceRailProps {
  view: string;
  response: AskResponse | null;
  highlightId: string | null;
  sourcesOpen: boolean;
  expandedEv: Set<number>;
  copiedDoi: string | null;
  onToggleSources: () => void;
  onToggleEv: (i: number) => void;
  onOpenPublication: (pubId: string) => void;
  onCopy: (doiOrId: string) => void;
  onHighlightEvidence: (pubId: string) => void;
  onHighlightSource: (pubId: string) => void;
}

const EMPTY_NOTES: Record<string, string> = {
  clarify: "Evidence unlocks after entity disambiguation.",
  notfound: "No evidence objects — the gate stopped before synthesis.",
};

/** Placeholder cards shown while retrieval is in flight — skeletons read as
 *  content-shaped progress where a spinner would only announce "something". */
function RailSkeleton({ rows }: { rows: number }) {
  return (
    <div className="rail-skeleton" aria-hidden>
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="skel-card">
          <span className="skel-line skel-w-sm" />
          <span className="skel-line skel-w-lg" />
          <span className="skel-line skel-w-md" />
        </div>
      ))}
    </div>
  );
}

/**
 * The provenance rail: verified evidence above, bibliography below. Both
 * sections always render — including their explanatory empty states — so the
 * structure a screen-reader user learns once stays true in every view.
 */
export function ProvenanceRail({
  view,
  response,
  highlightId,
  sourcesOpen,
  expandedEv,
  copiedDoi,
  onToggleSources,
  onToggleEv,
  onOpenPublication,
  onCopy,
  onHighlightEvidence,
  onHighlightSource,
}: ProvenanceRailProps) {
  const evidence = response?.evidence_objects ?? [];
  const sources = response?.sources ?? [];
  const loading = view === "loading";

  return (
    <div className="rail">
      <section className="rail-section" aria-labelledby="ev-h">
        <h2 className="rail-head" id="ev-h">
          <ShieldCheck size={14} aria-hidden className="icon-muted" /> Verified Evidence
          <span className="count">{evidence.length}</span>
        </h2>
        <div className="rail-body">
          {loading ? (
            <RailSkeleton rows={3} />
          ) : evidence.length === 0 ? (
            <p className="rail-empty">
              {EMPTY_NOTES[view] ??
                "Evidence objects appear here with metric, value, period, and confidence."}
            </p>
          ) : (
            evidence.map((ev, i) => (
              <EvidenceCard
                key={`${ev.metric}-${i}-${response?.request_id ?? "fixture"}`}
                ev={ev}
                index={i}
                expanded={expandedEv.has(i)}
                highlighted={ev.sources.some((s) => s.publication_id === highlightId)}
                onToggle={onToggleEv}
                onSourceClick={onHighlightSource}
              />
            ))
          )}
        </div>
      </section>

      <section className="rail-section" aria-labelledby="src-h">
        <h2 className="rail-head" id="src-h">
          <FileText size={14} aria-hidden className="icon-muted" /> Sources
          <span className="count">{sources.length}</span>
        </h2>
        <div className="rail-body">
          {loading ? (
            <RailSkeleton rows={2} />
          ) : sources.length === 0 ? (
            <p className="rail-empty">
              Linked publications with title, year, DOI, relevance, and source type appear here.
            </p>
          ) : (
            <>
              <button
                type="button"
                className="chip-state disclosure-btn align-start"
                aria-expanded={sourcesOpen}
                onClick={onToggleSources}
              >
                {sourcesOpen ? "Collapse" : "Expand"} {sources.length} source{sources.length === 1 ? "" : "s"}
                <ChevronDown size={13} aria-hidden />
              </button>
              <div className="collapsible" data-collapsed={!sourcesOpen}>
                <div className="collapsible-inner">
                  {sources.map((s, i) => (
                    <SourceCard
                      key={s.publication_id}
                      source={s}
                      highlighted={highlightId === s.publication_id}
                      copied={copiedDoi === (s.doi ?? s.publication_id)}
                      animateDelay={sourcesOpen ? Math.min(i * 50, 180) / 1000 : 0}
                      onOpen={onOpenPublication}
                      onCopy={onCopy}
                      onEvidence={onHighlightEvidence}
                    />
                  ))}
                </div>
              </div>
            </>
          )}
        </div>
      </section>
    </div>
  );
}