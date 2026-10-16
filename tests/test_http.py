"""HTTP layer tests: retries and TTL cache (offline, MockTransport)."""

from __future__ import annotations

import asyncio

import httpx
import pytest

import hw_components_mcp.http as http_mod
from hw_components_mcp.http import HttpError, SharedHttpClient


def _make(monkeypatch, handler) -> tuple[SharedHttpClient, dict]:
    monkeypatch.setattr(http_mod, "_RETRY_DELAYS", (0.0, 0.0, 0.0))
    monkeypatch.setattr(http_mod, "_DEFAULT_MIN_INTERVAL", 0.0)
    state = {"calls": 0}

    def wrapped(request):
        state["calls"] += 1
        return handler(request, state["calls"])

    client = SharedHttpClient(transport=httpx.MockTransport(wrapped))
    return client, state


async def test_retry_on_500_then_success(monkeypatch):
    def handler(request, call):
        if call <= 2:
            return httpx.Response(500)
        return httpx.Response(200, json={"ok": 1})

    client, state = _make(monkeypatch, handler)
    data = await client.get_json("https://test.example/x")
    assert data == {"ok": 1}
    assert state["calls"] == 3
    await client.aclose()


async def test_retry_exhaustion_raises(monkeypatch):
    client, state = _make(monkeypatch, lambda request, call: httpx.Response(503))
    with pytest.raises(HttpError):
        await client.get_json("https://test.example/x")
    assert state["calls"] == 4  # initial + 3 retries
    await client.aclose()


async def test_no_retry_on_404(monkeypatch):
    client, state = _make(monkeypatch, lambda request, call: httpx.Response(404, text="not found"))
    with pytest.raises(HttpError, match="404"):
        await client.get_json("https://test.example/missing")
    assert state["calls"] == 1
    await client.aclose()


async def test_ttl_cache_serves_repeat_get(monkeypatch):
    client, state = _make(monkeypatch, lambda request, call: httpx.Response(200, json={"n": call}))
    first = await client.get_json("https://test.example/x", ttl=60)
    second = await client.get_json("https://test.example/x", ttl=60)
    assert first == second == {"n": 1}
    assert state["calls"] == 1  # second hit served from cache
    await client.aclose()


async def test_ttl_cache_expires(monkeypatch):
    client, state = _make(monkeypatch, lambda request, call: httpx.Response(200, json={"n": call}))
    await client.get_json("https://test.example/x", ttl=0.05)
    await asyncio.sleep(0.08)
    again = await client.get_json("https://test.example/x", ttl=0.05)
    assert again == {"n": 2}  # cache expired, network hit again
    await client.aclose()


async def test_post_json_non_json_response_raises(monkeypatch):
    client, _ = _make(monkeypatch, lambda request, call: httpx.Response(200, text="not json"))
    with pytest.raises(HttpError, match="JSON"):
        await client.post_json("https://test.example/p", json_body={"a": 1})
    await client.aclose()
