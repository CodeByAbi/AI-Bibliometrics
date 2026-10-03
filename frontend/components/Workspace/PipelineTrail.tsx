"use client";

import { Check } from "lucide-react";
import type { WorkspaceView } from "../../lib/views";

const PIPE_LABELS = ["Question", "Retrieval", "Evidence", "Answer"] as const;
type PipeState = "done" | "active" | "idle";

/** Where the pipeline stands for a given view — the visible state also drives
 *  the text an assistive technology reads for each stage. */
export function pipeStates(view: WorkspaceView): PipeState[] {
  if (view === "empty") return ["active", "idle", "idle", "idle"];
  if (view === "loading") return ["done", "active", "idle", "idle"];
  if (view === "clarify") return ["done", "done", "active", "idle"];
  if (view === "notfound") return ["done", "done", "done", "idle"];
  return ["done", "done", "done", "done"];
}

const STATE_TEXT: Record<PipeState, string> = {
  done: "complete",
  active: "in progress",
  idle: "pending",
};

export interface PipelineTrailProps {
  view: WorkspaceView;
  activeQuestion: string;
  route?: string;
}

/**
 * Question → Retrieval → Evidence → Answer trail. Each stage carries its state
 * as text, not only as a filled/hollow dot, so the pipeline is legible without
 * colour perception.
 */
export function PipelineTrail({ view, activeQuestion, route }: PipelineTrailProps) {
  const pipe = pipeStates(view);
  const showQuestion = Boolean(activeQuestion) && view !== "empty";

  return (
    <>
      <ol className="pipeline" aria-label="Research pipeline: question to grounded answer">
        {PIPE_LABELS.map((label, i) => (
          <li key={label} data-state={pipe[i]}>
            {i > 0 && <span className="pipe-bar" aria-hidden />}
            <span className="pipe-dot" aria-hidden>
              {pipe[i] === "done" ? <Check size={11} strokeWidth={3} /> : i + 1}
            </span>
            <span>
              {label}
              <span className="sr-only"> — {STATE_TEXT[pipe[i] ?? "idle"]}</span>
            </span>
          </li>
        ))}
      </ol>
      {showQuestion && (
        <div className="active-q">
          <span className="mono" aria-hidden>
            Q
          </span>
          <span title={activeQuestion}>
            <strong>
              “{activeQuestion.length > 110 ? `${activeQuestion.slice(0, 110)}…` : activeQuestion}”
            </strong>
            {route && <span className="mono"> · [{route}]</span>}
          </span>
        </div>
      )}
    </>
  );
}