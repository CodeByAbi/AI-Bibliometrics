"""Unit tests: the Session Isolation Invariant is enforced in code, not by convention.

Docs Reference: docs/03 §0.3 invariant 5, AC-SESSION-2/3/7/12/16.

Every test here runs without a database. That is the point: these are structural
properties of the import graph and the configuration, so they must hold on a
laptop with no database at all. A boundary that is only verifiable against a live
database is a boundary that silently rots between deployments.
"""

from __future__ import annotations

import ast
import inspect
import pathlib

import pytest

from backend.app.core.config import Settings
from backend.app.models.session import SCOPE_INHERITABLE_KEYS
from backend.app.services import session_repository, session_service
from backend.app.services.retrievers.sql_security import ALLOWED_COLUMNS, ALLOWED_TABLES

SESSION_TABLE_NAMES = {
    "research_sessions",
    "research_messages",
    "research_session_summaries",
}

#: Canonical bibliometric tables. A session module naming any of these is a
#: boundary violation regardless of whether the SQL would have worked.
BIBLIOMETRIC_TABLES = {
    "publications",
    "authors",
    "institutions",
    "keywords",
    "funding",
    "pub_author",
    "pub_institution",
    "publication_references",
    "chunks",
    "institution_collaboration",
    "author_collaboration",
    "topics",
    "topic_evolution",
    "researcher_expertise",
}


def _module_source(module) -> str:
    path = pathlib.Path(inspect.getfile(module))
    return path.read_text(encoding="utf-8")


def _imported_modules(module) -> set[str]:
    """Top-level module names imported by ``module``, from its AST."""
    tree = ast.parse(_module_source(module))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


#: Words that mark a string literal as an SQL statement rather than prose.
SQL_MARKERS = (
    "SELECT ",
    "INSERT INTO",
    "UPDATE ",
    "DELETE FROM",
    " FROM ",
    " JOIN ",
    "information_schema",
)


def _docstring_nodes(tree: ast.AST) -> set[int]:
    """ids() of Constant nodes that are docstrings.

    A docstring can legitimately discuss the corpus — this module's own
    docstring warns against writing "1,238 publications" into a summary, and that
    warning must not be mistaken for a query. Docstrings are never executed, so
    they are excluded from the SQL scan.
    """
    ids: set[int] = set()

    def add_expr(node: ast.AST) -> None:
        if (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            ids.add(id(node.value))

    body = getattr(tree, "body", [])
    if body:
        add_expr(body[0])  # module docstring
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            # The docstring is the first statement INSIDE the body, not the node.
            inner = getattr(node, "body", None)
            if inner:
                add_expr(inner[0])
    return ids


def _sql_constants(module) -> list[str]:
    """Non-docstring string literals in ``module`` that look like SQL.

    Scoped to string CONSTANTS rather than the whole file so a docstring may
    legitimately discuss what the module must NOT do without tripping a naive
    substring scan. A docstring is not a query; an executed string literal is.
    """
    tree = ast.parse(_module_source(module))
    docstrings = _docstring_nodes(tree)
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) in docstrings:
                continue
            if any(marker in node.value.upper() for marker in SQL_MARKERS):
                out.append(node.value)
    return out


def _sql_bibliometric_references(module) -> list[str]:
    """SQL literals in ``module`` that reference a bibliometric table."""
    hits: list[str] = []
    for literal in _sql_constants(module):
        upper = literal.upper()
        for table in BIBLIOMETRIC_TABLES:
            if table.upper() in upper:
                hits.append(f"{module.__name__}: {table!r}")
    return hits


class TestSqlWhitelistExcludesSessionTables:
    """AC-SESSION-2 / Implementation Rule 6.

    `ALLOWED_TABLES` is the gate LLM-generated Text-to-SQL must pass. Session
    tables are absent, so generated SQL cannot read the transcript even if a
    prompt-injected publication title asks it to.
    """

    def test_no_session_table_is_whitelisted(self) -> None:
        assert not (ALLOWED_TABLES & SESSION_TABLE_NAMES)

    def test_no_session_table_has_whitelisted_columns(self) -> None:
        assert not (set(ALLOWED_COLUMNS) & SESSION_TABLE_NAMES)

    def test_whitelist_still_exactly_the_bibliometric_set(self) -> None:
        """Guards against the whitelist being widened to make a session query
        'work'. Any addition must be a deliberate, reviewed act."""
        assert ALLOWED_TABLES == BIBLIOMETRIC_TABLES

    def test_session_table_names_absent_from_retriever_package(self) -> None:
        """No retriever module may name a session table in SQL or prose."""
        pkg = pathlib.Path(session_repository.__file__).parent / "retrievers"
        offenders: list[str] = []
        for py in sorted(pkg.glob("*.py")):
            text = py.read_text(encoding="utf-8")
            for table in SESSION_TABLE_NAMES:
                if table in text:
                    offenders.append(f"{py.name}:{table}")
        assert not offenders, offenders


