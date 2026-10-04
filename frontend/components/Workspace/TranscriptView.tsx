"use client";

/**
 * Restored conversation transcript.
 *
 * Docs Reference: docs/06 Api Design.md §6.2.
 *
 * WHY THIS IS A PURE READ
 * ========================
 * Every turn here came from `GET /api/v1/sessions/{id}`. Nothing in this file
 * calls `/api/v1/ask`, routes a query, retrieves, or synthesises — reopening an
 * old session must never re-run the RAG pipeline. The evidence and source cards
 * are rendered from the per-turn snapshot that was stored with the answer
 * (migration 006), so they show what was actually shown at the time.
 *
 * WHAT THE SNAPSHOT IS NOT
 * ========================
 * A snapshot is a receipt, not a fact. Every number below was true of the corpus
 * when the answer was produced and may not be now. So each restored turn is
 * labelled with its `request_id`, which is the key for re-verifying it against
 * the live corpus, and the header states plainly that this is history.
 *
 * A `failed` turn is rendered explicitly rather than hidden. It records a real
 * event — retrieval or synthesis errored — and silently dropping it would make a
 * conversation look complete when it was not.
 */

import { useMemo, useState } from "react";
import { AlertTriangle, ChevronDown, History } from "lucide-react";

import { EvidenceCard } from "./EvidenceCard";
import { SourceCard } from "./SourceCard";
import type { SessionMessage } from "@/lib/sessions";
import type { EvidenceObject, SourceItem } from "@/lib/api";

function fmtTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** Sidebar-style meta line: source count and route, or the honest alternative. */
function metaFor(m: SessionMessage): string {
  const bits: string[] = [];
  const n = m.sources?.length ?? 0;
  bits.push(n === 1 ? "1 source" : `${n} sources`);
  if (m.route) bits.push(m.route);
  return bits.join(" · ");
}

function Turn({
  message,
  defaultOpen,
}: {
  message: SessionMessage;
  defaultOpen: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const evidence = (message.evidence_objects ?? []) as EvidenceObject[];
  const sources = (message.sources ?? []) as SourceItem[];

  if (message.role === "user") {
    return (
      <li className="tr-turn tr-user">
        <p className="tr-q">{message.content}</p>
        <p className="tr-meta mono">{fmtTime(message.created_at)}</p>
      </li>
    );
  }

  return (
    <li className="tr-turn tr-assistant">
      <button
        type="button"
        className="tr-head"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
      >
        <ChevronDown
          size={14}
          strokeWidth={2.4}
          aria-hidden
          className={open ? "tr-chev open" : "tr-chev"}
        />
        <span className="tr-status">
          {message.status === "failed" ? (
            <>
              <AlertTriangle size={12} strokeWidth={2.4} aria-hidden /> failed
            </>
          ) : (
            metaFor(message)
          )}
        </span>
        <span className="tr-meta mono">{fmtTime(message.created_at)}</span>
      </button>

      {open && (
        <div className="tr-body">
          <p className="tr-answer">{message.content}</p>

          {message.status === "failed" && (
            <p className="tr-failed">
              This turn failed before an answer was produced. Nothing was
              fabricated in its place.
            </p>
          )}

          {evidence.length > 0 && (
            <ul className="tr-ev">
              {evidence.map((ev, i) => (
                <li key={`${message.id}-ev-${i}`}>
                  <EvidenceCard
                    ev={ev}
                    index={i}
                    expanded={false}
                    highlighted={false}
                    onToggle={() => undefined}
                    onSourceClick={() => undefined}
                  />
                </li>
              ))}
            </ul>
          )}

          {sources.length > 0 && (
            <ul className="tr-src">
              {sources.map((s, i) => (
                <li key={`${message.id}-src-${s.publication_id ?? i}`}>
                  <SourceCard
                    source={s}
                    highlighted={false}
                    copied={false}
                    animateDelay={0}
                    onOpen={() => undefined}
                    onCopy={() => undefined}
                    onEvidence={() => undefined}
                  />
                </li>
              ))}
            </ul>
          )}

          {message.request_id && (
            <p className="tr-reverify mono">
              Snapshot of a past answer · request {message.request_id.slice(0, 8)} ·
              re-ask to re-verify against the live corpus
            </p>
          )}
        </div>
      )}
    </li>
  );
}

export function TranscriptView({
  messages,
  loading,
}: {
  messages: SessionMessage[];
  loading: boolean;
}) {
  const hasAny = messages.length > 0;
  const lastAssistantIdx = useMemo(() => {
    for (let i = messages.length - 1; i >= 0; i -= 1) {
      if (messages[i].role === "assistant") return i;
    }
    return -1;
  }, [messages]);

  if (loading) {
    return (
      <section className="transcript" aria-busy="true">
        <p className="tr-empty">Loading conversation…</p>
      </section>
    );
  }
  if (!hasAny) {
    return null;
  }

  return (
    <section className="transcript" aria-label="Conversation history">
      <header className="tr-header">
        <History size={13} strokeWidth={2.2} aria-hidden />
        <span>Conversation history</span>
        <span className="mono tr-count">
          {messages.filter((m) => m.role === "user").length} question
          {messages.filter((m) => m.role === "user").length === 1 ? "" : "s"}
        </span>
      </header>
      <ol className="tr-list">
        {messages.map((m, i) => (
          <Turn
            key={m.id}
            message={m}
            defaultOpen={m.role === "assistant" && i === lastAssistantIdx}
          />
        ))}
      </ol>
    </section>
  );
}