"""Unit tests for scripts/visualize_graph.py (pure stages, no DB).

Covers graph construction (self-loop drop, duplicate merge, min_weight),
full-graph metrics, deterministic top-N filtering, layout selection, and
GraphML serialization. DB loader SQL is validated by live runs, not here.
"""

from __future__ import annotations

import networkx as nx

from scripts.visualize_graph import (
    build_graph,
    choose_layout,
    compute_metrics,
    filter_top_n,
    serialize_for_graphml,
    short_label,
)


def _records():
    return [
        {
            "node_a": "A",
            "node_b": "B",
            "weight": 3,
            "via_publication_ids": ["P1", "P2"],
        },
        {"node_a": "B", "node_b": "C", "weight": 1, "via_publication_ids": ["P2"]},
        {"node_a": "A", "node_b": "C", "weight": 2, "via_publication_ids": ["P3"]},
        {"node_a": "C", "node_b": "D", "weight": 1, "via_publication_ids": ["P4"]},
    ]


def _labels():
    return {"A": "Inst A", "B": "Inst B", "C": "Inst C", "D": "Inst D"}


class TestBuildGraph:
    def test_nodes_edges_and_provenance(self):
        graph, stats = build_graph(_records(), _labels())
        assert graph.number_of_nodes() == 4
        assert graph.number_of_edges() == 4
        assert graph["A"]["B"]["weight"] == 3
        assert graph["A"]["B"]["via_publication_ids"] == ["P1", "P2"]
        assert graph["A"]["B"]["via_count"] == 2
        assert graph.nodes["A"]["label"] == "Inst A"
        assert stats["dropped_self_loops"] == 0
        assert stats["merged_duplicates"] == 0

    def test_self_loop_dropped(self):
        recs = _records() + [
            {"node_a": "A", "node_b": "A", "weight": 5, "via_publication_ids": ["P9"]}
        ]
        graph, stats = build_graph(recs, _labels())
        assert stats["dropped_self_loops"] == 1
        assert not graph.has_edge("A", "A")

    def test_duplicate_merged_with_union_provenance(self):
        recs = _records() + [
            {"node_a": "A", "node_b": "B", "weight": 1, "via_publication_ids": ["P5"]}
        ]
        graph, stats = build_graph(recs, _labels())
        assert stats["merged_duplicates"] == 1
        assert graph.number_of_edges() == 4
        assert graph["A"]["B"]["weight"] == 3  # max kept
        assert graph["A"]["B"]["via_publication_ids"] == ["P1", "P2", "P5"]

    def test_min_weight_filters_edges(self):
        graph, stats = build_graph(_records(), _labels(), min_weight=2)
        assert stats["dropped_below_min_weight"] == 2
        assert graph.number_of_edges() == 2


class TestComputeMetrics:
    def test_full_graph_metrics(self):
        graph, _ = build_graph(_records(), _labels())
        rows, summary = compute_metrics(graph)
        assert summary["nodes"] == 4
        assert summary["edges"] == 4
        assert summary["components"] == 1
        assert summary["largest_component"] == 4
        by_id = {r["node_id"]: r for r in rows}
        assert by_id["A"]["degree"] == 2
        assert by_id["A"]["weighted_degree"] == 5
        assert by_id["D"]["degree"] == 1
        assert all(0.0 <= r["degree_centrality"] <= 1.0 for r in rows)
        assert all(r["component_id"] == 0 for r in rows)

    def test_disconnected_components_labeled(self):
        recs = [
            r for r in _records() if not (r["node_a"] == "C" and r["node_b"] == "D")
        ]
        graph, _ = build_graph(recs, _labels())
        _, summary = compute_metrics(graph)
        assert summary["components"] == 2
        assert summary["largest_component"] == 3

    def test_empty_graph(self):
        rows, summary = compute_metrics(nx.Graph())
        assert rows == [] and summary["nodes"] == 0


class TestFilterTopN:
    def test_top_n_ranking_and_tiebreak(self):
        graph, _ = build_graph(_records(), _labels())
        rows, _ = compute_metrics(graph)
        sub, kept = filter_top_n(graph, rows, top_n=2, rank_by="degree")
        # C degree 3 first; A/B tie at degree 2 -> tie-break by node_id; D degree 1 excluded
        assert kept == ["C", "A"]
        assert set(sub.nodes()) == {"C", "A"}
        assert sub.number_of_edges() == 1  # induced subgraph keeps C-A only

    def test_top_n_larger_than_graph(self):
        graph, _ = build_graph(_records(), _labels())
        rows, _ = compute_metrics(graph)
        sub, kept = filter_top_n(graph, rows, top_n=99, rank_by="degree")
        assert len(kept) == 4
        assert sub.number_of_edges() == 4


class TestLayoutAndGraphML:
    def test_auto_prefers_kamada_for_small_connected(self):
        graph, _ = build_graph(_records(), _labels())
        name, pos = choose_layout(graph, "auto", seed=42)
        assert name == "kamada"
        assert set(pos) == set(graph.nodes())

    def test_auto_falls_back_to_spring_when_disconnected(self):
        graph = nx.Graph()
        graph.add_nodes_from(["X", "Y"])
        name, pos = choose_layout(graph, "auto", seed=42)
        assert name == "spring"
        assert set(pos) == {"X", "Y"}

    def test_explicit_layout_honored(self):
        graph, _ = build_graph(_records(), _labels())
        name, _ = choose_layout(graph, "spectral", seed=42)
        assert name == "spectral"

    def test_graphml_serialization_has_no_lists(self):
        graph, _ = build_graph(_records(), _labels())
        safe = serialize_for_graphml(graph)
        for _, _, data in safe.edges(data=True):
            assert isinstance(data["via_publication_ids"], str)
            assert isinstance(data["via_count"], int)
            assert isinstance(data["weight"], int)
        assert safe["A"]["B"]["via_publication_ids"] == "P1;P2"


class TestShortLabel:
    def test_short_names_untouched(self):
        assert short_label("Inst A") == "Inst A"

    def test_long_names_truncated_with_ellipsis(self):
        long = "Department of Chemical Engineering, Faculty of Engineering, Universitas Gadjah Mada"
        out = short_label(long, limit=45)
        assert len(out) == 45 and out.endswith("…")
        assert out == long[:44] + "…"

    def test_whitespace_collapsed(self):
        assert short_label("A  B\n C") == "A B C"