class TestSessionLayerNeverTouchesBibliometricTables:
    """The repository boundary the spec asks to be visible in code."""

    @pytest.mark.parametrize(
        "module", [session_repository, session_service], ids=["repository", "service"]
    )
    def test_does_not_import_retrievers(self, module) -> None:
        imports = _imported_modules(module)
        leaks = {m for m in imports if "retrievers" in m or "vector_retriever" in m}
        assert not leaks, f"{module.__name__} imports retrieval code: {leaks}"

    @pytest.mark.parametrize(
        "module", [session_repository, session_service], ids=["repository", "service"]
    )
    def test_does_not_import_evidence_or_synthesizer(
        self, module
    ) -> None:
        imports = _imported_modules(module)
        leaks = {m for m in imports if "evidence" in m or "synthesizer" in m}
        assert not leaks, f"{module.__name__} imports evidence/synthesis: {leaks}"

    @pytest.mark.parametrize(
        "module", [session_repository, session_service], ids=["repository", "service"]
    )
    def test_does_not_import_the_readonly_retrieval_pool(
        self, module
    ) -> None:
        """The session write path must not borrow the retrieval connection.

        Sharing one pool would put a write-capable handle on the connection that
        runs Text-to-SQL against `publications`.
        """
        imports = _imported_modules(module)
        assert "backend.app.db.pool" not in imports
        assert not any(m.endswith("db.pool") for m in imports)

    @pytest.mark.parametrize(
        "module", [session_repository, session_service], ids=["repository", "service"]
    )
    def test_no_sql_literal_references_a_bibliometric_table(self, module) -> None:
        hits = _sql_bibliometric_references(module)
        assert not hits, hits

    def test_repository_only_names_session_tables(self) -> None:
        """Every ``_q("...")`` in the repository must be a session table."""
        tree = ast.parse(_module_source(session_repository))
        qualified: set[str] = set()
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "_q"
                and node.args
                and isinstance(node.args[0], ast.Constant)
            ):
                qualified.add(node.args[0].value)
        assert qualified == SESSION_TABLE_NAMES, qualified


class TestSchemaBoundary:
    """A separate schema is what makes session tables unreachable from retrieval."""

    def test_retrieval_pool_still_pins_search_path_to_public(self) -> None:
        from backend.app.db.pool import create_pool

        src = inspect.getsource(create_pool)
        assert '"search_path": "public"' in src

    def test_session_pool_pins_the_session_schema(self) -> None:
        from backend.app.db.session_pool import create_session_pool

        src = inspect.getsource(create_session_pool)
        assert '"search_path": schema' in src

    def test_session_schema_must_not_be_public(self) -> None:
        """Rejecting `public` at config time is the invariant's enforcement point."""
        for banned in ("public", "PUBLIC", " information_schema ", "pg_catalog"):
            with pytest.raises(ValueError):
                Settings(session_schema=banned)

    def test_session_schema_must_be_a_plain_identifier(self) -> None:
        for bad in ("app; DROP TABLE publications", "app-public", "1app", ""):
            with pytest.raises(ValueError):
                Settings(session_schema=bad)

    def test_default_session_schema_is_app(self) -> None:
        assert Settings().session_schema == "app"

    def test_retrieval_and_session_schemas_are_distinct_by_default(self) -> None:
        s = Settings()
        assert s.session_schema != "public"


class TestCredentialBoundary:
    """AC-SESSION-16: two pools, two roles, two DSNs."""

    def test_session_persistence_enabled_derived_from_dsn(self) -> None:
        """Derived, not a separate flag, so the flag and the DSN cannot disagree."""
        assert Settings(db_url_session="").session_persistence_enabled is False
        assert Settings(db_url_session="postgresql://x:y@h/db").session_persistence_enabled

    def test_session_dsn_defaults_to_empty(self) -> None:
        assert Settings().db_url_session == ""

    def test_session_dsn_is_independent_of_biblio_dsn(self) -> None:
        s = Settings(db_url="postgresql://readonly@h/db")
        assert s.session_persistence_enabled is False

    def test_grant_script_asserts_both_directions(self) -> None:
        """The isolation check must be bidirectional, not just "app_session is
        scoped". A session role that can read `public` is the leak that bites."""
        import importlib.util

        root = pathlib.Path(__file__).resolve().parents[2]
        spec = importlib.util.spec_from_file_location(
            "grant_session_role", root / "scripts" / "grant_session_role.py"
        )
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        assert set(mod.MUST_NOT_REACH_APP) >= {"app_readonly"}
        assert set(mod.MUST_NOT_REACH_PUBLIC) >= {"app_session"}
        # The session grant is narrower than ALL: no TRUNCATE, REFERENCES, TRIGGER.
        assert set(mod.SESSION_TABLE_PRIVILEGES) == {
            "SELECT",
            "INSERT",
            "UPDATE",
            "DELETE",
        }
        assert "TRUNCATE" not in mod.SESSION_TABLE_PRIVILEGES


