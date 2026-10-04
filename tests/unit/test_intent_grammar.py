"""Regression tests for the shared SQLRoute intent grammar (W1).

The defect this file exists to prevent: ``QuestionRouter.SQL_PATTERNS`` and
``SqlRetriever.INTENT_PATTERNS`` were two independent copies of one grammar and
diverged in both directions, silently and repeatedly.

    * A template with no route pattern is unreachable. "Top 5 institutions" had
      a bounded template (88 ms) but matched no router pattern, so it went to
      VectorRoute, while the singular "Top institution" went to SQLRoute.
    * A route with no template converts a bounded query into an unbounded LLM
      call. "Berapa publikasi?" matched a route pattern and no template, costing
      6.0 s warm and 21.9-41.4 s cold instead of 84-90 ms.

Neither direction raised. Both returned HTTP 200 with a plausible answer.

``parity_violations()`` is the mechanical guard. It caught six further
divergences the day it was written, which is why it exists rather than a
handful of assertions: the specific phrasings below are evidence, the checker
is the actual defence.
"""

from __future__ import annotations

import pytest

from backend.app.services.intent_grammar import (
    INTENT_PARITY_PHRASES,
    INTENT_SPECS,
    coverage_report,
    is_sql_route_intent,
    match_intent,
    parity_violations,
)
from backend.app.services.router import QuestionRouter
from backend.app.services.retrievers.sql_retriever import SqlRetriever


class TestGrammarParity:
    def test_no_parity_violations(self):
        """Every canonical phrasing must agree between router and template."""
        assert parity_violations() == []

    def test_every_intent_is_reachable(self):
        """An intent with no route pattern has an unreachable template."""
        report = coverage_report()
        assert report["intents"]
        for spec in INTENT_SPECS:
            assert spec.route_patterns, f"{spec.name} has no route pattern"

    def test_intent_names_are_unique(self):
        names = [s.name for s in INTENT_SPECS]
        assert len(names) == len(set(names))

    @pytest.mark.parametrize("question,expected", INTENT_PARITY_PHRASES)
    def test_parity_phrase(self, question, expected):
        assert match_intent(question) == expected
        assert is_sql_route_intent(question), (
            f"{question!r} matched intent {expected!r} but no SQLRoute trigger fired"
        )


class TestRouteAlwaysHasATemplate:
    """The expensive direction: SQLRoute with no bounded template.

    This is what turns a sub-100 ms deterministic query into a 6-21 s Ollama
    Text-to-SQL call on the synchronous request path.
    """

    @pytest.mark.parametrize(
        "question",
        [
            "Berapa publikasi?",
            "Berapa jumlah publikasi tahun 2025?",
            "total publikasi",
            "top institutions",
            "Top 5 institutions",
            "Top 5 institution",
            "institutions teratas",
            "top 5 researchers by publication count",
            "penulis paling banyak disitasi",
            "List publications in 2025",
            "Which author has the highest publication count?",
            "Which author has the most citations?",
            "average citation count per publication",
            "Berapa grant yang diberikan?",
        ],
    )
    def test_sql_route_implies_a_template(self, question):
        assert QuestionRouter.classify_route(question, None).route == "SQLRoute"
        assert SqlRetriever.detect_intent(question) is not None, (
            f"{question!r} routes to SQLRoute with no deterministic template, so it "
            "would reach Ollama Text-to-SQL"
        )
        sql, _ = SqlRetriever.generate_deterministic_sql(question)
        assert sql is not None


