"""JD IC mall (京东电子元器件商城, ic.jd.com) provider via SSR HTML parsing.

Pages (login-free server-rendered HTML, verified 2026-10):

- search:  https://ic-search.jd.com/Search?keyword=<kw>&enc=utf-8
- detail:  https://ic-item.jd.com/<sku>.html

Each search row carries 品牌/分类/封装规格/规格型号(MPN)/包装清单 as
``<p><em>label：</em>value</p>`` blocks and stock/moq as attributes on the
quantity input box. Prices are filled client-side with a signed request, so
they are not available here — we return the product URL for manual checking.
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup

from ..http import get_client
from ..models import ComponentDetail, ComponentSummary, Parameter
from .base import Provider, ProviderError

SEARCH_URL = "https://ic-search.jd.com/Search"
ITEM_URL = "https://ic-item.jd.com/{sku}.html"

SEARCH_TTL = 300.0
DETAIL_TTL = 900.0

_HEADERS = {
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "accept-language": "zh-CN,zh;q=0.9,en;q=0.8",
    "referer": "https://ic.jd.com/",
}

_SKU_RE = re.compile(r"^\d{6,}$")
_MPN_LABELS = ("规格型号（MPN）", "规格型号(MPN)", "规格型号")


def _clean(text: str | None) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _labeled_ps(el) -> dict[str, str]:
    """Collect ``<p><em>label：</em>value</p>`` blocks into {label: value}."""
    out: dict[str, str] = {}
    for p in el.find_all("p"):
        em = p.find("em")
        if em is None:
            continue
        em_text = em.get_text(strip=True)
        label = em_text.rstrip("：:").strip()
        if not label:
            continue
        value = _clean(p.get_text(" ", strip=True).replace(em_text, "", 1))
        out[label] = value
    return out


def parse_search_html(html: str) -> list[ComponentSummary]:
    """Parse a JD IC search results page into unified summaries."""
    soup = BeautifulSoup(html, "lxml")
    out: list[ComponentSummary] = []
    seen: set[str] = set()
    for el in soup.select("div[data-sku]"):
        sku = el.get("data-sku")
        if not sku or not _SKU_RE.match(sku) or sku in seen:
            continue
        title_el = el.select_one(".goodtitlefc") or el.select_one(".goodsnamelx")
        if title_el is None:
            continue  # not a product row
        attrs = _labeled_ps(el)

        stock: int | None = None
        moq: int | None = None
        box = el.select_one(".input-boxx")
        if box is not None:
            if str(box.get("stock") or "").isdigit():
                stock = int(box["stock"])
            if str(box.get("moq") or "").isdigit():
                moq = int(box["moq"])

        part = next((attrs[lbl] for lbl in _MPN_LABELS if attrs.get(lbl)), None)
        out.append(
            ComponentSummary(
                source="jd",
                product_id=sku,
                part_number=part or None,
                brand=attrs.get("品牌") or None,
                package=attrs.get("封装规格") or None,
                description=_clean(title_el.get_text(" ", strip=True)) or None,
                stock=stock,
                price_tiers=[],
                min_order_qty=moq,
                product_url=f"https://item.jd.com/{sku}.html",
            )
        )
        seen.add(sku)
    return out


def parse_detail_html(html: str, sku: str) -> ComponentDetail:
    """Parse a JD IC item page into a unified detail."""
    soup = BeautifulSoup(html, "lxml")

    title_el = soup.select_one(".sku-name")
    title = _clean(title_el.get_text(" ", strip=True) if title_el else "")
    if not title and soup.title is not None:
        title = _clean(soup.title.get_text(strip=True))

    params: list[Parameter] = []
    brand: str | None = None
    for ul in soup.select(".p-parameter-list"):
        for li in ul.select("li"):
            text = li.get_text(" ", strip=True)
            name, sep, value = text.partition("：")
            if not sep:
                name, sep, value = text.partition(":")
            if not sep or not name.strip():
                continue
            name, value = _clean(name), _clean(value)
            params.append(Parameter(name=name, value=value))
            if name == "品牌":
                brand = value or None

    part: str | None = None
    package: str | None = None
    for it in soup.select(".attrisitembox .itemx"):
        span, font = it.find("span"), it.find("font")
        if span is None:
            continue
        name = _clean(span.get_text(" ", strip=True))
        value = _clean(font.get_text(" ", strip=True)) if font is not None else ""
        if not name:
            continue
        params.append(Parameter(name=name, value=value))
        if any(k in name for k in _MPN_LABELS):
            part = value or None
        elif "封装" in name:
            package = value or None

    return ComponentDetail(
        source="jd",
        product_id=sku,
        part_number=part,
        brand=brand,
        package=package,
        description=title or None,
        price_tiers=[],
        product_url=ITEM_URL.format(sku=sku),
        parameters=params,
        currency="CNY",
    )


class JdProvider(Provider):
    name = "jd"
    display_name = "京东元器件 ic.jd.com"
    currency_note = (
        "京东搜索/详情页为免登录 SSR，可取 MPN/品牌/封装/库存/起订量；"
        "价格需浏览器端签名接口，暂不可得，请按返回的商品链接到页查看。"
    )
    region_note = "建议使用中国大陆网络环境访问。"

    async def search(
        self, keyword: str, limit: int = 10, in_stock_only: bool = False
    ) -> list[ComponentSummary]:
        client = get_client()
        html = await client.get_text(
            SEARCH_URL,
            params={"keyword": keyword, "enc": "utf-8"},
            headers=_HEADERS,
            ttl=SEARCH_TTL,
        )
        if len(html) < 1000 or "安全验证" in html[:5000]:
            raise ProviderError("京东搜索页返回异常（可能触发反爬验证），请稍后重试或降低查询频率")
        items = parse_search_html(html)
        if in_stock_only:
            items = [i for i in items if (i.stock or 0) > 0]
        return items[:limit]

    async def get_detail(self, product_id: str) -> ComponentDetail:
        sku = product_id.strip()
        if not _SKU_RE.match(sku):
            raise ProviderError(f"京东商品编号为数字 sku（如 100110024845），收到: {product_id}")
        client = get_client()
        html = await client.get_text(ITEM_URL.format(sku=sku), headers=_HEADERS, ttl=DETAIL_TTL)
        if len(html) < 1000 or "安全验证" in html[:5000]:
            raise ProviderError(f"京东详情页返回异常（sku={sku}，可能触发反爬或商品下架）")
        return parse_detail_html(html, sku)
