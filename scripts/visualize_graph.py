"""Bibliometric Collaboration Graph Visualization (exploration/analytics layer).

Builds an undirected NetworkX graph from the derived collaboration edge tables
(`institution_collaboration`, `author_collaboration`), computes node-level
metrics, renders a restrained matplotlib visualization of the top-N induced
subgraph, and exports full-graph metrics (CSV) plus a Gephi-readable GraphML.

This script is an analytics/exploration layer only. It does NOT replace
`GraphRetriever` (parameterized recursive CTEs over the same edge tables)
and it performs SELECT-only reads via the `app_readonly` role (`DB_URL`).
No schema change, no new columns — node labels come from JOINs to the
canonical `institutions` / `authors` tables.

Layer separation (each stage is a pure function, no mixed SQL+plot code):
    database (SELECT-only)
      -> load_institution_edges() / load_author_edges()   [records + labels]
      -> build_graph()                                    [nx.Graph]
      -> compute_metrics()                                [metrics rows]
      -> filter_top_n()                                   [induced subgraph]
      -> choose_layout() + render_graph()                 [PNG]
      -> export_metrics_csv() + export_graphml()          [CSV + GraphML]

Large-graph policy: full-graph statistics are always computed and exported;
only the *visualization* shows the top-N induced subgraph, explicitly
annotated ("Top N of M nodes") so nothing is silently truncated.

Usage:
    python scripts/visualize_graph.py --type institution --top-n 30
    python scripts/visualize_graph.py --type author --top-n 30 --layout kamada
    python scripts/visualize_graph.py --type both --top-n 50 --rank-by weighted_degree
"""

from __future__ import annotations

import argparse
import csv
import logging
import pathlib
import sys

# Ensure project root is in sys.path (repo convention, cf. build_edges.py)
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.db import get_db_connection

try:
    import networkx as nx
except ImportError:
    print(
        "ERROR: networkx is required (pip install -r requirements.txt).",
        file=sys.stderr,
    )
    sys.exit(1)

import matplotlib

matplotlib.use("Agg")  # headless-safe: never open an interactive window
import matplotlib.pyplot as plt

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("visualize_graph")

DEFAULT_SEED = 42
ACCENT = "#2563EB"  # docs/07 accent token; single-hue, no rainbow
EDGE_COLOR = "#9CA3AF"

INSTITUTION_SQL = """
    SELECT ic.institution_a, ic.institution_b, ic.weight, ic.via_publication_ids,
           ia.institution_name AS name_a, ib.institution_name AS name_b
      FROM institution_collaboration ic
      JOIN institutions ia ON ia.institution_id = ic.institution_a
      JOIN institutions ib ON ib.institution_id = ic.institution_b
     ORDER BY ic.weight DESC, ic.institution_a, ic.institution_b;
"""

AUTHOR_SQL = """
    SELECT ac.author_a, ac.author_b, ac.weight, ac.via_publication_ids,
           aa.author_name AS name_a, ab.author_name AS name_b
      FROM author_collaboration ac
      JOIN authors aa ON aa.author_id = ac.author_a
      JOIN authors ab ON ab.author_id = ac.author_b
     ORDER BY ac.weight DESC, ac.author_a, ac.author_b;
"""


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Visualize institution/author collaboration graphs from derived edge tables."
    )
    parser.add_argument(
        "--type",
        choices=("institution", "author", "both"),
        default="institution",
        help="Which collaboration graph to visualize (default: institution)",
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=30,
        help="Nodes kept in the visualized induced subgraph, ranked by --rank-by (default: 30)",
    )
    parser.add_argument(
        "--rank-by",
        choices=("degree", "weighted_degree", "betweenness"),
        default="degree",
        help="Ranking key for top-N filtering and label selection (default: degree)",
    )
    parser.add_argument(
        "--layout",
        choices=("auto", "spring", "kamada", "spectral"),
        default="auto",
        help="NetworkX layout. 'auto': kamada_kawai for small connected graphs, "
        "spring otherwise (default: auto)",
    )
    parser.add_argument(
        "--min-weight",
        type=int,
        default=1,
        help="Drop edges with weight below this threshold before analysis (default: 1)",
    )
    parser.add_argument(
        "--out-dir",
        default="outputs",
        help="Output directory for PNG/CSV/GraphML files (default: outputs)",
    )
    parser.add_argument(
        "--community",
        action="store_true",
        help="Also run greedy modularity community detection; stored as a separate "
        "community_id column (never mixed into metrics)",
    )
    parser.add_argument(
        "--no-graphml",
        action="store_true",
        help="Skip GraphML export (default: GraphML is exported)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=DEFAULT_SEED,
        help="Random seed for spring/spectral layouts (default: 42)",
    )
    return parser


