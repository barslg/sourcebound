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
    ) -> list[RankedPassage]:
        """Return up to `limit` passages for the query, best first."""
        ...


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
    ) -> list[RankedPassage]:
        rows = await self._search(session, query=query, ts_config=ts_config, limit=limit)
        if not rows and len(query.split()) > 1:
            rows = await self._search(
                session, query=query, ts_config=ts_config, limit=limit, union=True
            )
        return [
            RankedPassage(
                passage_id=UUID(str(row[0])),
                source_id=UUID(str(row[1])),
                address_key=str(row[2]),
                text=str(row[3]),
                rank=position,
                score=float(row[4]),
            )
            for position, row in enumerate(rows, start=1)
        ]

    async def _search(
        self,
        session: AsyncSession,
        *,
        query: str,
        ts_config: str,
        limit: int,
        union: bool = False,
    ) -> list[Any]:
        matcher: str = (
            "replace(plainto_tsquery(CAST(:cfg AS regconfig), :q)::text, '&', '|')::tsquery"
            if union
            else "plainto_tsquery(CAST(:cfg AS regconfig), :q)"
        )
        rows = (
            await session.execute(
                sa.text(
                    "SELECT id, source_id, address_key, text,"
                    f" ts_rank(tsv, {matcher}) AS score"
                    " FROM passages"
                    f" WHERE tsv @@ {matcher}"
                    " ORDER BY score DESC LIMIT :limit"
                ),
                {"cfg": ts_config, "q": query, "limit": limit},
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
    ) -> list[RankedPassage]:
        return await self._inner.search(session, query=query, ts_config=ts_config, limit=limit)


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
