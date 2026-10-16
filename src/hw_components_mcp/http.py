"""Shared HTTP layer.

One browser-like ``httpx.AsyncClient`` for every provider, with:

- per-domain minimum-interval rate limiting (LCSC needs >= 3s to stay polite)
- retries with exponential backoff for transport errors / 429 / 5xx
- an in-process TTL cache so repeated lookups don't hit the platform again

Environment switches:

- ``HWC_NO_CACHE=1``            disable the TTL cache
- ``HWC_MIN_INTERVAL_LCSC``     min seconds between wmsc.lcsc.com requests (default 3)
- ``HWC_MIN_INTERVAL``          default min interval for other domains (default 1)
- ``HWC_HTTP_TIMEOUT``          request timeout seconds (default 20)
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

import httpx

CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

_RETRY_STATUS = {429, 500, 502, 503, 504}
_RETRY_DELAYS = (1.0, 3.0, 9.0)

NO_CACHE = os.environ.get("HWC_NO_CACHE", "").lower() not in ("", "0", "false", "no")

_DOMAIN_MIN_INTERVALS = {
    "wmsc.lcsc.com": float(os.environ.get("HWC_MIN_INTERVAL_LCSC", "3.0")),
}
_DEFAULT_MIN_INTERVAL = float(os.environ.get("HWC_MIN_INTERVAL", "1.0"))


class HttpError(Exception):
    """Raised for network / HTTP-level failures; message is agent-friendly."""

    def __init__(self, message: str, *, status_code: int | None = None, url: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.url = url


class _DomainLimiter:
    """Serialize requests to one host with a minimum spacing between them."""

    def __init__(self, min_interval: float):
        self.min_interval = min_interval
        self._lock = asyncio.Lock()
        self._last = 0.0

    async def acquire(self) -> None:
        async with self._lock:
            wait = self._last + self.min_interval - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._last = time.monotonic()


class _TTLCache:
    def __init__(self) -> None:
        self._store: dict[str, tuple[float, Any]] = {}

    def get(self, key: str) -> Any | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        expires, value = entry
        if time.monotonic() > expires:
            self._store.pop(key, None)
            return None
        return value

    def set(self, key: str, value: Any, ttl: float) -> None:
        self._store[key] = (time.monotonic() + ttl, value)


def _truthy_env(name: str) -> bool:
    return os.environ.get(name, "").lower() not in ("", "0", "false", "no")


class SharedHttpClient:
    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._client = httpx.AsyncClient(
            timeout=float(os.environ.get("HWC_HTTP_TIMEOUT", "20")),
            follow_redirects=True,
            transport=transport,
            headers={
                "user-agent": CHROME_UA,
                "accept-language": "en",
            },
        )
        self._limiters: dict[str, _DomainLimiter] = {}
        self._cache = _TTLCache()

    def _limiter_for(self, url: str) -> _DomainLimiter:
        host = httpx.URL(url).host or ""
        if host not in self._limiters:
            self._limiters[host] = _DomainLimiter(_DOMAIN_MIN_INTERVALS.get(host, _DEFAULT_MIN_INTERVAL))
        return self._limiters[host]

    async def _request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        json_body: Any | None = None,
    ) -> httpx.Response:
        limiter = self._limiter_for(url)
        last_error: Exception | None = None
        for attempt in range(len(_RETRY_DELAYS) + 1):
            await limiter.acquire()
            try:
                resp = await self._client.request(
                    method, url, headers=headers, params=params, json=json_body
                )
                if resp.status_code in _RETRY_STATUS and attempt < len(_RETRY_DELAYS):
                    await asyncio.sleep(_RETRY_DELAYS[attempt])
                    continue
                return resp
            except httpx.TransportError as exc:
                last_error = exc
                if attempt < len(_RETRY_DELAYS):
                    await asyncio.sleep(_RETRY_DELAYS[attempt])
                    continue
                raise HttpError(f"网络请求失败 {url}: {exc}", url=url) from exc
        raise HttpError(f"请求失败（已重试） {url}: {last_error}", url=url)

    # -- cache helpers ---------------------------------------------------

    @staticmethod
    def _cache_key(method: str, url: str, params: dict | None, json_body: Any) -> str:
        payload = json.dumps(
            {"p": params or {}, "j": json_body}, sort_keys=True, ensure_ascii=False, default=str
        )
        return f"{method}:{url}:{payload}"

    def _cache_get(self, key: str, ttl: float) -> Any | None:
        if NO_CACHE or ttl <= 0:
            return None
        return self._cache.get(key)

    # -- public API -------------------------------------------------------

    async def get_json(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        ttl: float = 0.0,
    ) -> Any:
        key = self._cache_key("GET", url, params, None)
        cached = self._cache_get(key, ttl)
        if cached is not None:
            return cached
        resp = await self._request("GET", url, headers=headers, params=params)
        if resp.status_code != 200:
            raise HttpError(
                f"HTTP {resp.status_code} 请求 {url}", status_code=resp.status_code, url=url
            )
        try:
            data = resp.json()
        except ValueError as exc:
            raise HttpError(f"响应不是合法 JSON: {url} ({exc})", url=url) from exc
        if ttl > 0:
            self._cache.set(key, data, ttl)
        return data

    async def post_json(
        self,
        url: str,
        *,
        json_body: Any = None,
        headers: dict[str, str] | None = None,
        ttl: float = 0.0,
    ) -> Any:
        key = self._cache_key("POST", url, None, json_body)
        cached = self._cache_get(key, ttl)
        if cached is not None:
            return cached
        resp = await self._request("POST", url, headers=headers, json_body=json_body)
        if resp.status_code != 200:
            raise HttpError(
                f"HTTP {resp.status_code} 请求 {url}", status_code=resp.status_code, url=url
            )
        try:
            data = resp.json()
        except ValueError as exc:
            raise HttpError(f"响应不是合法 JSON: {url} ({exc})", url=url) from exc
        if ttl > 0:
            self._cache.set(key, data, ttl)
        return data

    async def get_text(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        ttl: float = 0.0,
    ) -> str:
        key = self._cache_key("GETTEXT", url, params, None)
        cached = self._cache_get(key, ttl)
        if cached is not None:
            return cached
        resp = await self._request("GET", url, headers=headers, params=params)
        if resp.status_code != 200:
            raise HttpError(
                f"HTTP {resp.status_code} 请求 {url}", status_code=resp.status_code, url=url
            )
        text = resp.text
        if ttl > 0:
            self._cache.set(key, text, ttl)
        return text

    async def download(self, url: str, dest: Path, *, headers: dict[str, str] | None = None) -> Path:
        """Stream ``url`` to ``dest`` via a .part temp file; returns the final path."""
        limiter = self._limiter_for(url)
        await limiter.acquire()
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".part")
        try:
            async with self._client.stream("GET", url, headers=headers, follow_redirects=True) as resp:
                if resp.status_code != 200:
                    raise HttpError(
                        f"HTTP {resp.status_code} 下载 {url}", status_code=resp.status_code, url=url
                    )
                with open(tmp, "wb") as fh:
                    async for chunk in resp.aiter_bytes():
                        fh.write(chunk)
        except httpx.TransportError as exc:
            tmp.unlink(missing_ok=True)
            raise HttpError(f"下载失败 {url}: {exc}", url=url) from exc
        if tmp.stat().st_size == 0:
            tmp.unlink(missing_ok=True)
            raise HttpError(f"下载内容为空: {url}", url=url)
        tmp.replace(dest)
        return dest

    async def aclose(self) -> None:
        await self._client.aclose()


_shared: SharedHttpClient | None = None


def get_client() -> SharedHttpClient:
    global _shared
    if _shared is None:
        _shared = SharedHttpClient()
    return _shared
