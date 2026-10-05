import { AppError, NetworkError, isAbortError, parseBackendError, withRetry } from "./errors";

export type RouteKind = "SQLRoute" | "VectorRoute" | "GraphRoute" | "HybridRoute";
export type StatusKind = "ok" | "not_found" | "needs_clarification" | "error";
export type SourceType = "sql" | "vector" | "graph" | "analytics";

export interface EvidenceSourceRef {
  publication_id: string;
  doi?: string | null;
  eid?: string | null;
  title?: string | null;
  year?: number | null;
}

export interface EvidenceObject {
  claim: string;
  metric: string;
  value: number | string;
  period: string;
  sources: EvidenceSourceRef[];
  confidence: number;
}

export interface SourceItem {
  publication_id: string;
  title: string;
  year?: number | null;
  doi?: string | null;
  source_type: SourceType;
  relevance_score?: number | null;
  provenance?: string | null;
}

export interface CandidateItem {
  id: string;
  name: string;
  type: "author" | "institution" | "topic";
  publication_count: number;
  affiliation?: string | null;
}

export interface AskResponse {
  request_id: string;
  status: StatusKind;
  route: RouteKind;
  answer: string;
  evidence_objects: EvidenceObject[];
  sources: SourceItem[];
  candidates?: CandidateItem[] | null;
  filters_ignored: string[];
  answered_via_fallback: boolean;
  unverified_citations: string[];
  /** Echo of the request's session_id when the turn was attached to a session. */
  session_id?: string | null;
  /** Filter keys auto-filled from prior session scope. Debug-reports only. */
  session_filters_applied?: string[] | null;
  session_context_used?: boolean | null;
  debug?: {
    sql_executed?: string | null;
    route_reasoning?: string | null;
    latency_breakdown_ms?: Record<string, number> | null;
    scored_chunks?: Array<Record<string, unknown>> | null;
    embedding_backend?: string | null;
    synthesis_backend?: string | null;
    evidence_set?: Record<string, unknown> | null;
  } | null;
}

export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE?.replace(/\/$/, "") || "http://localhost:8000";

