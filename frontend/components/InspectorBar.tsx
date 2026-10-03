"use client";

import { X } from "lucide-react";
import type { AskResponse } from "../lib/api";
import { formatMs, shortId } from "../lib/format";

interface InspectorBarProps {
  open: boolean;
  onClose: () => void;
  response: AskResponse | null;
  live: boolean;
  activeQuestion: string;
}

/**
 * Collapsible provenance drawer from the HTML mockup (the "Provenance Info"
 * toggle in the top bar). Values are derived from the live AskResponse —
 * route, source types, request hash, latency, mean evidence confidence —
 * never hardcoded demo numbers.
 */
export function InspectorBar({ open, onClose, response, live, activeQuestion }: InspectorBarProps) {
  if (!open) return null;

  const route = response?.route ?? "—";
  const retrieval = response
    ? Array.from(new Set(response.sources.map((s) => s.source_type))).join(" + ") || response.route
    : "idle — no retrieval yet";
  const hash = response?.request_id ? `req-${shortId(response.request_id)}` : "no-request";
  const latency = response?.debug?.latency_breakdown_ms?.total_ms;
  const evs = response?.evidence_objects ?? [];
  const confs = evs.map((e) => e.confidence).filter((c) => Number.isFinite(c));
  const grounding = confs.length ? Math.round((confs.reduce((a, c) => a + c, 0) / confs.length) * 1000) / 10 : null;

  return (
    <div className="inspector-bar" role="status" aria-label="Provenance details">
      <div className="inspector-inner">
        <div className="inspector-group">
          <span className="inspector-title">Provenance Node:</span>
          <span>
            Index: <strong>Scopus prototype (Silver &amp; Gold)</strong>
          </span>
          <span aria-hidden>•</span>
          <span>
            Retrieval: <strong className="mono">{retrieval}</strong>
          </span>
          <span aria-hidden>•</span>
          <span>
            Request: <strong className="mono">{hash}</strong>
          </span>
        </div>
        <div className="inspector-group">
          <span>
            Latency: <strong className="mono">{formatMs(latency, 0)}</strong>
          </span>
          <span>
            Grounding:{" "}
            <strong className="mono inspector-ground">
              {grounding !== null ? `${grounding.toFixed(1)}%` : "—"}
            </strong>
          </span>
          <span className="mono inspector-live" data-live={live} title={activeQuestion || undefined}>
            {live ? "live" : "snapshot"}
            {activeQuestion ? ` · “${activeQuestion.slice(0, 48)}${activeQuestion.length > 48 ? "…" : ""}”` : ""}
          </span>
          <span className="mono">[{route}]</span>
          <button type="button" className="inspector-close" onClick={onClose} aria-label="Hide provenance details">
            <X size={14} />
          </button>
        </div>
      </div>
    </div>
  );
}
