"""Gates for scripts/check_scoped_sql.py: a plan_node_id-keyed write is red."""

from __future__ import annotations

from pathlib import Path

from gate_tree import ROOT, load_script, write_tree

checker = load_script("check_scoped_sql")


def _tree(tmp_path: Path, sql: str) -> Path:
    body = f"import sqlalchemy as sa\n\n\nasync def f(s):\n    await s.execute(sa.text({sql}))\n"
    return write_tree(tmp_path, {"src/app/services/x.py": body})


def test_plan_node_id_alone_is_red(tmp_path: Path) -> None:
    """The T05b defect: UPDATE blocks ... WHERE plan_node_id = :n holds another course's block."""
    root = _tree(tmp_path, "\"UPDATE blocks SET status = 'held' WHERE plan_node_id = :n\"")
    checked, problems = checker.check(root)
    assert checked == 1
    assert len(problems) == 1 and "plan_node_id" in problems[0]


def test_same_statement_with_course_join_is_green(tmp_path: Path) -> None:
    """Scoping it through topics and modules to the course clears it."""
    sql = (
        "\"UPDATE blocks b SET status = 'held' FROM topics t JOIN modules m ON m.id = t.module_id\""
        '" WHERE b.topic_id = t.id AND m.course_id = :c AND b.plan_node_id = :n"'
    )
    assert checker.check(_tree(tmp_path, sql)) == (1, [])


def test_primary_key_keyed_is_green(tmp_path: Path) -> None:
    """A statement keyed by the row's own id is allowed."""
    assert checker.check(_tree(tmp_path, "\"UPDATE blocks SET status = 'x' WHERE id = :b\"")) == (
        1,
        [],
    )


def test_join_on_id_without_parameter_is_not_a_key(tmp_path: Path) -> None:
    """`x.id = y.block_id` between two tables is a join, not a scope."""
    sql = '"SELECT 1 FROM blocks b JOIN passages p ON p.id = b.block_id"'
    assert len(checker.check(_tree(tmp_path, sql))[1]) == 1


def test_concatenated_dynamic_sql_is_read(tmp_path: Path) -> None:
    """A '+' concatenation with a dynamic tail still has its constant head checked."""
    sql = '"SELECT status FROM blocks WHERE plan_node_id = :n" + extra()'
    assert len(checker.check(_tree(tmp_path, sql))[1]) == 1


def test_other_tables_are_ignored(tmp_path: Path) -> None:
    """Statements on unguarded tables do not count."""
    assert checker.check(_tree(tmp_path, '"SELECT 1 FROM users WHERE email = :e"')) == (0, [])


def test_real_tree_is_clean() -> None:
    """The committed tree has no unscoped statement and a non-trivial count."""
    checked, problems = checker.check(ROOT)
    assert problems == []
    assert checked > 40
