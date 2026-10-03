export interface ZeroStateData {
  question: string;
  route: string;
  request_id: string;
  route_reasoning: string;
  seeds: Array<{ label: string; question: string }>;
}

export const ZERO: ZeroStateData = {
  question: "Quantum-dot yields in deep-sea fisheries after 2020?",
  route: "VectorRoute",
  request_id: "req_9f31a2c4",
  route_reasoning:
    "The evidence gate admitted zero records, so synthesis was skipped deterministically — no language model was called.",
  seeds: [
    {
      label: "MSC therapy trend",
      question: "How did MSC therapy output grow across Indonesian institutions?",
    },
    {
      label: "Rahman collaboration",
      question: "Which Rahman collaborates with Bandung labs?",
    },
    {
      label: "Topic evolution",
      question: "What topics emerged in Scopus publications after 2021?",
    },
  ],
};