class TestRegressionsFoundByTheParityChecker:
    """Each of these was a real divergence, found mechanically not by inspection."""

    def test_berapakah_grouping_rejects_plain_berapa(self):
        # The pattern was `berapakah?`, where `?` binds only to `h`, so it spelled
        # "berapah"/"berapakah" and REJECTED the plain "Berapa publikasi?" that the
        # router's literal `berapa` pattern happily routed to SQLRoute.
        assert match_intent("Berapa publikasi?") == "count_publications"

    def test_top_n_rank_allowed_between_top_and_noun(self):
        # The template had no `\\d*` between "top" and the noun, so "Top 5
        # institutions" matched nothing while "Top institutions" answered in 88 ms.
        assert match_intent("Top 5 institutions") == "top_institutions"
        assert match_intent("Top 5 institution") == "top_institutions"
        assert match_intent("top institutions") == "top_institutions"

    def test_articles_matches_indonesian_artikel(self):
        # The route pattern listed `articles?`, which matches "article" but not
        # the Indonesian "artikel", so a live template literal was unreachable.
        assert match_intent("artikel pada tahun") == "list_publications"
        assert is_sql_route_intent("artikel pada tahun")

    def test_most_cited_plural_routes_as_well_as_templates(self):
        # The template tolerated "citations" but the route pattern only matched
        # "cited", so the question got a correct intent and still went to
        # VectorRoute - the same class of bug the module exists to prevent.
        assert match_intent("most citations") == "most_cited"
        assert is_sql_route_intent("most citations")

    def test_experts_plural_is_recognised(self):
        # `\\bexpert\\b` does not match "experts" (trailing \\b needs a non-word
        # char). HybridRetriever.detect_intent therefore classified
        # "...and who are the experts?" as TOPIC_TRENDS and never queried the
        # expert half of the question.
        from backend.app.services.retrievers.hybrid_retriever import HybridRetriever

        assert HybridRetriever.detect_intent(
            "What topics are emerging in stem cell research after 2020 and who are the experts?"
        ) == "COMBINED_ANALYTICS"

    def test_author_paling_produktif_routes(self):
        # The route pattern covered "penulis paling produktif" but not the English
        # "author paling produktif", whose template existed.
        assert match_intent("author paling produktif") == "top_authors"
        assert is_sql_route_intent("author paling produktif")

    def test_avg_citation_does_not_swallow_per_year_grouping(self):
        """A template must not claim a question it answers differently.

        "Berapa rata-rata sitasi per tahun?" asks for a PER-YEAR breakdown. An
        earlier pattern accepted it and returned a single overall AVG - a
        different question that looked like a clean deterministic hit. Grouped
        aggregates must stay on the Text-to-SQL path, which can emit GROUP BY.
        """
        assert match_intent("Berapa rata-rata sitasi per publikasi?") == (
            "avg_citation_per_publication"
        )
        assert SqlRetriever.detect_intent("Berapa rata-rata sitasi per tahun?") is None
        # ...and the question still routes to SQLRoute, because it is relational.
        assert QuestionRouter.classify_route(
            "Berapa rata-rata sitasi per tahun?", None
        ).route == "SQLRoute"


class TestNonSqlRoutesUnaffected:
    @pytest.mark.parametrize(
        "question,route",
        [
            ("Who collaborates with Universitas Andalas?", "GraphRoute"),
            # Previously matched nothing and fell through to VectorRoute, which
            # cannot answer a collaboration question at all.
            ("Siapa yang sudah bekerja dengan Universitas Indonesia?", "GraphRoute"),
            ("What research discusses oxidative stress in stem cell therapy?", "VectorRoute"),
        ],
    )
    def test_route(self, question, route):
        assert QuestionRouter.classify_route(question, None).route == route

    def test_english_topic_evolution_reaches_hybrid(self):
        """Previously required an Indonesian topic noun, so this fell to Vector."""
        for q in (
            "How has research on Mesenchymal Stem Cells & Inflammation evolved over time?",
            "evolution of Artificial Intelligence & Benchmark Datasets over time",
        ):
            assert QuestionRouter.classify_route(q, None).route == "HybridRoute"

    def test_top_topics_is_hybrid_not_sql(self):
        """Routing "top institutions" to SQLRoute must not drag topics with it."""
        assert QuestionRouter.classify_route("top topics in stem cell research", None).route == (
            "HybridRoute"
        )