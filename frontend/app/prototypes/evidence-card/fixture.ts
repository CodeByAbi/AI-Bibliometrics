export interface ProtoSource {
  publication_id: string;
  title: string;
}

export interface ProtoEvidence {
  metric: string;
  value: string;
  period: string;
  confidence: number;
  claim: string;
  provenance: string;
  sources: ProtoSource[];
}

export const EVIDENCE: ProtoEvidence[] = [
  {
    metric: "publication_count",
    value: "47",
    period: "2020–2025",
    confidence: 0.87,
    claim: "Indonesian institutions drove MSC therapy output growth across the 2020–2025 window.",
    provenance: "Gold analytics · topics + researcher_expertise",
    sources: [
      { publication_id: "SCP-0042", title: "Mesenchymal stem cell therapy trends in Southeast Asia" },
      { publication_id: "SCP-0117", title: "Institutional output mapping for Indonesian biomedical research" },
    ],
  },
  {
    metric: "collaboration_strength",
    value: "0.73",
    period: "2021–2023",
    confidence: 0.78,
    claim: "Bandung labs anchor the GraphRoute co-authorship cluster for this query.",
    provenance: "Graph retriever · author_collaboration, max_hops = 3",
    sources: [
      { publication_id: "SCP-0089", title: "Co-authorship networks in Bandung research labs" },
      { publication_id: "SCP-0131", title: "Cross-institutional collaboration and citation gain" },
      { publication_id: "SCP-0204", title: "Mapping emerging clusters in Indonesian science" },
    ],
  },
  {
    metric: "cosine_min",
    value: "0.68",
    period: "2020–2025",
    confidence: 0.91,
    claim: "VectorRoute admitted only chunks above the 0.48 cosine gate into synthesis.",
    provenance: "Vector retriever · BAAI/bge-m3, HNSW <=> search",
    sources: [{ publication_id: "SCP-0156", title: "Dense retrieval thresholds for grounded synthesis" }],
  },
];
