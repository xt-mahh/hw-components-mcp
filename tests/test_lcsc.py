"""LCSC provider tests against recorded fixtures (offline)."""

from __future__ import annotations

import pytest

from conftest import FakeClient, load_fixture

from hw_components_mcp.providers import lcsc as lcsc_mod
from hw_components_mcp.providers.base import ProviderError

SEARCH_URL = f"{lcsc_mod.BASE}/ftps/wm/product/query/list"
DETAIL_URL = f"{lcsc_mod.BASE}/ftps/wm/product/detail"


@pytest.fixture
def provider(monkeypatch):
    fake = FakeClient(
        json_responses={
            SEARCH_URL: load_fixture("lcsc_search_stm32.json", as_json=True),
            DETAIL_URL: load_fixture("lcsc_detail_c8734.json", as_json=True),
        }
    )
    monkeypatch.setattr(lcsc_mod, "get_client", lambda: fake)
    return lcsc_mod.LcscProvider(), fake


async def test_search_maps_fields(provider):
    p, fake = provider
    items = await p.search("STM32F103C8T6", limit=10)
    assert len(items) == 3
    first = items[0]
    assert first.source == "lcsc"
    assert first.product_id == "C8734"
    assert first.part_number == "STM32F103C8T6"
    assert first.brand == "ST"
    assert first.package == "LQFP-48(7x7)"
    assert first.stock == 58939
    assert first.min_order_qty == 1
    assert first.product_url == "https://www.lcsc.com/product-detail/C8734.html"
    quantities = [t.quantity for t in first.price_tiers]
    assert quantities == sorted(quantities) == [1, 10, 30, 250, 500, 1000]
    assert all(t.currency == "$" for t in first.price_tiers)


async def test_search_in_stock_only_filters(provider):
    p, _ = provider
    items = await p.search("STM32F103C8T6", in_stock_only=True)
    assert items and all((i.stock or 0) > 0 for i in items)


async def test_bare_c_code_goes_straight_to_detail(provider):
    p, fake = provider
    items = await p.search("c8734")
    assert len(items) == 1
    assert items[0].product_id == "C8734"
    assert fake.calls[0][0] == "GET"  # detail endpoint, not search POST


async def test_detail_maps_parameters_and_alternatives(provider):
    p, _ = provider
    d = await p.get_detail("C8734")
    assert d.part_number == "STM32F103C8T6"
    assert d.stock == 58939
    assert ("Operating Temperature", "-40℃~+85℃") in [(x.name, x.value) for x in d.parameters]
    assert ("Number of I/O", "37") in [(x.name, x.value) for x in d.parameters]
    assert d.datasheet_url.startswith("https://datasheet.lcsc.com/")
    assert d.image_url and "lcsc.com" in d.image_url
    alts = d.alternative_parts
    assert alts and alts[0].product_id == "C2969777"
    assert alts[0].part_number == "STM32F103C8T6TR"


async def test_envelope_error_raises(provider, monkeypatch):
    p, fake = provider
    fake.json_responses[SEARCH_URL] = {"code": 429, "msg": "Rate Limit Exceeded", "result": None}
    with pytest.raises(ProviderError, match="429"):
        await p.search("STM32F103C8T6")


async def test_invalid_product_id(provider):
    p, _ = provider
    with pytest.raises(ProviderError, match="C 开头"):
        await p.get_detail("not-a-code")
