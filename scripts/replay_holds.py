"""Replay every code check over a stored course, offline, block by block.

A code check over generated content is not done until it has run over
a real course: this tool reads one course (SELECT only) and prints, per
block, what each check would hold today and why, plus the architect's
plan-level verdict. The checks live in `app.course.replay`; the product's
`POST /courses/{id}/held/recheck` lifts the holds this replay no longer finds.
Caller: `make replay-holds COURSE=<id>` (and the T11b report).

Usage: python scripts/replay_holds.py --course <uuid> [--database-url URL] [--json PATH]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any
from uuid import UUID

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))


async def replay(url: str, course_id: str) -> dict[str, Any]:
    """The service's replay over one course, on a standalone session (read-only)."""
    from app.db import create_session_factory
    from app.services.recheck import replay_course

    factory = create_session_factory(url)
    try:
        async with factory() as session:
            result = await replay_course(session, course_id=UUID(course_id))
            await session.rollback()
            return result
    finally:
        bind = factory.kw.get("bind")
        if bind is not None:
            await bind.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--course", required=True)
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL", ""))
    parser.add_argument("--json", default="")
    args = parser.parse_args()
    if not args.database_url:
        print("replay_holds: set --database-url or DATABASE_URL", file=sys.stderr)
        return 2
    result = asyncio.run(replay(args.database_url, args.course))
    held = 0
    for row in result["blocks"]:
        mark = "HOLD" if row["holds"] else "pass"
        held += bool(row["holds"])
        print(
            f"{mark:<4} {row['role']:<8} {row['type']:<17} {row['node']:<10}"
            f" {row['topic'][:28]:<28} cited={','.join(row['cited'])}"
        )
        for hold in row["holds"]:
            print(f"       - {hold[:160]}")
    print(
        f"replay_holds: {len(result['blocks'])} blocks, {held} would hold,"
        f" {result['passages']} passages, plan errors: {len(result['plan_errors'])}"
    )
    for error in result["plan_errors"]:
        print(f"  plan: {error[:200]}")
    if args.json:
        Path(args.json).write_text(json.dumps(result, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
