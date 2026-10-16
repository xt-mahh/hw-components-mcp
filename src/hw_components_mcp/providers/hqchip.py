"""HQChip (华秋商城, hqchip.com) provider via SSR HTML parsing.

Pages (login-free server-rendered, verified 2026-10):

- search:  https://www.hqchip.com/search?keyword=<kw>  (redirects to /search/<kw>.html)
- detail:  https://item.hqchip.com/<goods_id>.html

Search result rows are rich: 型号/品牌/封装/描述/编号 plus an RMB ladder
price table, spot stock and a direct datasheet PDF link. The detail page's
full parameter table is only rendered client-side (inside the __NUXT__
blob), so detail parameters stay empty in v1.
"""

from __future__ import annotations

import re
from urllib.parse import quote

from bs4 import BeautifulSoup

from ..http import HttpError, get_client
from ..models import ComponentDetail, ComponentSummary, Parameter, PriceTier
from .base import Provider, ProviderError

SEARCH_TTL = 300.0
DETAIL_TTL = 900.0

_HEADERS = {
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "accept-language": "zh-CN,zh;q=0.9,en;q=0.8",
    "referer": "https://www.hqchip.com/",
}

_GOODS_ID_RE = re.compile(r"^\d+$")
_NUM_RE = re.compile(r"\d+(?:\.\d+)?")


def _clean(text: str | None) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _abs_url(url: str | None) -> str | None:
    if not url:
        return None
    if url.startswith("//"):
        return "https:" + url
    return url


def _price_tiers_from_row(row) -> list[PriceTier]:
    tiers: list[PriceTier] = []
    for tr in row.select(".price_list table tr"):
        tds = tr.select("td")
        if len(tds) < 2:
            continue
        num = _NUM_RE.search(tds[0].get_text(strip=True))
        price = _NUM_RE.search(tds[1].get_text(strip=True))
        if not num or not price:
            continue
        tiers.append(
            PriceTier(quantity=int(float(num.group())), price=float(price.group()), currency="CNY")
        )
    tiers.sort(key=lambda t: t.quantity)
    return tiers


def parse_search_html(html: str) -> list[ComponentSummary]:
    """Parse an HQChip search page into unified summaries."""
    soup = BeautifulSoup(html, "lxml")
    out: list[ComponentSummary] = []
    seen: set[str] = set()
    for a in soup.select("a.ibox[data-goodsid]"):
        goods_id = a.get("data-goodsid")
        if not goods_id or goods_id in seen:
            continue
        row = a.find_parent("div", class_="tr")
        if row is None:
            continue

        brand = package = desc = None
        col2 = row.select_one(".col2")
        if col2 is not None:
            for p in col2.select(".c1 p"):
                tag = p.select_one(".tag")
                if tag is None:
                    continue
                tag_text = tag.get_text(strip=True)
                label = tag_text.rstrip("：:").strip()
                value = _clean(p.get_text(" ", strip=True).replace(tag_text, "", 1))
                if label == "品牌":
                    brand = value or None
                elif label == "封装":
                    package = value or None
                    if package in {"-", "—", "无"}:
                        package = None
            desc_el = col2.select_one(".desc")
            if desc_el is not None:
                # the title attribute is a generic category placeholder; the
                # anchor text is the real goods description
                desc = _clean(desc_el.get_text(" ", strip=True)) or _clean(desc_el.get("title")) or None

        stock = None
        stock_el = row.select_one(".col6 .c-bc")
        if stock_el is not None:
            m = _NUM_RE.search(stock_el.get_text(strip=True))
            if m:
                stock = int(float(m.group()))

        datasheet = _abs_url(row.select_one(".databook a").get("href")) if row.select_one(".databook a") else None

        out.append(
            ComponentSummary(
                source="hqchip",
                product_id=goods_id,
                part_number=a.get("data-goodsname") or None,
                brand=brand,
                package=package,
                description=desc,
                stock=stock,
                price_tiers=_price_tiers_from_row(row),
                product_url=f"https://item.hqchip.com/{goods_id}.html",
                datasheet_url=datasheet,
            )
        )
        seen.add(goods_id)
    return out


