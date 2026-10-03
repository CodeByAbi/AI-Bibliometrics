"use client";

import { useMemo, useRef } from "react";
import type { Ref } from "react";
import { Download } from "lucide-react";
import type { AskResponse } from "../lib/api";
import { formatValue } from "../lib/format";
import { typeDistribution, yearHistogram } from "../lib/views";

interface ExploreViewProps {
  response: AskResponse | null;
  onOpenPublication: (pubId: string) => void;
  titleRef: Ref<HTMLHeadingElement>;
}

function points(values: number[], w: number, h: number, pad: number): string {
  if (!values.length) return "";
  const max = Math.max(...values, 1);
  const min = Math.min(...values, 0);
  const span = max - min || 1;
  const step = values.length > 1 ? (w - pad * 2) / (values.length - 1) : 0;
  return values
    .map((v, i) => {
      const x = pad + i * step;
      const y = h - pad - ((v - min) / span) * (h - pad * 2);
      return `${i === 0 ? "M" : "L"} ${x.toFixed(1)} ${y.toFixed(1)}`;
    })
    .join(" ");
}

/**
 * Bibliometric trajectories + emerging clusters (HTML mockup §6). The chart
 * plots this answer set's sources per year (solid) against mean relevance
 * per year (dashed); cluster cards are the answer's own evidence objects —
 * no invented growth rates.
 */
export function ExploreView({ response, onOpenPublication, titleRef }: ExploreViewProps) {
  const svgRef = useRef<SVGSVGElement | null>(null);
  const hist = useMemo(() => yearHistogram(response), [response]);
  const dist = useMemo(() => typeDistribution(response), [response]);
  const clusters = useMemo(() => response?.evidence_objects ?? [], [response]);

  const meanRel = useMemo(() => {
    const pool = response?.sources ?? [];
    return hist.map(({ year }) => {
      const scores = pool.filter((s) => s.year === year && typeof s.relevance_score === "number").map((s) => s.relevance_score as number);
      return scores.length ? scores.reduce((a, b) => a + b, 0) / scores.length : 0;
    });
  }, [hist, response]);

  const W = 800;
  const H = 180;
  const PAD = 8;
  const volPath = points(hist.map((h) => h.count), W, H, PAD);
  const relPath = points(meanRel, W, H, PAD);
  const lastX = W - PAD;
  const lastY = useMemo(() => {
    const v = hist.map((h) => h.count);
    if (!v.length) return H - PAD;
    const max = Math.max(...v, 1);
    const min = Math.min(...v, 0);
    const last = v[v.length - 1] ?? 0;
    return H - PAD - ((last - min) / (max - min || 1)) * (H - PAD * 2);
  }, [hist]);

  const yearRange = hist.length ? `${hist[0]?.year}–${hist[hist.length - 1]?.year}` : "—";
  const total = hist.reduce((a, h) => a + h.count, 0);

  const exportSvg = () => {
    const node = svgRef.current;
    if (!node) return;
    const blob = new Blob([new XMLSerializer().serializeToString(node)], { type: "image/svg+xml" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "bibliometric-trajectory.svg";
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="explore" aria-labelledby="explore-title">
      <div className="explore-head">
        <div>
          <h2 id="explore-title" ref={titleRef} tabIndex={-1} className="explore-title">
            Bibliometric Trajectories &amp; Emerging Clusters
          </h2>
          <p className="explore-sub">
            Cross-analyzing {total} cited publication{total === 1 ? "" : "s"} in this answer set
            {hist.length ? ` · indexed ${yearRange}` : " · run a question to populate"}
          </p>
        </div>
        <button type="button" className="chip-state" onClick={exportSvg} disabled={!hist.length}>
          <Download size={13} aria-hidden /> <span className="mono">Export SVG</span>
        </button>
      </div>

      <div className="panel-card explore-chart">
        <div className="explore-legend mono">
          <span className="explore-legend-item">
            <span className="explore-swatch explore-swatch-vol" aria-hidden /> Publication Volume
          </span>
          <span className="explore-legend-item">
            <span className="explore-swatch explore-swatch-rel" aria-hidden /> Mean Relevance
          </span>
          <span className="explore-legend-src">Answer-set sources</span>
        </div>
        {hist.length > 1 ? (
          <div className="explore-plot">
            <svg ref={svgRef} className="explore-svg" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" role="img" aria-label={`Publications per year from ${yearRange}`}>
              {[180, 135, 90, 45].map((y) => (
                <line key={y} x1="0" x2={W} y1={y} y2={y} stroke="#E9E9E7" strokeWidth="1" strokeDasharray={y === 180 ? undefined : "2,2"} />
              ))}
              <path d={relPath} fill="none" stroke="#A8A8A2" strokeDasharray="3,3" strokeWidth="1.5" />
              <path d={volPath} fill="none" stroke="#2563EB" strokeWidth="2" />
              <circle cx={lastX} cy={lastY} r="3.5" fill="#2563EB" />
            </svg>
            <div className="explore-axis mono">
              {hist.map((h) => (
                <span key={h.year}>
                  {h.year} ({h.count})
                </span>
              ))}
            </div>
          </div>
        ) : (
          <p className="rail-empty">
            {hist.length === 1
              ? `A single indexed year (${hist[0]?.year}) — trajectories need at least two years. Ask a broader question to trace movement.`
              : "No year-indexed sources in this answer set yet."}
          </p>
        )}
        {dist.length > 0 && (
          <div className="explore-dist" aria-label="Source-type distribution">
            <div className="explore-dist-bar">
              {dist.map((d) => (
                <span key={d.type} className={`explore-dist-seg explore-dist-${d.type}`} style={{ width: `${d.share * 100}%` }} title={`${d.type}: ${d.count}`} />
              ))}
            </div>
            <div className="mono explore-dist-labels">
              {dist.map((d) => (
                <span key={d.type}>
                  {d.type} {Math.round(d.share * 100)}%
                </span>
              ))}
            </div>
          </div>
        )}
      </div>

      <div className="explore-grid">
        {clusters.length === 0 && (
          <p className="rail-empty">Emerging clusters appear here as evidence objects once a question is answered.</p>
        )}
        {clusters.map((ev, i) => (
          <article key={`${ev.metric}-${i}`} className="explore-cluster">
            <div className="explore-cluster-top">
              <span className="explore-cluster-tag mono">{ev.metric}</span>
              <span className="mono explore-cluster-conf">{Math.round(ev.confidence * 100)}% conf</span>
            </div>
            <h4 className="explore-cluster-title">{ev.claim}</h4>
            <p className="mono explore-cluster-meta">
              {formatValue(ev.value)} · {ev.period}
            </p>
            {ev.sources[0] && (
              <button type="button" className="explore-cluster-src" onClick={() => onOpenPublication(ev.sources[0]!.publication_id)} title={ev.sources[0].title ?? ev.sources[0].publication_id}>
                {ev.sources[0].title ?? ev.sources[0].publication_id} →
              </button>
            )}
          </article>
        ))}
      </div>
    </div>
  );
}
