"use client";

import type { AskResponse } from "../../lib/api";
import { SEEDS } from "../../lib/api";
import type { TitleRef } from "./types";

export interface NotFoundPanelProps {
  response: AskResponse | null;
  activeQuestion: string;
  onTrySeed: (question: string) => void;
  onLoadExample: () => void;
  titleRef: TitleRef;
}

/**
 * Deterministic zero-state. This is a valid outcome, not a failure: the
 * evidence gate admitted zero records and synthesis was skipped, so the panel
 * states what was searched, why it stopped, and offers grounded retries
 * instead of an invented answer.
 */
export function NotFoundPanel({
  response,
  activeQuestion,
  onTrySeed,
  onLoadExample,
  titleRef,
}: NotFoundPanelProps) {
  if (response?.status !== "not_found") {
    return (
      <section className="notfound reveal" aria-labelledby="nf-title">
        <h2 id="nf-title" ref={titleRef} tabIndex={-1}>
          No zero-state in this answer set
        </h2>
        <p>
          The current result is grounded. To inspect the deterministic zero-state, load the corpus example that matches
          nothing.
        </p>
        <div className="nf-actions">
          <button type="button" className="seed" onClick={onLoadExample}>
            Load corpus example
          </button>
        </div>
      </section>
    );
  }

  return (
    <section className="notfound reveal" aria-labelledby="nf-title">
      <h2 id="nf-title" ref={titleRef} tabIndex={-1}>
        No supporting evidence found
      </h2>
      <p>{response.answer}</p>
      <div className="nf-grid">
        <div className="nf-cell">
          <h3>What was searched</h3>
          <p title={activeQuestion}>
            “{activeQuestion.length > 140 ? `${activeQuestion.slice(0, 140)}…` : activeQuestion}” via{" "}
            <span className="mono">[{response.route}]</span> over Scopus publications, embedded chunks, and
            collaboration edges.
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
        <button type="button" className="seed" onClick={() => onTrySeed(SEEDS[0].question)}>
          Try: {SEEDS[0].label}
        </button>
        <button type="button" className="seed" onClick={() => onTrySeed(SEEDS[1].question)}>
          Try: {SEEDS[1].label}
        </button>
      </div>
      <p className="mono">status: not_found · route: {response.route} · short-circuit, no LLM call</p>
    </section>
  );
}