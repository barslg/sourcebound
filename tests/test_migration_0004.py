"""Migration 0004 round-trip on the isolated test database (downgrade first).

0004 owns the T03 course model, library, and run additions: course
groups, courses, modules, topics, blocks, block versions, course
versions, methodologies, course profiles with typed constraint rows,
immutable sources with parser specs, addressed passages with FTS,
citations, retrieval pairs, web/source candidates plus the human
queue, run DAG/estimate/stop-marker columns, step keys with
input digests, and the per-actor operation idempotency key.

Upgrade must add them; downgrade must remove exactly those additions
and restore runs/run_steps/operations to their 0003 shape.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from conftest import require_postgres, test_database_url
from test_migration_0001 import _harness_tables


def _cfg(url: str) -> Config:
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


_ADDED_TABLES = frozenset(
    {
        "course_groups",
        "courses",
        "modules",
        "topics",
        "blocks",
        "block_versions",
        "course_versions",
        "methodologies",
        "course_profiles",
        "profile_constraints",
        "sources",
        "passages",
        "citations",
        "retrieval_pairs",
        "source_candidates",
        "human_queue",
    }
)


def test_0004_upgrade_adds_course_library_and_run_columns() -> None:
    url = test_database_url()
    require_postgres(url)
    command.upgrade(_cfg(url), "head")
    tables = _harness_tables(url)
    assert _ADDED_TABLES <= set(tables)
    assert {"owner_id", "title", "language", "status", "scope_tags", "row_version"} <= tables[
        "courses"
    ]
    assert {"course_id", "ordinal", "entry_knowledge", "exit_knowledge"} <= tables["modules"]
    assert {"kind", "plan_node_id", "through_line_note", "plan_digest"} <= tables["topics"]
    assert {"plan_node_id", "brief", "current_version_id", "status", "hold_reason"} <= tables[
        "blocks"
    ]
    assert {"content", "answer_key", "schema_version", "run_step_id"} <= tables["block_versions"]
    assert {"content_sha256", "licence", "approval", "parser_spec", "supersedes_id"} <= tables[
        "sources"
    ]
    assert {"address", "address_key", "ts_config", "tsv"} <= tables["passages"]
    assert {"passage_id", "claim_path", "quote_start", "quote_end", "verified_by"} <= tables[
        "citations"
    ]
    assert {"query_lang", "ranker", "rank", "score", "label", "label_source"} <= tables[
        "retrieval_pairs"
    ]
    assert {"compiled_dag", "dag_digest", "prompt_snapshot", "estimate", "stop_marker"} <= tables[
        "runs"
    ]
    assert {"step_key", "depends_on", "input_digest", "output", "attempt"} <= tables["run_steps"]
    assert "idempotency_key" in tables["operations"]


def test_0004_downgrade_removes_only_t03_additions() -> None:
    url = test_database_url()
    require_postgres(url)
    command.upgrade(_cfg(url), "head")
    command.downgrade(_cfg(url), "0003")
    tables = _harness_tables(url)
    for removed in _ADDED_TABLES:
        assert removed not in tables
    assert "compiled_dag" not in tables["runs"]
    assert "step_key" not in tables["run_steps"]
    assert "idempotency_key" not in tables["operations"]
    command.upgrade(_cfg(url), "head")
    engine = sa.create_engine(url.replace("postgresql+asyncpg://", "postgresql://", 1))
    try:
        inspector = sa.inspect(engine)
        assert "courses" in inspector.get_table_names()
    finally:
        engine.dispose()