def parse_args() -> argparse.Namespace:
    return build_parser().parse_args()


def parse_args_from(argv: list[str]) -> argparse.Namespace:
    """Parse an explicit argv list (test entry point, no sys.argv side effects)."""
    return build_parser().parse_args(argv)


# ---------------------------------------------------------------------------
# Stage 1: loaders (SQL + JOIN labels only)
# ---------------------------------------------------------------------------


def load_edges(cur, kind: str) -> tuple[list[dict], dict[str, str]]:
    """Load edge records plus an id->label map for one graph kind.

    Returns (records, labels) where each record holds node_a, node_b,
    weight, via_publication_ids (list, provenance preserved).
    """
    sql = INSTITUTION_SQL if kind == "institution" else AUTHOR_SQL
    cur.execute(sql)
    records: list[dict] = []
    labels: dict[str, str] = {}
    for node_a, node_b, weight, via_pubs, name_a, name_b in cur.fetchall():
        records.append(
            {
                "node_a": node_a,
                "node_b": node_b,
                "weight": int(weight),
                "via_publication_ids": list(via_pubs or []),
            }
        )
        labels.setdefault(node_a, name_a or node_a)
        labels.setdefault(node_b, name_b or node_b)
    logger.info(
        "Loaded %d %s edge records (%d labeled nodes)", len(records), kind, len(labels)
    )
    return records, labels


# ---------------------------------------------------------------------------
# Stage 2: graph construction
# ---------------------------------------------------------------------------


def build_graph(
    records: list[dict], labels: dict[str, str], min_weight: int = 1
) -> tuple[nx.Graph, dict]:
    """Build an undirected graph; drop self-loops, merge duplicate edges.

    The DB schema (CHECK a<b) already forbids self-loops and symmetric
    duplicates, so both branches below are defensive — any hit is logged.
    """
    dropped_self_loops = 0
    merged_duplicates = 0
    dropped_light = 0
    graph = nx.Graph()
    for node_id, label in sorted(labels.items()):
        graph.add_node(node_id, label=label)
    for rec in records:
        a, b, w = rec["node_a"], rec["node_b"], rec["weight"]
        if w < min_weight:
            dropped_light += 1
            continue
        if a == b:
            dropped_self_loops += 1
            continue
        via = list(rec.get("via_publication_ids") or [])
        if graph.has_edge(a, b):
            merged_duplicates += 1
            prev = graph[a][b]
            prev_provenance = prev.get("via_publication_ids", [])
            merged = sorted(set(prev_provenance) | set(via))
            prev["weight"] = max(prev["weight"], w)
            prev["via_publication_ids"] = merged
            prev["via_count"] = len(merged)
        else:
            if a not in graph:
                graph.add_node(a, label=labels.get(a, a))
            if b not in graph:
                graph.add_node(b, label=labels.get(b, b))
            graph.add_edge(
                a,
                b,
                weight=w,
                via_publication_ids=sorted(set(via)),
                via_count=len(set(via)),
            )
    stats = {
        "dropped_self_loops": dropped_self_loops,
        "merged_duplicates": merged_duplicates,
        "dropped_below_min_weight": dropped_light,
    }
    if dropped_self_loops:
        logger.warning(
            "Dropped %d self-loop(s) (unexpected: schema forbids a==b)",
            dropped_self_loops,
        )
    if merged_duplicates:
        logger.warning(
            "Merged %d duplicate edge(s) (unexpected: PK(a,b) forbids these)",
            merged_duplicates,
        )
    logger.info(
        "Built graph: %d nodes, %d edges (min_weight=%d, light-dropped=%d)",
        graph.number_of_nodes(),
        graph.number_of_edges(),
        min_weight,
        dropped_light,
    )
    return graph, stats