class TestProvenanceIsNotEvidence:
    """AC-SESSION-7: applied_filters is scope, never a fact."""

    def test_sanitize_filters_drops_non_allowlisted_keys(self) -> None:
        clean = session_repository._sanitize_filters(
            {"country": "indonesia", "publication_count": 999999, "evidence": "x"}
        )
        assert clean == {"country": "indonesia"}

    def test_sanitize_filters_drops_non_primitive_values(self) -> None:
        clean = session_repository._sanitize_filters({"country": {"nested": "obj"}})
        assert isinstance(clean["country"], str)

    def test_sanitize_filters_returns_none_when_empty(self) -> None:
        assert session_repository._sanitize_filters(None) is None
        assert session_repository._sanitize_filters({}) is None
        assert session_repository._sanitize_filters({"year": 2024}) is None

    def test_every_allowlisted_key_exists_on_filterparams(self) -> None:
        """An allow-listed scope key with no FilterParams counterpart would be
        merged into a field nothing reads — silent no-op scope."""
        from backend.app.models.ask import FilterParams

        assert set(FilterParams.model_fields) >= SCOPE_INHERITABLE_KEYS


class TestMergePrecedence:
    """Explicit filters beat inherited scope, always."""

    def _service(self):
        return session_service.SessionService.__new__(
            session_service.SessionService
        )

    def test_explicit_filter_wins_over_scope(self) -> None:
        from backend.app.models.ask import FilterParams
        from backend.app.models.session import ConversationScope

        svc = self._service()
        explicit = FilterParams(country="malaysia")
        scope = ConversationScope(country="indonesia", year_from=2020)
        merged, applied = svc.effective_filters(explicit, scope)
        assert merged.country == "malaysia"
        assert merged.year_from == 2020
        assert applied == ["year_from"]

    def test_empty_scope_returns_explicit_untouched(self) -> None:
        from backend.app.models.ask import FilterParams
        from backend.app.models.session import ConversationScope

        svc = self._service()
        explicit = FilterParams(country="jerman")
        merged, applied = svc.effective_filters(explicit, ConversationScope())
        assert merged == explicit
        assert applied == []

    def test_derived_scope_uses_most_recent_value(self) -> None:
        """Scope moves: a later turn overriding an earlier one is honoured."""
        merged = session_service.merge_applied_filters(
            [{"country": "indonesia"}, {"country": "malaysia"}]
        )
        assert merged == {"country": "malaysia"}

    def test_merge_ignores_non_allowlisted_keys(self) -> None:
        merged = session_service.merge_applied_filters(
            [{"country": "id", "publication_count": 5}]
        )
        assert merged == {"country": "id"}

    def test_merge_skips_empty_values(self) -> None:
        merged = session_service.merge_applied_filters(
            [{"country": "indonesia"}, {"country": ""}, {"topic_name": "ai"}]
        )
        assert merged == {"country": "indonesia", "topic_name": "ai"}

    def test_effective_filters_does_not_mutate_the_caller_object(self) -> None:
        """AskRequest is frozen; a shared-mutation bug here would leak scope from
        one request into the next."""
        from backend.app.models.ask import FilterParams
        from backend.app.models.session import ConversationScope

        svc = self._service()
        explicit = FilterParams()
        merged, _ = svc.effective_filters(explicit, ConversationScope(country="id"))
        assert explicit.country is None
        assert merged.country == "id"


class TestSummaryServiceImportsNothingBibliometric:
    """AC-SESSION-14: the summary renderer is not a synthesis engine."""

    def test_no_llm_or_evidence_import(self) -> None:
        from backend.app.services import session_summary_service as mod

        imports = _imported_modules(mod)
        leaks = {m for m in imports if "llm" in m or "evidence" in m or "ollama" in m}
        assert not leaks, leaks

    def test_no_sql_literal_references_a_bibliometric_table(self) -> None:
        from backend.app.services import session_summary_service as mod

        hits = _sql_bibliometric_references(mod)
        assert not hits, hits

    def test_docstrings_may_discuss_the_corpus_without_querying_it(self) -> None:
        """A prose warning about bibliometric numbers must not fail the suite.

        Documenting "never write '1,238 publications' here" is the point of the
        module; the SQL-literal check must not punish it.
        """
        from backend.app.services import session_summary_service as mod

        assert "publications" in _module_source(mod)
        assert _sql_bibliometric_references(mod) == []