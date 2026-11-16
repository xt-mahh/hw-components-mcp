"""HQChip provider tests against recorded fixtures (offline)."""

from __future__ import annotations

import pytest

from conftest import FakeClient, load_fixture

from hw_components_mcp.http import HttpError
from hw_components_mcp.providers import hqchip as hq_mod
from hw_components_mcp.providers.base import ProviderError


@pytest.fixture
def provider(monkeypatch):
    fake = FakeClient(
        texts={
            "https://www.hqchip.com/search/STM32F103C8T6.html": load_fixture(
                "hqchip_search_page.html"
            ),
            "https://item.hqchip.com/2500269839.html": load_fixture(
                "hqchip_item_2500269839.html"
            ),
        }
    )
    monkeypatch.setattr(hq_mod, "get_client", lambda: fake)
    return hq_mod.HqchipProvider(), fake


async def test_search_parses_rows(provider):
    p, _ = provider
    items = await p.search("STM32F103C8T6")
    assert len(items) == 3
    first = items[0]
    assert first.source == "hqchip"
    assert first.product_id == "2500269839"
    assert first.part_number == "STM32F103C8T6"
    assert first.brand == "ST"
    assert first.package == "LQFP-48(7x7)"
    assert first.stock == 20445
    assert first.description == "32位MCU微控制器 ARM-M3 72MHz 64KB"
    assert [(t.quantity, t.price, t.currency) for t in first.price_tiers] == [
        (1, 6.5, "CNY"),
        (50, 6.1, "CNY"),
        (1000, 5.9, "CNY"),
    ]
    assert first.datasheet_url.startswith("https://file.huaqiu.com/")
    assert first.product_url == "https://item.hqchip.com/2500269839.html"


async def test_detail_parses_ladder_and_stock(provider):
    p, _ = provider
    d = await p.get_detail("2500269839")
    assert d.part_number == "STM32F103C8T6"
    assert d.brand == "ST"
    assert d.description == "32位MCU微控制器"
    assert d.stock == 20445
    assert d.min_order_qty == 1
    assert [(t.quantity, t.price) for t in d.price_tiers] == [(1, 6.5), (50, 6.1), (1000, 5.9)]
    assert d.datasheet_url and d.datasheet_url.startswith("https://file.huaqiu.com/")


async def test_search_wraps_network_error_with_region_hint(monkeypatch):
    class ErrClient:
        async def get_text(self, *a, **k):
            raise HttpError("Connection failed")

    monkeypatch.setattr(hq_mod, "get_client", lambda: ErrClient())
    p = hq_mod.HqchipProvider()
    with pytest.raises(ProviderError, match="中国大陆"):
        await p.search("STM32F103C8T6")


async def test_http_status_error_has_no_region_hint(monkeypatch):
    class ErrClient:
        async def get_text(self, *a, **k):
            raise HttpError("HTTP 404 请求 ...", status_code=404)

    monkeypatch.setattr(hq_mod, "get_client", lambda: ErrClient())
    p = hq_mod.HqchipProvider()
    with pytest.raises(ProviderError) as exc_info:
        await p.search("STM32F103C8T6")
    assert "中国大陆" not in str(exc_info.value)
    assert "404" in str(exc_info.value)


async def test_login_wall_raises_guidance(provider, monkeypatch):
    p, fake = provider
    fake.texts["https://www.hqchip.com/search/STM32F103C8T6.html"] = (
        "<html><head><meta http-equiv=\"refresh\" content=\"0;url=https://www.hqchip.com/users/login.html?forceLogin=1\"></head>"
        + "x" * 1500
    )
    with pytest.raises(ProviderError, match="HQCHIP_COOKIE"):
        await p.search("STM32F103C8T6")


async def test_cookie_env_var_is_sent(provider, monkeypatch):
    p, fake = provider
    monkeypatch.setenv("HQCHIP_COOKIE", "PHPSESSID=abc123")
    await p.search("STM32F103C8T6")
    headers = fake.calls[0][3]
    assert headers.get("cookie") == "PHPSESSID=abc123"


async def test_invalid_goods_id(provider):
    p, _ = provider
    with pytest.raises(ProviderError, match="goodsId"):
        await p.get_detail("C8734")
