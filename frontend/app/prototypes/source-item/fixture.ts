export interface ProtoSourceItem {
  publication_id: string;
  title: string;
  source_type: string;
  year: number | null;
  doi: string | null;
  relevance_score: number;
  provenance: string;
}

export const SOURCES: ProtoSourceItem[] = [
  {
    publication_id: "SCP-0042",
    title: "Mesenchymal stem cell therapy trends in Southeast Asia",
    source_type: "publication",
    year: 2023,
    doi: "10.1016/j.stem.2023.04.011",
    relevance_score: 0.92,
    provenance: "VectorRoute · chunk #14 · cosine 0.81",
  },
  {
    publication_id: "SCP-0089",
    title: "Co-authorship networks in Bandung research labs",
    source_type: "graph_edge",
    year: 2022,
    doi: "10.1007/s11192-022-04310-9",
    relevance_score: 0.74,
    provenance: "GraphRoute · author_collaboration · 2 hops",
  },
  {
    publication_id: "SCP-0117",
    title: "Institutional output mapping for Indonesian biomedical research",
    source_type: "publication",
    year: null,
    doi: null,
    relevance_score: 0.61,
    provenance: "SQLRoute · publications · year filter ignored",
  },
];