# ---------------------------------------------------------------------------
# Stage 3: metrics (graph metric vs community output kept separate)
# ---------------------------------------------------------------------------


def compute_metrics(
    graph: nx.Graph, with_community: bool = False
) -> tuple[list[dict], dict]:
    """Compute per-node metrics on the FULL graph plus a summary dict.

    Betweenness is intentionally unweighted: co-authorship `weight` counts
    shared publications (strength), not a distance, so weight-as-distance
    would invert its meaning. Documented here so the choice is explicit.
    """
    if graph.number_of_nodes() == 0:
        return [], {
            "nodes": 0,
            "edges": 0,
            "components": 0,
            "largest_component": 0,
            "avg_degree": 0.0,
        }
    degree = dict(graph.degree())
    weighted_degree = dict(graph.degree(weight="weight"))
    degree_centrality = nx.degree_centrality(graph)
    betweenness_centrality = nx.betweenness_centrality(
        graph, normalized=True
    )  # unweighted, see docstring
    components = sorted(nx.connected_components(graph), key=len, reverse=True)
    comp_of = {}
    for cid, comp in enumerate(components):
        for node in comp:
            comp_of[node] = (cid, len(comp))
    community_of: dict = {}
    if with_community:
        from networkx.algorithms.community import greedy_modularity_communities

        for cid, comm in enumerate(greedy_modularity_communities(graph)):
            for node in comm:
                community_of[node] = cid
        logger.info(
            "Community detection: %d communities (greedy modularity)",
            len(set(community_of.values())) or 0,
        )
    rows: list[dict] = []
    for node in sorted(graph.nodes()):
        row = {
            "node_id": node,
            "label": graph.nodes[node].get("label", node),
            "degree": degree[node],
            "weighted_degree": weighted_degree[node],
            "degree_centrality": round(degree_centrality[node], 6),
            "betweenness_centrality": round(betweenness_centrality[node], 6),
            "component_id": comp_of[node][0],
            "component_size": comp_of[node][1],
        }
        if with_community:
            row["community_id"] = community_of.get(node, -1)
        rows.append(row)
    summary = {
        "nodes": graph.number_of_nodes(),
        "edges": graph.number_of_edges(),
        "components": len(components),
        "largest_component": len(components[0]) if components else 0,
        "avg_degree": round(sum(degree.values()) / len(degree), 3) if degree else 0.0,
    }
    return rows, summary


# ---------------------------------------------------------------------------
# Stage 4: top-N filtering (visualization only; full stats already exported)
# ---------------------------------------------------------------------------


def filter_top_n(
    graph: nx.Graph, rows: list[dict], top_n: int, rank_by: str
) -> tuple[nx.Graph, list[str]]:
    """Return the induced subgraph of the top-N nodes by rank_by.

    Deterministic tie-break on node_id. Never mutates the full graph.
    """
    if top_n <= 0:
        raise ValueError("--top-n must be positive")
    ranked = sorted(rows, key=lambda r: (-r[rank_by], r["node_id"]))
    kept = [r["node_id"] for r in ranked[:top_n]]
    sub = graph.subgraph(kept).copy()
    logger.info(
        "Filter: showing top %d of %d nodes by %s (%d edges in subgraph)",
        len(kept),
        graph.number_of_nodes(),
        rank_by,
        sub.number_of_edges(),
    )
    return sub, kept


# ---------------------------------------------------------------------------
# Stage 5: layout + rendering
# ---------------------------------------------------------------------------


