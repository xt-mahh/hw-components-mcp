"""JD provider tests against recorded fixtures (offline)."""

from __future__ import annotations

import pytest

from conftest import FakeClient, load_fixture

from hw_components_mcp.providers import jd as jd_mod
from hw_components_mcp.providers.base import ProviderError


@pytest.fixture
def provider(monkeypatch):
    fake = FakeClient(
        texts={
            jd_mod.SEARCH_URL: load_fixture("jd_search_stm32.html"),
            jd_mod.ITEM_URL.format(sku="100110024845"): load_fixture(
                "jd_item_100110024845.html"
            ),
        }
    )
    monkeypatch.setattr(jd_mod, "get_client", lambda: fake)
    return jd_mod.JdProvider(), fake


async def test_search_parses_rows(provider):
    p, _ = provider
    items = await p.search("STM32F103C8T6")
    assert len(items) == 6
    first = items[0]
    assert first.source == "jd"
    assert first.product_id == "100110024845"
    assert first.brand == "双铂"
    assert first.part_number == "核心板（向上焊接）"
    assert first.package == "1"
    assert first.stock == 99999
    assert first.min_order_qty == 1
    assert first.product_url == "https://item.jd.com/100110024845.html"
    # JD SSR has no prices — tiers stay empty, URL is provided instead
    assert first.price_tiers == []


async def test_detail_parses_parameters(provider):
    p, _ = provider
    d = await p.get_detail("100110024845")
    assert d.part_number == "核心板（向上焊接）"
    assert d.brand == "双铂"
    assert d.package == "1"
    names = [x.name for x in d.parameters]
    assert "规格型号（MPN）" in names
    assert d.product_url == "https://ic-item.jd.com/100110024845.html"


async def test_invalid_sku_rejected(provider):
    p, _ = provider
    with pytest.raises(ProviderError, match="数字 sku"):
        await p.get_detail("C8734")


async def test_blocked_page_raises(provider, monkeypatch):
    p, fake = provider
    fake.texts[jd_mod.SEARCH_URL] = "<html>too short</html>"
    with pytest.raises(ProviderError, match="反爬"):
        await p.search("STM32F103C8T6")
