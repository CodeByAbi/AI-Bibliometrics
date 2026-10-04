import type { AskResponse, EvidenceObject, SourceItem } from "./api";

export type WorkspaceView =
  | "empty"
  | "loading"
  | "answer"
  | "clarify"
  | "notfound"
  | "explore"
  | "publication"
  | "error";

/** The 7 HTML mockup tabs mapped onto workspace views (error stays lab-only). */
export const VIEW_TABS: Array<{ id: string; view: WorkspaceView; tab: string }> = [
  { id: "workspace", view: "answer", tab: "1. Brief & Provenance" },
  { id: "idle", view: "empty", tab: "2. Idle" },
  { id: "pipeline", view: "loading", tab: "3. Pipeline" },
  { id: "clarification", view: "clarify", tab: "4. Disambiguation" },
  { id: "not-found", view: "notfound", tab: "5. Zero-State" },
  { id: "explore", view: "explore", tab: "6. Trends" },
  { id: "publication-detail", view: "publication", tab: "7. Paper Detail" },
];

export function isGroundedView(v: WorkspaceView): boolean {
  return v === "answer" || v === "clarify" || v === "notfound" || v === "explore" || v === "publication";
}

/**
 * Source pool for a real answer set. NEVER a fixture.
 *
 * P0-A: this used to return `fixtureHybrid.sources` whenever `response` was
 * null, which meant the Trends chart plotted four invented publications in the
 * error and timeout states — and the "Cross-analyzing N cited publications in
 * this answer set" caption became a lie about data that was never retrieved.
 * A null response now yields an empty pool, which the charts already render as
 * an explicit "run a question to populate" state.
 */
function sourcePool(response: AskResponse | null): SourceItem[] {
  return response?.sources ?? [];
}

/**
 * Resolve the publication shown in the Paper Detail view.
 *
 * P0-A: this used to fall back to `fixtureHybrid.sources` whenever the answer
 * set had zero sources. That made a legitimate `not_found` — zero retrieved
 * sources — render a real-looking publication detail page with an invented
 * title, year, and citation count. Returns null instead; the caller renders its
 * own empty state.
 */
export function selectPublication(response: AskResponse | null, pubId: string | null): SourceItem | null {
  const pool = sourcePool(response);
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

/** Year histogram over the current answer's sources (for the Trends chart). */
export function yearHistogram(response: AskResponse | null): Array<{ year: number; count: number }> {
  const map = new Map<number, number>();
  for (const s of sourcePool(response)) {
    if (typeof s.year === "number") map.set(s.year, (map.get(s.year) ?? 0) + 1);
  }
  return Array.from(map.entries())
    .map(([year, count]) => ({ year, count }))
    .sort((a, b) => a.year - b.year);
}

/** Source-type distribution over the current answer's sources. */
export function typeDistribution(response: AskResponse | null): Array<{ type: string; count: number; share: number }> {
  const pool = sourcePool(response);
  const map = new Map<string, number>();
  for (const s of pool) map.set(s.source_type, (map.get(s.source_type) ?? 0) + 1);
  const total = pool.length || 1;
  return Array.from(map.entries()).map(([type, count]) => ({ type, count, share: count / total }));
}