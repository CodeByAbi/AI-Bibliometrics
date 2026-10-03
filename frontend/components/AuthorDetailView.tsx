"use client";

import { useState } from "react";
import type { Ref } from "react";
import { ArrowRight, Download } from "lucide-react";
import { doiHref } from "../lib/format";
import type { CandidateItem, SourceItem } from "../lib/api";

interface AuthorDetailViewProps {
  author: CandidateItem | null;
  publications: SourceItem[];
  onOpenPublication: (pubId: string) => void;
  onResolve: () => void;
  titleRef: Ref<HTMLHeadingElement>;
}

/** First character of a word, keeping emoji surrogate pairs intact (🦊 Fox → "🦊", not "�"). ES5-safe: no string spread. */
function firstChar(w: string): string {
  const lead = w.charCodeAt(0);
  if (Number.isNaN(lead)) return "";
  return lead >= 0xd800 && lead <= 0xdbff && w.length > 1 ? w.slice(0, 2) : w.charAt(0);
}

function initials(name: string | null | undefined): string {
  // Spread = code points, so emoji initials (🦊 Fox) don't split a surrogate
  // pair into �. Missing names fall back to "?" instead of crashing.
  const parts = (name ?? "")
    .replace(/^Dr\.\s*/, "")
    .split(/[\s.]+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((w) => firstChar(w).toUpperCase())
    .join("");
  return parts || "?";
}

function toRis(pubs: SourceItem[]): string {
  return pubs
    .map((p) => {
      const href = doiHref(p.doi);
      return [
        "TY  - JOUR",
        `TI  - ${p.title}`,
        p.year ? `Y1  - ${p.year}` : null,
        p.doi ? `DO  - ${p.doi}` : null,
        href ? `UR  - ${href}` : null,
        `ID  - ${p.publication_id}`,
        "ER  - ",
        "",
      ]
        .filter(Boolean)
        .join("\n");
    })
    .join("\n");
}

/**
 * Researcher profile (HTML mockup §7). Every figure comes from the
 * disambiguation candidate + the current answer set — h-index, citation
 * totals, and abstracts are not shown because the API does not return them.
 */
export function AuthorDetailView({ author, publications, onOpenPublication, onResolve, titleRef }: AuthorDetailViewProps) {
  const [following, setFollowing] = useState(false);

  if (!author) {
    return (
      <section className="panel-card" aria-labelledby="author-empty-title">
        <h2 id="author-empty-title" ref={titleRef} tabIndex={-1}>
          No researcher selected
        </h2>
        <p className="sub">
          The author index resolves through disambiguation. Ask an author-scoped question and pick a candidate to open
          a grounded profile.
        </p>
        <div className="resolve-row">
          <button type="button" className="ask-btn btn-resolve" onClick={onResolve}>
            Go to disambiguation <ArrowRight size={15} aria-hidden />
          </button>
        </div>
      </section>
    );
  }

  const scored = publications.filter((p) => typeof p.relevance_score === "number") as Array<SourceItem & { relevance_score: number }>;
  const meanRel = scored.length ? scored.reduce((a, p) => a + (p.relevance_score ?? 0), 0) / scored.length : null;
  const maxRel = scored.length ? Math.max(...scored.map((p) => p.relevance_score ?? 0)) : null;

  const exportRis = () => {
    const blob = new Blob([toRis(publications)], { type: "application/x-research-info-systems" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${author.id}-publications.ris`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="author" aria-labelledby="author-title">
      <div className="panel-card author-head-card">
        <div className="author-head">
          <div className="author-id-block">
            <span className="author-avatar" aria-hidden>
              {initials(author.name)}
            </span>
            <div>
              <div className="author-name-row">
                <h2 id="author-title" ref={titleRef} tabIndex={-1} className="author-name">
                  {author.name}
                </h2>
                <span className="verified-pill">Corpus Verified</span>
              </div>
              <p className="author-aff">{author.affiliation ?? "Affiliation recorded in corpus"}</p>
              <p className="mono author-ids">
                Candidate ID: <strong>{author.id}</strong> <span aria-hidden>•</span> Type: <strong>{author.type}</strong>
              </p>
            </div>
          </div>
          <div className="author-actions">
            <button type="button" className="btn-retry author-follow" onClick={() => setFollowing((f) => !f)} aria-pressed={following}>
              {following ? "Following ✓" : "Follow Updates"}
            </button>
            <button type="button" className="chip-state" onClick={exportRis} disabled={!publications.length}>
              <Download size={13} aria-hidden /> <span className="mono">Export CV (.RIS)</span>
            </button>
          </div>
        </div>

        <div className="metric-row mono">
          <div className="metric-cell">
            <span className="metric-label">Indexed Pubs</span>
            <span className="metric-value">{author.publication_count}</span>
            <span className="metric-sub">Corpus count</span>
          </div>
          <div className="metric-cell">
            <span className="metric-label">Linked Sources</span>
            <span className="metric-value">{publications.length}</span>
            <span className="metric-sub">In this answer set</span>
          </div>
          <div className="metric-cell">
            <span className="metric-label">Mean Relevance</span>
            <span className="metric-value">{meanRel !== null ? meanRel.toFixed(2) : "—"}</span>
            <span className="metric-sub">Answer-set match</span>
          </div>
          <div className="metric-cell">
            <span className="metric-label">Top Relevance</span>
            <span className="metric-value">{maxRel !== null ? maxRel.toFixed(2) : "—"}</span>
            <span className="metric-sub">Best match</span>
          </div>
        </div>

        <div className="author-pubs">
          <div className="author-pubs-head mono">
            <span>Linked Publications</span>
            <span>Showing {publications.length} record{publications.length === 1 ? "" : "s"}</span>
          </div>
          <div className="author-pubs-list">
            {publications.map((p) => (
              <div key={p.publication_id} className="author-pub">
                <div className="author-pub-main">
                  <button type="button" className="author-pub-title" onClick={() => onOpenPublication(p.publication_id)}>
                    {p.title}
                  </button>
                  <p className="author-pub-meta">
                    {p.year ?? "—"} · <span className="mono">{p.source_type}</span>
                    {p.doi ? <span className="mono author-pub-doi"> doi:{p.doi}</span> : <span className="mono"> no-doi</span>}
                  </p>
                </div>
                <div className="mono author-pub-score">
                  <span className="metric-value sm">{typeof p.relevance_score === "number" ? p.relevance_score.toFixed(2) : "—"}</span>
                  <span className="metric-sub">relevance</span>
                </div>
              </div>
            ))}
            {publications.length === 0 && <p className="rail-empty">No linked publications in this answer set.</p>}
          </div>
        </div>
      </div>
    </div>
  );
}
