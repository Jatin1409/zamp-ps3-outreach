"""Search provider interface + Tavily (live), Replay (stored results) and Fixture (tests)."""
from __future__ import annotations

import asyncio
import random
from dataclasses import asdict, dataclass
from typing import Protocol

import httpx

from ..config import get_settings


class SearchError(Exception):
    def __init__(self, message: str, kind: str = "provider_error"):
        super().__init__(message)
        self.kind = kind


@dataclass
class SearchResult:
    title: str
    url: str
    content: str
    raw_content: str | None = None
    published_date: str | None = None
    score: float | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class SearchQuery:
    query: str
    topic: str = "general"          # general | news
    max_results: int = 6
    time_range: str | None = None   # day|week|month|year
    include_raw: bool = True

    def key(self) -> str:
        return f"{self.topic}|{self.time_range or ''}|{self.query.strip().lower()}"


class SearchProvider(Protocol):
    name: str

    async def search(self, q: SearchQuery) -> list[SearchResult]: ...


class TavilySearch:
    name = "tavily"
    URL = "https://api.tavily.com/search"

    def __init__(self):
        s = get_settings()
        if not s.tavily_api_key:
            raise SearchError("TAVILY_API_KEY is not set", kind="config")
        self.key = s.tavily_api_key
        self.timeout = s.search_timeout_s
        self.retries = s.provider_retries

    async def search(self, q: SearchQuery) -> list[SearchResult]:
        body = {
            "query": q.query[:400],
            "topic": q.topic,
            "search_depth": "basic",
            "max_results": q.max_results,
            "include_raw_content": "text" if q.include_raw else False,
            "include_published_date": True,
            "include_answer": False,
        }
        if q.time_range:
            body["time_range"] = q.time_range
        # Excluded at the source so the result slots go to usable public pages (see DECISIONS D9).
        from ..pipeline.stages import EXCLUDED_DOMAINS

        body["exclude_domains"] = EXCLUDED_DOMAINS
        last = ""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for attempt in range(self.retries + 1):
                try:
                    r = await client.post(self.URL, json=body, headers={"Authorization": f"Bearer {self.key}"})
                except (httpx.TimeoutException, httpx.TransportError) as e:
                    last = f"network/timeout: {type(e).__name__}"
                else:
                    if r.status_code == 200:
                        data = r.json()
                        return [
                            SearchResult(
                                title=(x.get("title") or "")[:500], url=x.get("url") or "",
                                content=x.get("content") or "", raw_content=x.get("raw_content"),
                                published_date=x.get("published_date"), score=x.get("score"),
                            )
                            for x in data.get("results", []) if x.get("url")
                        ]
                    if r.status_code in (401, 403):
                        raise SearchError("Search provider rejected the API key", kind="config")
                    if r.status_code in (432, 433):
                        raise SearchError("Search provider credit limit reached", kind="quota")
                    if r.status_code not in (429, 500, 502, 503, 504, 434):
                        raise SearchError(f"Search provider error {r.status_code}: {r.text[:200]}")
                    last = f"HTTP {r.status_code}"
                if attempt < self.retries:
                    await asyncio.sleep((2 ** attempt) + random.random())
        raise SearchError(f"Search provider failed after {self.retries + 1} attempts ({last})", kind="unavailable")


class ReplaySearch:
    """Serves results saved from an earlier live run of the same prospect. Never calls the network."""

    name = "replay"

    def __init__(self, records: dict[str, list[dict]], source_run_id: str | None):
        self.records = records
        self.source_run_id = source_run_id
        self.misses: list[str] = []

    async def search(self, q: SearchQuery) -> list[SearchResult]:
        rows = self.records.get(q.key())
        if rows is None:
            self.misses.append(q.query)
            return []
        return [SearchResult(**r) for r in rows]


class FixtureSearch:
    name = "fixture"

    def __init__(self, fixture: dict):
        self.data: dict[str, list[dict]] = fixture.get("search", {})
        self.fail = bool(fixture.get("fail_search"))

    async def search(self, q: SearchQuery) -> list[SearchResult]:
        await asyncio.sleep(0)
        if self.fail:
            raise SearchError("Simulated search outage", kind="unavailable")
        rows = self.data.get(q.key())
        if rows is None:
            rows = self.data.get(q.query.strip().lower(), self.data.get("*", []))
        return [SearchResult(**r) for r in rows]


def make_search(fixture: dict | None = None) -> SearchProvider:
    if fixture is not None or get_settings().search_provider == "fixture":
        return FixtureSearch(fixture or {})
    return TavilySearch()