def choose_layout(graph: nx.Graph, preference: str, seed: int) -> tuple[str, dict]:
    """Pick a layout, comparing the three candidates.

    - kamada_kawai: best path-length fidelity for small connected graphs
      (<=300 nodes); unreliable on disconnected graphs (infinite distances).
    - spring: general fallback, seeded for reproducibility, handles any shape.
    - spectral: highlights cluster separation, often less readable for labels.
    'auto' encodes that comparison: kamada_kawai when connected and small,
    spring otherwise. An explicit --layout overrides the heuristic.
    """
    n = graph.number_of_nodes()
    if preference != "auto":
        name = preference
    elif n <= 300 and n > 0 and nx.is_connected(graph):
        name = "kamada"
    else:
        name = "spring"
    if name == "kamada":
        pos = nx.kamada_kawai_layout(graph)
    elif name == "spectral":
        pos = nx.spectral_layout(graph)
    else:
        name = "spring"
        # Spread-out k: default 1/sqrt(n) collapses dense cliques (each
        # publication's institutions form a clique) into blobs; 3/sqrt(n)
        # keeps components separated while staying seeded/deterministic.
        pos = nx.spring_layout(graph, seed=seed, k=3.0 / max(n**0.5, 1))
    logger.info(
        "Layout: %s (nodes=%d, connected=%s)",
        name,
        n,
        nx.is_connected(graph) if n else False,
    )
    return name, pos


def short_label(label: str, limit: int = 45) -> str:
    """Truncate long affiliation strings so dense cliques stay readable."""
    label = " ".join(str(label).split())
    return label if len(label) <= limit else label[: limit - 1] + "…"


def render_graph(
    sub: nx.Graph,
    pos: dict,
    full_summary: dict,
    kind: str,
    rank_by: str,
    out_path: pathlib.Path,
) -> None:
    """Render the filtered subgraph with restrained visual encoding."""
    n = sub.number_of_nodes()
    metric = {
        node: (
            sub.degree(node)
            if rank_by == "degree"
            else sub.degree(node, weight="weight")
        )
        for node in sub.nodes()
    }
    if rank_by == "betweenness":
        btw = nx.betweenness_centrality(sub, normalized=True)
        metric = {node: btw[node] for node in sub.nodes()}
    vals = list(metric.values())
    lo, hi = min(vals, default=0), max(vals, default=0)
    span = (hi - lo) or 1
    node_sizes = [100 + 600 * (metric[node] - lo) / span for node in sub.nodes()]
    weights = [d.get("weight", 1) for _, _, d in sub.edges(data=True)]
    w_lo, w_hi = min(weights, default=1), max(weights, default=1)
    w_span = (w_hi - w_lo) or 1
    edge_widths = [0.5 + 2.5 * (w - w_lo) / w_span for w in weights]

    # Labels for the top-10 nodes only, truncated: full names live in the CSV.
    top_labeled = sorted(sub.nodes(), key=lambda x: (-metric[x], x))[: min(10, n)]
    labels = {
        node: short_label(sub.nodes[node].get("label", node)) for node in top_labeled
    }

    width = max(10.0, min(20.0, 6.0 + n * 0.35))
    _, ax = plt.subplots(figsize=(width, width * 0.75))
    ax.set_facecolor("white")
    plt.gcf().patch.set_facecolor("white")
    nx.draw_networkx_edges(
        sub, pos, ax=ax, width=edge_widths, edge_color=EDGE_COLOR, alpha=0.7
    )
    nx.draw_networkx_nodes(
        sub,
        pos,
        ax=ax,
        node_size=node_sizes,
        node_color=ACCENT,
        alpha=0.85,
        linewidths=0.5,
        edgecolors="white",
    )
    nx.draw_networkx_labels(
        sub, pos, labels, ax=ax, font_size=6.5, font_color="#111827"
    )
    title_metric = {
        "degree": "degree",
        "weighted_degree": "weighted degree",
        "betweenness": "betweenness",
    }[rank_by]
    ax.set_title(
        f"{kind.title()} Collaboration — Top {n} of {full_summary['nodes']} nodes "
        f"({sub.number_of_edges()} of {full_summary['edges']} edges, node size = {title_metric})",
        fontsize=11,
        color="#111827",
        pad=12,
    )
    ax.text(
        0.5,
        -0.02,
        f"Full-graph stats: {full_summary['nodes']} nodes, {full_summary['edges']} edges, "
        f"{full_summary['components']} components (largest {full_summary['largest_component']}). "
        "See metrics CSV for the complete ranking.",
        transform=ax.transAxes,
        ha="center",
        fontsize=7,
        color="#6B7280",
    )
    ax.axis("off")
    plt.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close()
    logger.info("Wrote %s", out_path)