export async function postAsk(
  question: string,
  developerMode: boolean,
  signal?: AbortSignal,
  filters?: Record<string, string | number | null | undefined>,
  sessionId?: string | null,
): Promise<AskResponse> {
  // One id per user action, minted outside the retry closure: a retry is the
  // same request, so the backend trace stays a single correlated record.
  const requestId =
    typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
      ? crypto.randomUUID()
      : `req-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
  const doFetch = async (): Promise<AskResponse> => {
    let res: Response;
    try {
      res = await fetch(`${API_BASE}/api/v1/ask`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Request-ID": requestId },
        body: JSON.stringify({
          question,
          filters: filters ?? {},
          developer_mode: developerMode,
          // Omitted entirely when absent rather than sent as null: the backend
          // treats a missing session_id as "stateless question", which is the
          // behaviour the pre-session UI relies on. Sending null would be a
          // different request with the same intent.
          ...(sessionId ? { session_id: sessionId } : {}),
        }),
        signal,
      });
    } catch (error) {
      if (isAbortError(error)) throw error;
      throw new NetworkError(error instanceof Error ? `Backend unreachable: ${error.message}` : "Backend unreachable");
    }
    if (!res.ok) {
      const text = await res.text().catch(() => "");
      throw await parseBackendError(res, text);
    }
    // P0-A: a 200 whose body is not a usable AskResponse must be an explicit
    // error, never a partially-populated object the views then read counts
    // off. Previously any parseable JSON was cast and trusted, so a truncated
    // or wrong-shaped payload rendered as a successful answer with no evidence.
    let parsed: unknown;
    try {
      parsed = await res.json();
    } catch {
      throw new AppError(
        "The backend returned a response that could not be read.",
        "MALFORMED_RESPONSE",
        502,
      );
    }
    if (!isAskResponse(parsed)) {
      throw new AppError(
        "The backend returned an unexpected response shape.",
        "MALFORMED_RESPONSE",
        502,
      );
    }
    return parsed;
  };
  // P0-B: exactly ONE attempt.
  //
  // The backend already returns structured 4xx/5xx envelopes, so a client-side
  // retry adds nothing except wall clock: it doubled an 8 s abort into ~16.5 s.
  // Worse, POST /api/v1/ask commits the user's question to
  // `app.research_messages` BEFORE retrieval runs, so a retry silently
  // duplicated the question in the conversation transcript.
  return withRetry(doFetch, { maxAttempts: 1, signal });
}

/**
 * Structural guard for the response envelope.
 *
 * Only the fields the UI dereferences unconditionally are checked. `sources`,
 * `evidence_objects` and `unverified_citations` are defaulted to empty arrays
 * below so a partially-shaped but recognisable payload degrades to an empty
 * state rather than a crash — an empty answer set is a valid outcome, a
 * fabricated one is not.
 */
function isAskResponse(v: unknown): v is AskResponse {
  if (typeof v !== "object" || v === null) return false;
  const o = v as Record<string, unknown>;
  if (typeof o.request_id !== "string") return false;
  if (typeof o.status !== "string") return false;
  if (typeof o.answer !== "string") return false;
  if (typeof o.route !== "string") return false;
  if (!Array.isArray(o.sources)) return false;
  if (!Array.isArray(o.evidence_objects)) return false;
  if (o.sources.length > 0 && typeof (o.sources[0] as Record<string, unknown>)?.title !== "string") {
    return false;
  }
  return true;
}

/* ------------------------------------------------------------------ */
/* Realistic fixtures — shaped exactly like POST /api/v1/ask responses */
/* ------------------------------------------------------------------ */

export const SEEDS = [
  {
    id: "hybrid-msc",
    label: "MSC therapy trend + leading experts",
    question:
      "Stem-cell therapy publications from Indonesian institutions after 2020 — what is emerging, and who are the experts?",
  },
  {
    id: "sql-top",
    label: "Top productive authors in 2023",
    question: "Who were the 5 most productive authors in 2023 by publication count?",
  },
  {
    id: "vector-wharton",
    label: "Oxidative stress in Wharton's jelly",
    question: "Papers on oxidative stress in Wharton's jelly mesenchymal stem cells?",
  },
] as const;

export const fixtureHybrid: AskResponse = {
  request_id: "c83b7e41-6a20-4e89-981f-f1791a8e9921",
  status: "ok",
  route: "HybridRoute",
  answer:
    "Mesenchymal stem cell (MSC) therapy is the fastest-growing topic among Indonesian institutions since 2021, with publication output up 28.4% year-over-year in 2023. The leading expert is Dr. A. Rahman with an expertise score of 84.50, supported by 14 topic publications and a topic H-index of 9 [Mesenchymal Stem Cell Therapy for Cartilage Regeneration, 2023, 10.1016/j.cell.2023.01.002]. Isolation-protocol work on Wharton's jelly forms the semantic core of the cluster [Wharton's Jelly Isolation Protocol for Clinical-Grade MSCs, 2021, no-doi]. Collaboration is concentrated between Universitas Indonesia and Institut Teknologi Bandung [Collaborative Networks in Indonesian Stem Cell Research, 2022, 10.1016/j.stem.2022.04.011].",
  evidence_objects: [
    {
      claim: "MSC therapy publications grew 28.4% YoY in 2023",
      metric: "growth_score",
      value: 0.284,
      period: "2023",
      sources: [
        {
          publication_id: "pub_89210",
          doi: "10.1016/j.cell.2023.01.002",
          eid: "2-s2.0-851492019",
          title: "Mesenchymal Stem Cell Therapy for Cartilage Regeneration",
          year: 2023,
        },
      ],
      confidence: 1.0,
    },
    {
      claim: "Dr. A. Rahman leads MSC expertise with score 84.50 and topic H-index 9",
      metric: "expertise_score",
      value: 84.5,
      period: "all-time",
      sources: [
        {
          publication_id: "pub_89210",
          doi: "10.1016/j.cell.2023.01.002",
          eid: "2-s2.0-851492019",
          title: "Mesenchymal Stem Cell Therapy for Cartilage Regeneration",
          year: 2023,
        },
      ],
      confidence: 0.97,
    },
    {
      claim: "14 MSC-cluster publications attributed to the leading expert",
      metric: "publication_count",
      value: 14,
      period: "2020-2023",
      sources: [
        {
          publication_id: "pub_77402",
          doi: null,
          eid: "2-s2.0-851100233",
          title: "Wharton's Jelly Isolation Protocol for Clinical-Grade MSCs",
          year: 2021,
        },
      ],
      confidence: 0.93,
    },
  ],
  sources: [
    {
      publication_id: "pub_89210",
      title: "Mesenchymal Stem Cell Therapy for Cartilage Regeneration",
      year: 2023,
      doi: "10.1016/j.cell.2023.01.002",
      source_type: "analytics",
      relevance_score: 0.94,
      provenance: "Gold Layer: topics & researcher_expertise",
    },
    {
      publication_id: "pub_77402",
      title: "Wharton's Jelly Isolation Protocol for Clinical-Grade MSCs",
      year: 2021,
      doi: null,
      source_type: "vector",
      relevance_score: 0.87,
      provenance: "Silver Layer: chunks (bge-m3, cosine 0.87)",
    },
    {
      publication_id: "pub_80119",
      title: "Collaborative Networks in Indonesian Stem Cell Research",
      year: 2022,
      doi: "10.1016/j.stem.2022.04.011",
      source_type: "graph",
      relevance_score: 0.81,
      provenance: "Edge Layer: institution_collaboration (UI–ITB, weight 6)",
    },
    {
      publication_id: "pub_81207",
      title: "Citation Acceleration in Emerging Regenerative Topics",
      year: 2023,
      doi: "10.1007/s00441-023-03781-4",
      source_type: "sql",
      relevance_score: 0.78,
      provenance: "Silver Layer: publications (citation_count 214)",
    },
  ],
  filters_ignored: [],
  answered_via_fallback: false,
  unverified_citations: [],
  debug: {
    sql_executed:
      "SELECT topic_name, publication_count, growth_score FROM topics WHERE topic_name ILIKE '%mesenchymal%' ORDER BY growth_score DESC LIMIT 50;",
    route_reasoning:
      "HybridRoute: question combines emerging-topic intent ('what is emerging') with expertise intent ('who are the experts') plus an institution filter — requires Gold analytics joined to Silver.",
    latency_breakdown_ms: {
      routing_ms: 18.4,
      entity_resolution_ms: 42.1,
      vector_retrieval_ms: 210.6,
      evidence_unify_ms: 12.3,
      synthesis_ms: 4210.0,
      verification_ms: 3.1,
      total_ms: 4496.5,
    },
  },
};

export const fixtureSQL: AskResponse = {
  request_id: "a41f0c22-9d3e-4b71-9f2c-0d19aa31c7e2",
  status: "ok",
  route: "SQLRoute",
  answer:
    "The five most productive authors in 2023 are led by Dr. A. Rahman with 14 publications, followed by Dr. B. Santoso with 11 [Mesenchymal Stem Cell Therapy for Cartilage Regeneration, 2023, 10.1016/j.cell.2023.01.002]. Counts use COUNT(DISTINCT publication_id) over the authorship junction to avoid double-counting co-authored papers.",
  evidence_objects: [
    {
      claim: "Dr. A. Rahman published 14 papers in 2023",
      metric: "publication_count",
      value: 14,
      period: "2023",
      sources: [
        {
          publication_id: "pub_89210",
          doi: "10.1016/j.cell.2023.01.002",
          eid: "2-s2.0-851492019",
          title: "Mesenchymal Stem Cell Therapy for Cartilage Regeneration",
          year: 2023,
        },
      ],
      confidence: 1.0,
    },
    {
      claim: "Dr. B. Santoso published 11 papers in 2023",
      metric: "publication_count",
      value: 11,
      period: "2023",
      sources: [
        {
          publication_id: "pub_81207",
          doi: "10.1007/s00441-023-03781-4",
          eid: "2-s2.0-851552781",
          title: "Citation Acceleration in Emerging Regenerative Topics",
          year: 2023,
        },
      ],
      confidence: 1.0,
    },
  ],
  sources: [
    {
      publication_id: "pub_89210",
      title: "Mesenchymal Stem Cell Therapy for Cartilage Regeneration",
      year: 2023,
      doi: "10.1016/j.cell.2023.01.002",
      source_type: "sql",
      relevance_score: 1.0,
      provenance: "Silver Layer: publications ⨝ pub_author (COUNT DISTINCT)",
    },
    {
      publication_id: "pub_81207",
      title: "Citation Acceleration in Emerging Regenerative Topics",
      year: 2023,
      doi: "10.1007/s00441-023-03781-4",
      source_type: "sql",
      relevance_score: 0.99,
      provenance: "Silver Layer: publications ⨝ pub_author",
    },
  ],
  filters_ignored: [],
  answered_via_fallback: false,
  unverified_citations: [],
  debug: {
    sql_executed:
      "SELECT a.author_name, COUNT(DISTINCT p.publication_id) AS n FROM authors a JOIN pub_author pa ON pa.author_id = a.author_id JOIN publications p ON p.publication_id = pa.publication_id WHERE p.year = 2023 GROUP BY a.author_name_normalized, a.author_name ORDER BY n DESC LIMIT 5;",
    route_reasoning:
      "SQLRoute: aggregation intent ('5 most productive', year filter 2023) over canonical Silver tables — deterministic Text-to-SQL with AST whitelist.",
    latency_breakdown_ms: {
      routing_ms: 9.2,
      entity_resolution_ms: 11.8,
      sql_retrieval_ms: 88.4,
      evidence_unify_ms: 6.9,
      synthesis_ms: 3120.0,
      verification_ms: 2.2,
      total_ms: 3238.5,
    },
  },
};

export const fixtureVector: AskResponse = {
  request_id: "7e02b1c4-55aa-4d9e-8f1c-3c2a9e05b410",
  status: "ok",
  route: "VectorRoute",
  answer:
    "Conceptual search over 40 embedded abstract chunks finds a coherent Wharton's jelly cluster centred on oxidative-stress preconditioning (cosine 0.87) [Wharton's Jelly Isolation Protocol for Clinical-Grade MSCs, 2021, no-doi]. A second passage links hypoxic preconditioning to improved post-thaw viability [Hypoxic Preconditioning Improves MSC Viability, 2022, 10.1186/s13287-022-02914-7].",
  evidence_objects: [
    {
      claim: "Top semantic match for oxidative-stress query at cosine 0.87",
      metric: "similarity_score",
      value: 0.87,
      period: "all-time",
      sources: [
        {
          publication_id: "pub_77402",
          doi: null,
          eid: "2-s2.0-851100233",
          title: "Wharton's Jelly Isolation Protocol for Clinical-Grade MSCs",
          year: 2021,
        },
      ],
      confidence: 0.87,
    },
  ],
  sources: [
    {
      publication_id: "pub_77402",
      title: "Wharton's Jelly Isolation Protocol for Clinical-Grade MSCs",
      year: 2021,
      doi: null,
      source_type: "vector",
      relevance_score: 0.87,
      provenance: "Silver Layer: chunks (bge-m3, DISTINCT ON publication_id)",
    },
    {
      publication_id: "pub_77931",
      title: "Hypoxic Preconditioning Improves MSC Viability",
      year: 2022,
      doi: "10.1186/s13287-022-02914-7",
      source_type: "vector",
      relevance_score: 0.79,
      provenance: "Silver Layer: chunks (bge-m3, cosine 0.79)",
    },
  ],
  filters_ignored: [],
  answered_via_fallback: false,
  unverified_citations: [],
  debug: {
    sql_executed: null,
    route_reasoning:
      "VectorRoute: semantic/concept intent ('papers on ...') with no aggregation — pgvector HNSW cosine search, DISTINCT ON (publication_id) LIMIT 8, gate ≥ 0.65.",
    latency_breakdown_ms: {
      routing_ms: 12.7,
      entity_resolution_ms: 8.3,
      vector_retrieval_ms: 164.2,
      evidence_unify_ms: 7.4,
      synthesis_ms: 2890.0,
      verification_ms: 2.8,
      total_ms: 3085.4,
    },
  },
};

export const fixtureClarify: AskResponse = {
  request_id: "b6d2f008-1c4e-4a2a-9c1e-77aa0e91d2c1",
  status: "needs_clarification",
  route: "GraphRoute",
  answer:
    "“Rahman” matches 3 authors and “Bandung” matches 2 institutions in the corpus. Select one to resolve the collaboration query — retrieval pauses until the entity is disambiguated.",
  evidence_objects: [],
  sources: [],
  candidates: [
    { id: "auth_014", name: "Dr. A. Rahman", type: "author", publication_count: 14, affiliation: "Universitas Indonesia" },
    { id: "auth_089", name: "A. Rahman Hakim", type: "author", publication_count: 6, affiliation: "Institut Teknologi Bandung" },
    { id: "auth_122", name: "Rahman Setiawan", type: "author", publication_count: 3, affiliation: "Universitas Gadjah Mada" },
    { id: "inst_007", name: "Institut Teknologi Bandung", type: "institution", publication_count: 22, affiliation: "Bandung, Indonesia" },
    { id: "inst_031", name: "Universitas Padjadjaran", type: "institution", publication_count: 9, affiliation: "Bandung, Indonesia" },
  ],
  filters_ignored: [],
  answered_via_fallback: false,
  unverified_citations: [],
  debug: {
    sql_executed: null,
    route_reasoning:
      "GraphRoute with EntityResolutionGate: ILIKE matched multiple canonical entities — returning needs_clarification with candidates instead of guessing.",
    latency_breakdown_ms: { routing_ms: 14.1, entity_resolution_ms: 66.5, total_ms: 80.6 },
  },
};

export const fixtureNotFound: AskResponse = {
  request_id: "d4c1a990-2b7e-4e5a-a1f0-9c8e41b2f6d7",
  status: "not_found",
  route: "VectorRoute",
  answer:
    "No supporting evidence was found in the database for this question. The vector gate (cosine ≥ 0.65) admitted zero chunks and the structured filters matched zero publications, so synthesis was skipped deterministically.",
  evidence_objects: [],
  sources: [],
  filters_ignored: [],
  answered_via_fallback: false,
  unverified_citations: [],
  debug: {
    sql_executed: null,
    route_reasoning:
      "VectorRoute short-circuit: max cosine 0.41 < 0.65 threshold — 0 evidence, 0 LLM calls, 142ms.",
    latency_breakdown_ms: { routing_ms: 11.9, vector_retrieval_ms: 130.4, total_ms: 142.3 },
  },
};

/* ------------------------------------------------------------------ */
/* Adversarial fixtures — break-ui worst-case set (dev-only, ?data=).  */
/* Every value is either plausible production text or a schema-backed  */
/* limit (TEXT unbounded, doi VARCHAR(255), year SMALLINT). `as unknown */
/* as` casts simulate runtime nulls that the TS contract forbids but   */
/* the database can actually deliver.                                  */
/*                                                                     */
/* P0-A: these are DEVELOPMENT fixtures. They must never be reachable  */
/* from a live request path. `use-ask` used to pass                   */
/* `pickFixture(question)` as the error fallback, so any backend        */
/* failure rendered one of these as if it were a real answer.           */
/* ------------------------------------------------------------------ */

export type DataKind = "demo" | "worst" | "empty" | "one" | "huge";

const LONG_DOI = "10.1016/j." + "supplemental-material-section-".repeat(7) + "001";

const LONG_CLAIM =
  "A systematic review and meta-analysis of mesenchymal stem cell therapy for cartilage regeneration across 214 " +
  "Scopus-indexed publications (2020–2023) reports pooled efficacy with substantial heterogeneity (I² = 78%), " +
  "subgroup effects by cell source, and publication-bias-adjusted estimates — Đặng Thị Ngọc Hân";

export const fixtureWorst: AskResponse = {
  request_id: undefined as unknown as string,
  status: "ok",
  route: "HybridRoute",
  answer:
    `MSC therapy output grew substantially since 2021 [${LONG_CLAIM.slice(0, 60)}…, 2023, ${LONG_DOI}]. ` +
    "Isolation-protocol work forms the semantic core [Wharton's Jelly Isolation Protocol for Clinical-Grade MSCs, 2021, no-doi]. " +
    "A second passage links hypoxic preconditioning to viability [Hypoxic Preconditioning Improves MSC Viability, 2022, 10.1186/s13287-022-02914-7].",
  evidence_objects: [
    {
      claim: LONG_CLAIM,
      metric: "Benachrichtigungseinstellungen",
      value: 1284000,
      period: "2021–2023 (revised) v12 [final]",
      sources: [
        {
          publication_id: "pub_90001",
          doi: LONG_DOI,
          eid: "2-s2.0-851492019",
          title: LONG_CLAIM,
          year: 2023,
        },
      ],
      confidence: 0.871,
    },
    {
      claim: "Metric pipeline returned NULL for this window — value and confidence missing upstream",
      metric: "growth_score",
      value: null as unknown as number,
      period: "2023",
      sources: [
        {
          publication_id: "pub_90002",
          doi: null,
          eid: "2-s2.0-851100233",
          title: "Wharton's Jelly Isolation Protocol for Clinical-Grade MSCs",
          year: 2021,
        },
      ],
      confidence: undefined as unknown as number,
    },
    {
      claim: "Escaping check: &amp; &lt;b&gt;bold&lt;/b&gt; renders literally — identical 40-char title prefixes collide in matchCitationToSource",
      metric: "publication_count",
      value: 14,
      period: "all-time",
      sources: [
        {
          publication_id: "pub_90003",
          doi: "10.1186/s13287-022-02914-7",
          eid: "2-s2.0-851552781",
          title: "Hypoxic Preconditioning Improves MSC Viability in Extended Culture Conditions",
          year: 2022,
        },
        {
          publication_id: "pub_90004",
          doi: "10.1186/s13287-022-02914-8",
          eid: "2-s2.0-851552782",
          title: "Hypoxic Preconditioning Improves MSC Viability Under Serum-Free Protocols",
          year: 2022,
        },
      ],
      confidence: 0.5,
    },
    {
      claim: "Non-finite value from a division-by-zero window",
      metric: "growth_score",
      value: NaN,
      period: "2023",
      sources: [
        {
          publication_id: "pub_90005",
          doi: null,
          eid: null,
          title: null,
          year: 0,
        },
      ],
      confidence: 1.0,
    },
  ],
  sources: [
    {
      publication_id: "pub_90001",
      title: LONG_CLAIM,
      year: 2023,
      doi: LONG_DOI,
      source_type: "analytics",
      relevance_score: 0.94,
      provenance: "Gold Layer: topics & researcher_expertise",
    },
    {
      publication_id: "pub_90002",
      title: "Biến đổi khí hậu và thích ứng nông nghiệp đồng bằng sông Cửu Long: Đặng Thị Ngọc Hân",
      year: 0,
      doi: null,
      source_type: "vector",
      relevance_score: 0.87,
      provenance: "Silver Layer: chunks (bge-m3, cosine 0.87)",
    },
    {
      publication_id: "pub_90003",
      title: "تأثير تغير المناخ على الزراعة في دلتا ميكونغ: دراسة ببليومترية",
      year: 2022,
      doi: "10.1186/s13287-022-02914-7",
      source_type: "graph",
      relevance_score: null,
      provenance: null,
    },
    {
      publication_id: "pub_90004",
      title: "Hypoxic Preconditioning Improves MSC Viability Under Serum-Free Protocols",
      year: 2100,
      doi: "10.1186/s13287-022-02914-8",
      source_type: "sql",
      relevance_score: 0.78,
      provenance: "Silver Layer: publications (citation_count 214)",
    },
    {
      publication_id: "pub_90005",
      title: "  Sam   Lee ",
      year: null,
      doi: null,
      source_type: "vector",
      relevance_score: 0.65,
      provenance: "Silver Layer: chunks (whitespace title)",
    },
  ],
  filters_ignored: [],
  answered_via_fallback: false,
  unverified_citations: [],
  debug: {
    sql_executed: null,
    route_reasoning: "break-ui worst-case fixture: unbounded TEXT, VARCHAR(255) DOI, SMALLINT year edge values, runtime nulls.",
    latency_breakdown_ms: {
      routing_ms: 18.4,
      synthesis_ms: null as unknown as number,
      total_ms: 4496.5,
    },
  },
};

export const fixtureEmpty: AskResponse = {
  request_id: "e5f6a7b8-0000-4e5a-a1f0-9c8e41b2f6d7",
  status: "ok",
  route: "VectorRoute",
  answer:
    "Synthesis ran but the evidence gate admitted zero records, so the brief carries no claims and the rail shows its empty states.",
  evidence_objects: [],
  sources: [],
  filters_ignored: [],
  answered_via_fallback: false,
  unverified_citations: [],
  debug: null,
};

export const fixtureOne: AskResponse = {
  request_id: "f6a7b8c9-1111-4e5a-a1f0-9c8e41b2f6d7",
  status: "ok",
  route: "SQLRoute",
  answer:
    "A single publication matches the query [Mesenchymal Stem Cell Therapy for Cartilage Regeneration, 2023, 10.1016/j.cell.2023.01.002].",
  evidence_objects: [
    {
      claim: "Exactly one publication matches all filters",
      metric: "publication_count",
      value: 1,
      period: "2023",
      sources: [
        {
          publication_id: "pub_89210",
          doi: "10.1016/j.cell.2023.01.002",
          eid: "2-s2.0-851492019",
          title: "Mesenchymal Stem Cell Therapy for Cartilage Regeneration",
          year: 2023,
        },
      ],
      confidence: 1.0,
    },
  ],
  sources: [
    {
      publication_id: "pub_89210",
      title: "Mesenchymal Stem Cell Therapy for Cartilage Regeneration",
      year: 2023,
      doi: "10.1016/j.cell.2023.01.002",
      source_type: "sql",
      relevance_score: 1.0,
      provenance: "Silver Layer: publications",
    },
  ],
  filters_ignored: [],
  answered_via_fallback: false,
  unverified_citations: [],
  debug: null,
};

/** 50 evidence + 50 sources — the server-side LIMIT 50 ceiling, generated deterministically. */
export function makeHugeFixture(): AskResponse {
  const sources = Array.from({ length: 50 }, (_, i) => ({
    publication_id: `pub_h${String(i).padStart(3, "0")}`,
    title:
      i % 7 === 0
        ? `${LONG_CLAIM} — variant ${i}`
        : `Huge-corpus publication ${i}: MSC therapy window analysis`,
    year: i % 11 === 0 ? 0 : 2020 + (i % 4),
    doi: i % 3 === 0 ? null : `10.1016/j.huge.2023.${String(i).padStart(4, "0")}`,
    source_type: (["sql", "vector", "graph", "analytics"] as const)[i % 4],
    relevance_score: i % 4 === 0 ? null : 0.99 - i * 0.005,
    provenance: i % 4 === 0 ? null : `Silver Layer: publications (batch ${i})`,
  }));
  const evidence_objects = Array.from({ length: 50 }, (_, i) => ({
    claim: `Corpus window ${i} aggregates ${1000 + i * 37} citations across the MSC cluster`,
    metric: "citation_count",
    value: 1000 + i * 37,
    period: `202${i % 4}`,
    sources: [
      {
        publication_id: sources[i]!.publication_id,
        doi: sources[i]!.doi,
        eid: null,
        title: sources[i]!.title,
        year: sources[i]!.year,
      },
    ],
    confidence: 0.6 + (i % 40) / 100,
  }));
  return {
    request_id: "huge-50-fixture-0000-4e5a-a1f0-9c8e41b2f6d7",
    status: "ok",
    route: "HybridRoute",
    answer:
      "Fifty evidence objects back this brief at the server LIMIT 50 ceiling [Huge-corpus publication 1: MSC therapy window analysis, 2021, 10.1016/j.huge.2023.0001].",
    evidence_objects,
    sources,
    filters_ignored: [],
    answered_via_fallback: false,
    unverified_citations: [],
    debug: null,
  };
}

/** Dev-only dataset switch. "demo" returns null = normal behavior. */
export function resolveDataFixture(kind: DataKind): AskResponse | null {
  switch (kind) {
    case "worst":
      return fixtureWorst;
    case "empty":
      return fixtureEmpty;
    case "one":
      return fixtureOne;
    case "huge":
      return makeHugeFixture();
    default:
      return null;
  }
}

export function parseDataKind(v: string | null): DataKind {
  return v === "worst" || v === "empty" || v === "one" || v === "huge" ? v : "demo";
}
