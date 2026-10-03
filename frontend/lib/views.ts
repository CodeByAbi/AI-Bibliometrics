import type { AskResponse, CandidateItem, EvidenceObject, SourceItem } from "./api";
import { fixtureClarify, fixtureHybrid } from "./api";

export type WorkspaceView =
  | "empty"
  | "loading"
  | "answer"
  | "clarify"
  | "notfound"
  | "explore"
  | "author"
  | "publication"
  | "error";

/** The 8 HTML mockup tabs mapped onto workspace views (error stays lab-only). */
export const VIEW_TABS: Array<{ id: string; view: WorkspaceView; tab: string }> = [
  { id: "workspace", view: "answer", tab: "1. Brief & Provenance" },
  { id: "idle", view: "empty", tab: "2. Idle" },
  { id: "pipeline", view: "loading", tab: "3. Pipeline" },
  { id: "clarification", view: "clarify", tab: "4. Disambiguation" },
  { id: "not-found", view: "notfound", tab: "5. Zero-State" },
  { id: "explore", view: "explore", tab: "6. Trends" },
  { id: "author-detail", view: "author", tab: "7. Author CV" },
  { id: "publication-detail", view: "publication", tab: "8. Paper Detail" },
];

export function isGroundedView(v: WorkspaceView): boolean {
  return v === "answer" || v === "clarify" || v === "notfound" || v === "explore" || v === "author" || v === "publication";
}

/** Resolve the publication shown in the Paper Detail view. */
export function selectPublication(response: AskResponse | null, pubId: string | null): SourceItem | null {
  const pool = response?.sources?.length ? response.sources : fixtureHybrid.sources;
  if (!pool.length) return null;
  if (pubId) {
    const hit = pool.find((s) => s.publication_id === pubId);
    if (hit) return hit;
  }
  return pool[0] ?? null;
}

/** Evidence objects traced to a publication (for the Paper Detail view). */
export function evidenceForPublication(response: AskResponse | null, pubId: string): EvidenceObject[] {
  if (!response) return [];
  return response.evidence_objects.filter((ev) => ev.sources.some((s) => s.publication_id === pubId));
}

/** Resolve the author shown in the Author CV view — always from real candidates. */
export function selectAuthor(response: AskResponse | null, authorId: string | null): CandidateItem | null {
  const cands = response?.candidates?.filter((c) => c.type === "author") ?? [];
  if (authorId) {
    const hit = cands.find((c) => c.id === authorId);
    if (hit) return hit;
  }
  if (cands.length) return cands[0] ?? null;
  const fallback = (fixtureClarify.candidates ?? []).filter((c) => c.type === "author");
  return fallback[0] ?? null;
}

/** Publications in the current answer set plausibly linked to an author name. */
export function publicationsForAuthor(response: AskResponse | null, author: CandidateItem | null): SourceItem[] {
  const pool = response?.sources?.length ? response.sources : fixtureHybrid.sources;
  if (!author) return pool.slice(0, 3);
  const surname = author.name.replace(/^Dr\.\s*/, "").split(/[\s.]+/).filter(Boolean).pop()?.toLowerCase() ?? "";
  if (!surname) return pool.slice(0, 3);
  const linked = pool.filter((s) => s.title.toLowerCase().includes(surname) || (s.provenance ?? "").toLowerCase().includes(surname));
  return (linked.length ? linked : pool).slice(0, 3);
}

/** Year histogram over the current answer's sources (for the Trends chart). */
export function yearHistogram(response: AskResponse | null): Array<{ year: number; count: number }> {
  const pool = response?.sources?.length ? response.sources : fixtureHybrid.sources;
  const map = new Map<number, number>();
  for (const s of pool) {
    if (typeof s.year === "number") map.set(s.year, (map.get(s.year) ?? 0) + 1);
  }
  return Array.from(map.entries())
    .map(([year, count]) => ({ year, count }))
    .sort((a, b) => a.year - b.year);
}

/** Source-type distribution over the current answer's sources. */
export function typeDistribution(response: AskResponse | null): Array<{ type: string; count: number; share: number }> {
  const pool = response?.sources?.length ? response.sources : fixtureHybrid.sources;
  const map = new Map<string, number>();
  for (const s of pool) map.set(s.source_type, (map.get(s.source_type) ?? 0) + 1);
  const total = pool.length || 1;
  return Array.from(map.entries()).map(([type, count]) => ({ type, count, share: count / total }));
}