# ---------------------------------------------------------------------------
# Stage 6: exports
# ---------------------------------------------------------------------------

METRIC_FIELDS = (
    "node_id",
    "label",
    "degree",
    "weighted_degree",
    "degree_centrality",
    "betweenness_centrality",
    "component_id",
    "component_size",
)


def export_metrics_csv(
    rows: list[dict], kept: list[str], out_path: pathlib.Path, with_community: bool
) -> None:
    kept_set = set(kept)
    fields = [*METRIC_FIELDS, "in_top_n"]
    if with_community:
        fields.append("community_id")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            out = {k: row.get(k) for k in fields if k != "in_top_n"}
            out["in_top_n"] = row["node_id"] in kept_set
            writer.writerow(out)
    logger.info("Wrote %s (%d rows)", out_path, len(rows))


def serialize_for_graphml(graph: nx.Graph) -> nx.Graph:
    """Copy the graph with GraphML-safe attributes (no list values)."""
    safe = nx.Graph()
    for node, data in graph.nodes(data=True):
        safe.add_node(node, label=str(data.get("label", node)))
    for a, b, data in graph.edges(data=True):
        via = data.get("via_publication_ids", [])
        safe.add_edge(
            a,
            b,
            weight=int(data.get("weight", 1)),
            via_count=int(
                data.get("via_count", len(via) if isinstance(via, list) else 0)
            ),
            via_publication_ids=";".join(via) if isinstance(via, list) else str(via),
        )
    return safe


def export_graphml(graph: nx.Graph, out_path: pathlib.Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    nx.write_graphml(serialize_for_graphml(graph), out_path)
    logger.info("Wrote %s", out_path)


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def run_kind(cur, kind: str, args: argparse.Namespace, out_dir: pathlib.Path) -> int:
    records, labels = load_edges(cur, kind)
    if not records:
        logger.error("No %s edges found — nothing to visualize", kind)
        return 1
    graph, _ = build_graph(records, labels, min_weight=args.min_weight)
    if graph.number_of_nodes() == 0:
        logger.error("Graph is empty after min_weight=%d filtering", args.min_weight)
        return 1
    rows, summary = compute_metrics(graph, with_community=args.community)
    logger.info(
        "Full %s graph: %d nodes, %d edges, %d components (largest %d), avg degree %.3f",
        kind,
        summary["nodes"],
        summary["edges"],
        summary["components"],
        summary["largest_component"],
        summary["avg_degree"],
    )
    top5 = sorted(rows, key=lambda r: (-r[args.rank_by], r["node_id"]))[:5]
    for row in top5:
        logger.info("  top: %s (%s=%s)", row["label"], args.rank_by, row[args.rank_by])
    sub, kept = filter_top_n(graph, rows, args.top_n, args.rank_by)
    _, pos = choose_layout(sub, args.layout, args.seed)
    render_graph(
        sub, pos, summary, kind, args.rank_by, out_dir / f"{kind}_collaboration.png"
    )
    export_metrics_csv(
        rows, kept, out_dir / f"{kind}_graph_metrics.csv", args.community
    )
    if not args.no_graphml:
        export_graphml(graph, out_dir / f"{kind}_collaboration.graphml")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args() if argv is None else parse_args_from(argv)
    kinds = ("institution", "author") if args.type == "both" else (args.type,)
    out_dir = pathlib.Path(args.out_dir)
    # SELECT-only via the read path (app_readonly role); never the owner DSN.
    with (
        get_db_connection(dsn_env="DB_URL", autocommit=True) as conn,
        conn.cursor() as cur,
    ):
        for kind in kinds:
            if run_kind(cur, kind, args, out_dir) != 0:
                return 1
    logger.info("Done. Outputs in %s", out_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
