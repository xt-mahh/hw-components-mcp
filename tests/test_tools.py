"""Tool-level tests with fake providers (offline)."""

from __future__ import annotations

import pytest

from hw_components_mcp.models import (
    AlternativePart,
    ComponentDetail,
    ComponentSummary,
    PriceTier,
)
from hw_components_mcp.providers import resolve_platform
from hw_components_mcp.providers.base import Provider, ProviderError
from hw_components_mcp import server
from hw_components_mcp.server import (
    _unit_price_at_qty,
    check_bom,
    find_alternatives,
    search_components,
)


class FakeProvider(Provider):
    def __init__(self, items=None, detail=None, error=None):
        self.items = items or []
        self.detail = detail
        self.error = error

    async def search(self, keyword, limit=10, in_stock_only=False):
        if self.error:
            raise ProviderError(self.error)
        return self.items[:limit]

    async def get_detail(self, product_id):
        if self.error:
            raise ProviderError(self.error)
        if self.detail is not None:
            return self.detail
        raise ProviderError(f"no detail for {product_id}")


def _summary(**kw):
    base = dict(
        source="lcsc",
        product_id="C1",
        part_number="STM32F103C8T6",
        brand="ST",
        stock=100,
        price_tiers=[PriceTier(quantity=1, price=6.5, currency="CNY"),
                     PriceTier(quantity=100, price=6.0, currency="CNY")],
        product_url="https://example.com/C1",
    )
    base.update(kw)
    return ComponentSummary(**base)


def _install(monkeypatch, providers: dict):
    monkeypatch.setattr(server, "get_provider", lambda name: providers[name])
    monkeypatch.setattr(server, "all_providers", lambda: providers)


async def test_search_all_aggregates_and_captures_errors(monkeypatch):
    _install(
        monkeypatch,
        {
            "lcsc": FakeProvider(items=[_summary()]),
            "jd": FakeProvider(error="京东搜索页返回异常"),
            "hqchip": FakeProvider(items=[]),
        },
    )
    out = await search_components("STM32F103C8T6", platform="all")
    assert len(out["lcsc"]["items"]) == 1
    assert out["lcsc"]["items"][0]["part_number"] == "STM32F103C8T6"
    assert "异常" in out["jd"]["error"]
    assert out["hqchip"]["items"] == []


async def test_check_bom_price_tier_and_shortage(monkeypatch):
    _install(monkeypatch, {"lcsc": FakeProvider(items=[_summary()])})
    report = await check_bom("STM32F103C8T6 250", platform="lcsc")
    assert report.total_lines == 1
    line = report.lines[0]
    assert line.matched and line.exact_match
    assert line.quantity == 250
    res = line.results[0]
    assert res.unit_price == 6.0  # tier 100+ applies at qty 250
    assert res.shortage == 150  # stock 100 < qty 250
    assert res.stock == 100


async def test_check_bom_skips_comments_and_defaults_qty(monkeypatch):
    _install(monkeypatch, {"lcsc": FakeProvider(items=[_summary()])})
    report = await check_bom("# a comment\n\nSTM32F103C8T6, 10\n", platform="lcsc")
    assert report.total_lines == 1
    assert report.lines[0].quantity == 10
    assert report.lines[0].results[0].unit_price == 6.5  # tier 1 at qty 10


async def test_check_bom_unmatched_line(monkeypatch):
    _install(monkeypatch, {"lcsc": FakeProvider(items=[])})
    report = await check_bom("NONEXISTENT999 5", platform="lcsc")
    assert report.total_lines == 1 and report.matched_lines == 0
    assert report.lines[0].matched is False


async def test_check_bom_reports_platform_error(monkeypatch):
    _install(monkeypatch, {"jd": FakeProvider(error="boom")})
    report = await check_bom("STM32F103C8T6 5", platform="jd")
    assert report.lines[0].matched is False
    assert "boom" in (report.lines[0].error or "")


async def test_find_alternatives_non_lcsc_guides(monkeypatch):
    _install(monkeypatch, {"jd": FakeProvider()})
    out = await find_alternatives("100110024845", platform="jd")
    assert out["supported"] is False


async def test_find_alternatives_lcsc(monkeypatch):
    detail = ComponentDetail(
        source="lcsc",
        product_id="C8734",
        part_number="STM32F103C8T6",
        alternative_parts=[
            AlternativePart(source="lcsc", product_id="C2969777", part_number="STM32F103C8T6TR")
        ],
    )
    _install(monkeypatch, {"lcsc": FakeProvider(detail=detail)})
    out = await find_alternatives("C8734", platform="lcsc")
    assert out["supported"] is True
    assert out["alternatives"][0]["product_id"] == "C2969777"


def test_unit_price_at_qty():
    tiers = [PriceTier(quantity=10, price=5.0, currency="CNY"), PriceTier(quantity=1, price=6.0, currency="CNY")]
    assert _unit_price_at_qty(tiers, 1) == (6.0, "CNY")
    assert _unit_price_at_qty(tiers, 10) == (5.0, "CNY")
    assert _unit_price_at_qty(tiers, 9999) == (5.0, "CNY")
    assert _unit_price_at_qty([], 10) == (None, None)


def test_resolve_platform_aliases():
    assert resolve_platform("立创") == "lcsc"
    assert resolve_platform("SZLCSC") == "lcsc"
    assert resolve_platform("京东") == "jd"
    assert resolve_platform("华秋商城") == "hqchip"
    assert resolve_platform("all") == "all"
    with pytest.raises(ValueError):
        resolve_platform("taobao")
