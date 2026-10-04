"use client";

import { ArrowRight, Check } from "lucide-react";
import type { AskResponse } from "../../lib/api";
import { plural } from "../../lib/format";
import { RouteBadge } from "../AnswerBrief";
import type { TitleRef } from "./types";

export interface ClarifyPanelProps {
  response: AskResponse | null;
  selectedCand: string | null;
  onSelect: (id: string) => void;
  onResolve: () => void;
  /** P0-A: injects a hardcoded answer. Optional; omitted in the default UI. */
  onLoadExample?: () => void;
  titleRef: TitleRef;
}

/**
 * Entity disambiguation. Retrieval pauses here by design — the gate never
 * guesses between three canonical "Rahman" records, so the panel presents
 * candidates with their affiliations and corpus counts before continuing.
 */
export function ClarifyPanel({
  response,
  selectedCand,
  onSelect,
  onResolve,
  onLoadExample,
  titleRef,
}: ClarifyPanelProps) {
  const candidates = response?.candidates ?? [];

  if (!candidates.length) {
    return (
      <section className="panel-card reveal" aria-labelledby="clarify-title">
        <h2 id="clarify-title" ref={titleRef} tabIndex={-1}>
          No ambiguous entities
        </h2>
        <p className="sub">
          The current answer set needs no disambiguation. Ask an author- or institution-scoped question
          {onLoadExample ? " — or load the corpus disambiguation example" : ""}.
        </p>
        {onLoadExample && (
          <div className="resolve-row">
            <button type="button" className="ask-btn btn-resolve" onClick={onLoadExample}>
              Load corpus example <ArrowRight size={15} aria-hidden />
            </button>
          </div>
        )}
      </section>
    );
  }

  return (
    <section className="panel-card reveal" aria-labelledby="clarify-title">
      <h2 id="clarify-title" ref={titleRef} tabIndex={-1}>
        Needs clarification
      </h2>
      <p className="sub">
        {response && <RouteBadge route={response.route} fallback={response.answered_via_fallback} />} {response?.answer}
      </p>
      <div className="cand-grid" role="group" aria-label="Candidate entities">
        {candidates.map((c) => (
          <button
            key={c.id}
            type="button"
            className="cand"
            aria-pressed={selectedCand === c.id}
            onClick={() => onSelect(c.id)}
          >
            <span className="cand-name">{c.name}</span>
            <span className="cand-aff">{c.affiliation ?? "—"}</span>
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
        <button type="button" className="ask-btn btn-resolve" disabled={!selectedCand} onClick={onResolve}>
          Resolve with selected entity <ArrowRight size={15} aria-hidden />
        </button>
        {!selectedCand && <span className="resolve-hint">Select a card to continue retrieval.</span>}
      </div>
    </section>
  );
}