def parse_item_html(html: str, goods_id: str) -> ComponentDetail:
    """Parse an HQChip item page: title/brand/description, price ladder, stock."""
    soup = BeautifulSoup(html, "lxml")
    title = _clean(soup.title.get_text(strip=True)) if soup.title else ""
    # Title pattern: "<MPN>-<Brand>(<BrandCN>)<description>[现货 价格 ...]-华秋商城"
    part = brand = desc = None
    m = re.match(r"^(?P<part>[^-【\[]+?)-(?P<rest>.+)$", title)
    if m:
        part = m.group("part").strip() or None
        rest = m.group("rest")
        if "(" in rest and ")" in rest:
            brand = rest.split("(", 1)[0].strip() or None
            tail = rest.split(")", 1)[1]
        else:
            brand = rest.split("[", 1)[0].split("-华秋", 1)[0].strip() or None
            tail = ""
        desc = _clean(tail.split("[", 1)[0].split("-华秋", 1)[0]) or None

    price_tiers: list[PriceTier] = []
    for tbl in soup.select("table"):
        headers = [c.get_text(strip=True) for c in tbl.select("tr:first-child th")]
        if not any("数量" in h or "国内含税" in h for h in headers):
            continue
        for tr in tbl.select("tr")[1:]:
            tds = tr.select("td")
            if len(tds) < 2:
                continue
            num = _NUM_RE.search(tds[0].get_text(strip=True))
            price = _NUM_RE.search(tds[1].get_text(strip=True))
            if not num or not price:
                continue
            price_tiers.append(
                PriceTier(quantity=int(float(num.group())), price=float(price.group()), currency="CNY")
            )
        break

    stock = None
    moq = None
    for tbl in soup.select("table"):
        cells = [c.get_text(strip=True) for c in tbl.select("td")]
        if len(cells) >= 2 and cells[0].isdigit() and "起订" in cells[1]:
            stock = int(cells[0])
            m3 = _NUM_RE.search(cells[1])
            moq = int(float(m3.group())) if m3 else None
            break

    datasheet = None
    for a in soup.find_all("a"):
        if "数据手册" in a.get_text():
            datasheet = _abs_url(a.get("href"))
            if datasheet:
                break

    return ComponentDetail(
        source="hqchip",
        product_id=goods_id,
        part_number=part,
        brand=brand,
        package=None,
        description=desc,
        stock=stock,
        price_tiers=sorted(price_tiers, key=lambda t: t.quantity),
        min_order_qty=moq,
        product_url=f"https://item.hqchip.com/{goods_id}.html",
        datasheet_url=datasheet,
        parameters=[],  # full parameter table is client-side rendered (__NUXT__), not in SSR HTML
        currency="CNY",
    )


class HqchipProvider(Provider):
    name = "hqchip"
    display_name = "华秋商城 hqchip.com"
    currency_note = "人民币价格（自营现货），含阶梯价与数据手册直链；详情页参数表需浏览器渲染，v1 暂不提供。"
    region_note = "必须中国大陆网络出口访问（海外/数据中心 IP 会被直接拦截）。"

    async def search(
        self, keyword: str, limit: int = 10, in_stock_only: bool = False
    ) -> list[ComponentSummary]:
        client = get_client()
        try:
            html = await client.get_text(
                f"https://www.hqchip.com/search?keyword={quote(keyword)}",
                headers=_HEADERS,
                ttl=SEARCH_TTL,
            )
        except HttpError as exc:
            raise ProviderError(
                f"华秋商城访问失败：该站需要中国大陆网络出口（{exc}）"
            ) from exc
        if len(html) < 1000:
            raise ProviderError("华秋搜索页返回异常，请稍后重试")
        items = parse_search_html(html)
        if in_stock_only:
            items = [i for i in items if (i.stock or 0) > 0]
        return items[:limit]

    async def get_detail(self, product_id: str) -> ComponentDetail:
        goods_id = product_id.strip()
        if not _GOODS_ID_RE.match(goods_id):
            raise ProviderError(f"华秋商品编号为数字 goodsId（如 2500269839），收到: {product_id}")
        client = get_client()
        try:
            html = await client.get_text(
                f"https://item.hqchip.com/{goods_id}.html", headers=_HEADERS, ttl=DETAIL_TTL
            )
        except HttpError as exc:
            raise ProviderError(f"华秋商城访问失败：该站需要中国大陆网络出口（{exc}）") from exc
        if len(html) < 1000:
            raise ProviderError(f"华秋详情页返回异常（goodsId={goods_id}，可能已下架）")
        return parse_item_html(html, goods_id)
