"""Shared test helpers: fixture loader and fake HTTP client."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str, as_json: bool = False) -> Any:
    path = FIXTURES / name
    if as_json:
        return json.loads(path.read_text(encoding="utf-8"))
    return path.read_text(encoding="utf-8", errors="replace")


class FakeClient:
    """Stands in for SharedHttpClient in provider tests (no network)."""

    def __init__(
        self,
        json_responses: dict[str, Any] | None = None,
        texts: dict[str, str] | None = None,
    ):
        self.json_responses = json_responses or {}
        self.texts = texts or {}
        self.calls: list[tuple] = []

    def _lookup(self, table: dict, url: str):
        if url in table:
            return table[url]
        for key, value in table.items():  # prefix match for query-string variants
            if url.startswith(key):
                return value
        raise AssertionError(f"unexpected url requested: {url}")

    async def post_json(self, url, json_body=None, headers=None, ttl=0.0):
        self.calls.append(("POST", url, json_body))
        value = self._lookup(self.json_responses, url)
        if isinstance(value, Exception):
            raise value
        return value

    async def get_json(self, url, params=None, headers=None, ttl=0.0):
        self.calls.append(("GET", url, params))
        value = self._lookup(self.json_responses, url)
        if isinstance(value, Exception):
            raise value
        return value

    async def get_text(self, url, headers=None, params=None, ttl=0.0):
        self.calls.append(("GETTEXT", url, params))
        value = self._lookup(self.texts, url)
        if isinstance(value, Exception):
            raise value
        return value
