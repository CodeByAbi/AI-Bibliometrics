"use client";

import { useEffect, useId, useState } from "react";
import { ArrowRight, CalendarDays, Database, Gauge, Plus } from "lucide-react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { motionTokens, springs } from "../lib/motion-tokens";
import { API_BASE } from "../lib/api";

export const HERO_PERIODS: Array<{ label: string; filters: Record<string, number> }> = [
  { label: "all-time", filters: {} },
  { label: "2020 – 2025", filters: { year_from: 2020, year_to: 2025 } },
  { label: "2021 – 2023", filters: { year_from: 2021, year_to: 2023 } },
];

const MIN_QUESTION_LENGTH = 3;
const MAX_QUESTION_LENGTH = 1000;

interface ResearchHeroProps {
  /** Last submitted question — seeds the draft; typing never writes back. */
  question: string;
  onSynthesize: (q: string) => void;
  loading: boolean;
  periodIdx: number;
  onCyclePeriod: () => void;
  entityFilterLabel: string | null;
  onAddFilter: () => void;
  live: boolean;
  devMode: boolean;
}

/**
 * Research question as visual centerpiece: headline-scale textarea, quiet
 * filter chips, signal-blue Synthesize action. The period chip is a functional
 * year_from/year_to filter; the cosine-gate chip reports the real VectorRoute
 * admission threshold. Keystroke state lives here so typing re-renders only
 * the hero, not the workspace tree.
 */
export function ResearchHero({
  question,
  onSynthesize,
  loading,
  periodIdx,
  onCyclePeriod,
  entityFilterLabel,
  onAddFilter,
  live,
  devMode,
}: ResearchHeroProps) {
  const period = HERO_PERIODS[periodIdx] ?? HERO_PERIODS[0]!;
  const reduceMotion = useReducedMotion();
  const hintId = useId();
  const [draft, setDraft] = useState(question);
  useEffect(() => setDraft(question), [question]);

  const trimmed = draft.trim();
  const tooShort = trimmed.length < MIN_QUESTION_LENGTH;
  const submitDisabled = loading || tooShort;
  const remaining = MAX_QUESTION_LENGTH - draft.length;

  return (
    <section className="hero" aria-labelledby="hero-label">
      <div className="hero-top">
        <span id="hero-label" className="hero-kicker">
          Research Inquiry
        </span>
        <span className="mono hero-corpus">Scopus Grounded Corpus</span>
      </div>

      <form
        onSubmit={(e) => {
          e.preventDefault();
          onSynthesize(draft);
        }}
      >
        <label htmlFor="hero-input" className="sr-only">
          Research question
        </label>
        <textarea
          id="hero-input"
          className="hero-input"
          rows={2}
          maxLength={MAX_QUESTION_LENGTH}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
              e.preventDefault();
              if (!submitDisabled) onSynthesize(draft);
            }
          }}
          placeholder="Ask an academic or bibliometric inquiry across Scopus…"
          aria-describedby={hintId}
          aria-invalid={trimmed.length > 0 && tooShort ? true : undefined}
          aria-busy={loading}
        />

        <div className="hero-row">
          <div className="hero-chips" role="group" aria-label="Retrieval scope">
            <button
              type="button"
              className="hero-chip"
              onClick={onCyclePeriod}
              aria-label={`Publication year scope: ${period.label}. Activate to change.`}
              title="Cycle the publication-year filter (year_from / year_to sent to the API)"
            >
              <CalendarDays size={13} aria-hidden />
              <span className="mono">{period.label}</span>
            </button>
            <span className="hero-chip hero-chip-static">
              <Database size={13} aria-hidden />
              <span className="mono">Scopus Core</span>
            </span>
            <span
              className="hero-chip hero-chip-static"
              title="VectorRoute admission gate — chunks below this cosine never reach synthesis"
            >
              <Gauge size={13} aria-hidden />
              <span className="mono">Cosine gate ≥ 0.48</span>
            </span>
            {entityFilterLabel && (
              <span className="hero-chip hero-chip-entity">
                <span className="mono">{entityFilterLabel}</span>
              </span>
            )}
            <button
              type="button"
              className="hero-add"
              onClick={onAddFilter}
              title="Resolve an entity filter via disambiguation"
            >
              <Plus size={14} aria-hidden />
              <span>Add filter</span>
            </button>
          </div>

          <motion.button
            type="submit"
            className="ask-btn hero-synthesize"
            disabled={submitDisabled}
            aria-describedby={hintId}
            whileTap={reduceMotion ? undefined : { scale: motionTokens.scale.press }}
            transition={springs.snappy}
          >
            <AnimatePresence mode="wait">
              {loading ? (
                <motion.span
                  key="loading"
                  initial={{ opacity: 0, filter: "blur(2px)" }}
                  animate={{ opacity: 1, filter: "blur(0px)" }}
                  exit={{ opacity: 0, filter: "blur(2px)", transition: { duration: motionTokens.duration.exit } }}
                  transition={{ duration: motionTokens.duration.fast }}
                >
                  Retrieving…
                </motion.span>
              ) : (
                <motion.span
                  key="label"
                  initial={{ opacity: 0, filter: "blur(2px)" }}
                  animate={{ opacity: 1, filter: "blur(0px)" }}
                  exit={{ opacity: 0, filter: "blur(2px)", transition: { duration: motionTokens.duration.exit } }}
                  transition={{ duration: motionTokens.duration.fast }}
                >
                  <span>Synthesize</span>
                  <ArrowRight size={15} aria-hidden />
                </motion.span>
              )}
            </AnimatePresence>
          </motion.button>
        </div>
      </form>

      {/* Describes both the field and the submit button: why it is disabled,
          what it costs, and where the answer comes from. */}
      <div className="ask-meta">
        <span id={hintId}>
          {tooShort && !loading
            ? `Enter at least ${MIN_QUESTION_LENGTH} characters — or press ⌘K to jump back to the question box.`
            : live
              ? "Verified against the live database."
              : "Answers trace to database evidence — no invention."}
        </span>
        <span className="live-dot" data-live={live}>
          <i aria-hidden /> {live ? "Verified against live database" : "Database-grounded corpus"}
        </span>
        {devMode && (
          <span className="mono" aria-label={`API endpoint ${API_BASE}/api/v1/ask`}>
            POST {API_BASE.replace("http://", "").replace("https://", "")}/api/v1/ask
          </span>
        )}
        <span className="mono hero-count" aria-hidden={!tooShort}>
          {remaining}
        </span>
      </div>
    </section>
  );
}