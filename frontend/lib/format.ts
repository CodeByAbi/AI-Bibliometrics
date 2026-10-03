import type { AskResponse, SourceItem } from "./api";

/**
 * Split answer text on verified [Title, Year|n.d., DOI|no-doi] citations.
 *
 * The year alternative mirrors `matchCitationToSource` exactly — a citation
 * the matcher can resolve must also be splittable, or a `n.d.` record would
 * render as inert text while a year-bearing one renders as a link.
 */
export function renderAnswerParts(answer: string): Array<{ kind: "text" | "cite"; value: string }> {
  const re = /\[([^\[\]]+?,\s*(?:\d{4}|n\.d\.),\s*(?:10\.\S+|no-doi))\]/g;
  const parts: Array<{ kind: "text" | "cite"; value: string }> = [];
  let last = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(answer)) !== null) {
    if (m.index > last) parts.push({ kind: "text", value: answer.slice(last, m.index) });
    parts.push({ kind: "cite", value: m[0] });
    last = m.index + m[0].length;
  }
  if (last < answer.length) parts.push({ kind: "text", value: answer.slice(last) });
  return parts.length ? parts : [{ kind: "text", value: answer }];
}

/**
 * Worst-case-hardened number formatting. DB columns are TEXT/unbounded and
 * the API contract is not runtime-validated, so null/NaN/Infinity must render
 * as "—" instead of crashing the rail (null.toLocaleString throws) or
 * printing "NaN". Uses the viewer's locale — separators are not hardcoded.
 */
export function formatValue(v: number | string | null | undefined): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "string") return v;
  if (!Number.isFinite(v)) return "—";
  if (Number.isInteger(v)) return v.toLocaleString();
  if (Math.abs(v) < 1 && Math.abs(v) > 0) return `${(v * 100).toFixed(2)}%`;
  return v.toLocaleString(undefined, { maximumFractionDigits: 2 });
}

/** "1 pub" / "3 pubs" — Intl.PluralRules instead of a hardcoded plural. */
export function plural(n: number, one: string, many: string): string {
  if (!Number.isFinite(n)) return `— ${many}`;
  const rule = new Intl.PluralRules("en-US").select(Math.trunc(n));
  return `${n.toLocaleString()} ${rule === "one" ? one : many}`;
}

/** First 8 chars of a request id, safe against missing ids. */
export function shortId(requestId: string | null | undefined): string {
  const id = (requestId ?? "").slice(0, 8);
  return id || "—";
}

/**
 * Latency cell: null/NaN/Infinity render as "—". Note Number(null) === 0,
 * so an explicit type check is required — a bare isFinite would print "0.0ms"
 * for a missing measurement.
 */
export function formatMs(v: number | null | undefined, digits = 1): string {
  if (typeof v !== "number" || !Number.isFinite(v)) return "—";
  return `${v.toFixed(digits)}ms`;
}

export function doiHref(doi?: string | null): string | null {
  if (!doi) return null;
  const d = doi.trim();
  if (!d || d === "no-doi") return null;
  return d.startsWith("http") ? d : `https://doi.org/${d}`;
}

export function statusLabel(r: AskResponse, live: boolean): string {
  if (r.status !== "ok") return r.status.replace("_", " ");
  return live ? "Verified against live database" : "Verified against prototype snapshot";
}

/**
 * Match an inline `[Title, Year, DOI|no-doi]` citation to a retrieved source.
 * DOI equality wins; otherwise a title-prefix + year match. Returns the
 * publication_id or null when the citation traces to nothing on screen.
 */
export function matchCitationToSource(cite: string, sources: SourceItem[]): string | null {
  const m = cite.match(/^\[([\s\S]+?),\s*(\d{4}|n\.d\.),\s*(.+?)\]$/);
  if (!m) return null;
  const rawTitle = (m[1] ?? "").trim().toLowerCase();
  const rawYear = (m[2] ?? "").trim();
  const rawDoi = (m[3] ?? "").trim().toLowerCase();
  const year = rawYear === "n.d." ? null : Number(rawYear);

  if (rawDoi && rawDoi !== "no-doi") {
    const byDoi = sources.find((s) => (s.doi ?? "").trim().toLowerCase() === rawDoi);
    if (byDoi) return byDoi.publication_id;
  }
  if (rawTitle) {
    const key = rawTitle.slice(0, 28);
    const byTitle = sources.find((s) => {
      const t = (s.title ?? "").toLowerCase();
      // Empty titles match everything via "".includes — never match on them.
      if (!t) return false;
      const hit = t.includes(key) || key.includes(t.slice(0, 28));
      if (!hit) return false;
      return year === null || s.year === null || s.year === undefined || s.year === year;
    });
    if (byTitle) return byTitle.publication_id;
  }
  return null;
}

/** Split a grounded answer into a lead conclusion + supporting narrative. */
export function splitLead(answer: string): [string, string] {
  const m = answer.match(/^(.+?[.!?])\s+([\s\S]*)$/);
  if (!m) return [answer, ""];
  // Keep lead to a readable length; avoid swallowing the whole brief.
  if ((m[1] ?? "").length > 220) return [answer, ""];
  return [m[1] ?? answer, m[2] ?? ""];
}
