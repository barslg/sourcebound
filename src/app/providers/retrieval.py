"""Retrieval provider: one Protocol, two adapters.

`fts` is the Postgres full-text baseline and the default; `reranker`
is a stub in T03 behind the same interface, implemented in T05 by a
fine-tuned Muse Glimmer trained on retrieval_pairs. No embedding
model of any kind: the hackathon rule allows only Meta models.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class RankedPassage:
    """One candidate passage with its code-computed rank."""

    passage_id: UUID
    source_id: UUID
    address_key: str
    text: str
    rank: int
    score: float | None
    ts_config: str = "english"


class RetrievalProvider(Protocol):
    """Full-text candidate retrieval over addressed passages."""

    name: str

    async def search(
        self,
        session: AsyncSession,
        *,
        query: str,
        ts_config: str,
        limit: int,
        course_id: UUID | None = None,
    ) -> list[RankedPassage]:
        """Return up to `limit` passages for the query, best first.

        When `course_id` is given, only approved passages of sources
        bound to that course with an approved binding are visible;
        without it the search stays global (unit tests, no owner).
        """
        ...

    async def search_course(
        self,
        session: AsyncSession,
        *,
        query: str,
        course_id: UUID,
        limit: int,
    ) -> list[RankedPassage]:
        """One query per distinct passage language, OR-ed together."""
        ...


def scoped_join(course_id: UUID | None) -> str:
    """Course + approved filter shared by every passage query."""
    if course_id is None:
        return ""
    return (
        " JOIN course_sources cs ON cs.source_id = passages.source_id"
        " AND cs.course_id = :course AND cs.approval = 'approved'"
        " JOIN sources s ON s.id = passages.source_id AND s.approval = 'approved'"
    )


class FtsRetriever:
    """Postgres full-text baseline: plainto_tsquery plus ts_rank.

    Multi-word queries first try AND semantics; when no passage holds
    every lexeme the adapter falls back to OR so a partial match still
    reaches the model for ranking instead of an empty list.
    """

    name = "fts"

    async def search(
        self,
        session: AsyncSession,
        *,
        query: str,
        ts_config: str,
        limit: int,
        course_id: UUID | None = None,
    ) -> list[RankedPassage]:
        rows = await self._search(
            session, query=query, ts_config=ts_config, limit=limit, course_id=course_id
        )
        if not rows and len(query.split()) > 1:
            rows = await self._search(
                session,
                query=query,
                ts_config=ts_config,
                limit=limit,
                union=True,
                course_id=course_id,
            )
        if not rows and ts_config == "simple":
            rows = await self._trigram(session, query=query, limit=limit, course_id=course_id)
        return [
            RankedPassage(
                passage_id=UUID(str(row[0])),
                source_id=UUID(str(row[1])),
                address_key=str(row[2]),
                text=str(row[3]),
                rank=position,
                score=float(row[5]),
                ts_config=str(row[4]),
            )
            for position, row in enumerate(rows, start=1)
        ]

    async def search_course(
        self,
        session: AsyncSession,
        *,
        query: str,
        course_id: UUID,
        limit: int,
    ) -> list[RankedPassage]:
        """One query per distinct passage language, OR-ed together.

        Passages carry their own ts_config (en english, nb norwegian,
        uk simple): a single-config query would silence every other
        language, so each distinct config of the course's approved
        passages is queried and the best score per passage wins.
        """
        configs = (
            await session.execute(
                sa.text(
                    "SELECT DISTINCT passages.ts_config FROM passages"
                    + scoped_join(course_id)
                    + " WHERE passages.ts_config IS NOT NULL"
                ),
                {"course": str(course_id)},
            )
        ).all()
        merged: dict[str, list[Any]] = {}
        for (config,) in configs:
            rows = await self._search(
                session, query=query, ts_config=str(config), limit=limit, course_id=course_id
            )
            if not rows and len(query.split()) > 1:
                rows = await self._search(
                    session,
                    query=query,
                    ts_config=str(config),
                    limit=limit,
                    union=True,
                    course_id=course_id,
                )
            for row in rows:
                key = str(row[0])
                if key not in merged or float(row[5]) > float(merged[key][5]):
                    merged[key] = list(row)
        if not merged and any(str(config) == "simple" for config, *_ in configs):
            for row in await self._trigram(session, query=query, limit=limit, course_id=course_id):
                merged.setdefault(str(row[0]), list(row))
        ordered = sorted(merged.values(), key=lambda row: float(row[5]), reverse=True)[:limit]
        return [
            RankedPassage(
                passage_id=UUID(str(row[0])),
                source_id=UUID(str(row[1])),
                address_key=str(row[2]),
                text=str(row[3]),
                rank=position,
                score=float(row[5]),
                ts_config=str(row[4]),
            )
            for position, row in enumerate(ordered, start=1)
        ]

    async def _search(
        self,
        session: AsyncSession,
        *,
        query: str,
        ts_config: str,
        limit: int,
        union: bool = False,
        course_id: UUID | None = None,
    ) -> list[Any]:
        matcher: str = (
            "replace(plainto_tsquery(CAST(:cfg AS regconfig), :q)::text, '&', '|')::tsquery"
            if union
            else "plainto_tsquery(CAST(:cfg AS regconfig), :q)"
        )
        params: dict[str, Any] = {"cfg": ts_config, "q": query, "limit": limit}
        if course_id is not None:
            params["course"] = str(course_id)
        rows = (
            await session.execute(
                sa.text(
                    "SELECT passages.id, passages.source_id, passages.address_key,"
                    " passages.text, passages.ts_config,"
                    f" ts_rank(passages.tsv, {matcher}) AS score"
                    " FROM passages" + scoped_join(course_id) + f" WHERE passages.tsv @@ {matcher}"
                    " ORDER BY score DESC LIMIT :limit"
                ),
                params,
            )
        ).all()
        return list(rows)

    async def _trigram(
        self,
        session: AsyncSession,
        *,
        query: str,
        limit: int,
        course_id: UUID | None = None,
    ) -> list[Any]:
        """pg_trgm fallback for `simple` (uk): similarity-ranked."""
        params: dict[str, Any] = {"q": query, "limit": limit}
        if course_id is not None:
            params["course"] = str(course_id)
        rows = (
            await session.execute(
                sa.text(
                    "SELECT passages.id, passages.source_id, passages.address_key,"
                    " passages.text, passages.ts_config,"
                    " similarity(passages.text, :q) AS score"
                    " FROM passages"
                    + scoped_join(course_id)
                    + " WHERE similarity(passages.text, :q) > 0.05"
                    " ORDER BY score DESC LIMIT :limit"
                ),
                params,
            )
        ).all()
        return list(rows)


class RerankerStub:
    """T05 Glimmer reranker placeholder: FTS order, stub ranker name.

    Implements the same interface so the researcher records
    retrieval_pairs with ranker='reranker_stub' from the first run;
    T05 replaces the body with the fine-tuned model.
    """

    name = "reranker_stub"

    def __init__(self, inner: RetrievalProvider | None = None) -> None:
        self._inner = inner or FtsRetriever()

    async def search(
        self,
        session: AsyncSession,
        *,
        query: str,
        ts_config: str,
        limit: int,
        course_id: UUID | None = None,
    ) -> list[RankedPassage]:
        return await self._inner.search(
            session, query=query, ts_config=ts_config, limit=limit, course_id=course_id
        )

    async def search_course(
        self,
        session: AsyncSession,
        *,
        query: str,
        course_id: UUID,
        limit: int,
    ) -> list[RankedPassage]:
        inner = self._inner
        search_course = getattr(inner, "search_course", None)
        if search_course is None:  # pragma: no cover - inner is always FTS here
            raise NotImplementedError("inner retriever has no search_course")
        result: list[RankedPassage] = await search_course(
            session, query=query, course_id=course_id, limit=limit
        )
        return result


RETRIEVERS: dict[str, RetrievalProvider] = {
    "fts": FtsRetriever(),
    "reranker": RerankerStub(),
}


def get_retriever(name: str) -> RetrievalProvider:
    """Return the named adapter; unknown names fail closed."""
    try:
        return RETRIEVERS[name]
    except KeyError:
        raise ValueError(f"unknown retriever: {name}") from None
