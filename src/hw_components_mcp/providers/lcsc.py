"""LCSC (立创商城) provider via the lcsc.com global-site JSON API.

Endpoints (undocumented but login-free, verified 2026-10):

- POST https://wmsc.lcsc.com/ftps/wm/product/query/list   -- paged keyword search
- GET  https://wmsc.lcsc.com/ftps/wm/product/detail?productCode=Cxxxx

The Chinese site szlcsc.com sits behind an Aliyun WAF and is not usable
programmatically; prices here come from the global site and are USD.
"""

from __future__ import annotations

import re
from typing import Any

from ..http import get_client
from ..models import (
    AlternativePart,
    ComponentDetail,
    ComponentSummary,
    Parameter,
    PriceTier,
)
from .base import Provider, ProviderError

BASE = "https://wmsc.lcsc.com"

SEARCH_TTL = 300.0
DETAIL_TTL = 900.0

_HEADERS = {
    "accept": "application/json, text/plain, */*",
    "accept-language": "en",
    "content-type": "application/json;charset=UTF-8",
    "origin": "https://www.lcsc.com",
    "referer": "https://www.lcsc.com/",
}

_C_CODE_RE = re.compile(r"^C\d+$", re.IGNORECASE)


def _price_tiers(item: dict[str, Any]) -> list[PriceTier]:
    tiers: list[PriceTier] = []
    for p in item.get("productPriceList") or []:
        price = p.get("currencyPrice")
        currency = p.get("currencySymbol") or "USD"
        if price is None:
            price = p.get("usdPrice")
            currency = "USD"
        if price is None:
            continue
        try:
            tiers.append(
                PriceTier(quantity=int(p.get("ladder") or 0), price=float(price), currency=currency)
            )
        except (TypeError, ValueError):
            continue
    tiers.sort(key=lambda t: t.quantity)
    return tiers


def _fields_from_item(item: dict[str, Any]) -> dict[str, Any]:
    """Map an LCSC search/detail item into ComponentSummary constructor kwargs."""
    code = item.get("productCode")
    stock = item.get("stockNumber")
    return {
        "source": "lcsc",
        "product_id": code,
        "part_number": item.get("productModel"),
        "brand": item.get("brandNameEn") or item.get("brandName"),
        "package": item.get("encapStandard"),
        "description": item.get("productIntroEn") or item.get("productIntro"),
        "stock": int(stock) if isinstance(stock, (int, float)) else None,
        "price_tiers": _price_tiers(item),
        "min_order_qty": item.get("minBuyNumber"),
        "product_url": f"https://www.lcsc.com/product-detail/{code}.html" if code else None,
    }


def _check_envelope(data: Any, context: str) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ProviderError(f"LCSC 接口返回格式异常 ({context})")
    if data.get("code") not in (200, "200"):
        raise ProviderError(
            f"LCSC 接口返回错误: code={data.get('code')} msg={data.get('msg')} ({context})"
        )
    return data.get("result") or {}


class LcscProvider(Provider):
    name = "lcsc"
    display_name = "立创商城 LCSC"
    currency_note = "价格来自 LCSC 全球站（美元），库存为全球零售库存（非嘉立创贴片库存）。"
    region_note = "无区域限制；接口未公开文档，已内置 ≥3 秒请求间隔以尊重限流。"

    async def search(
        self, keyword: str, limit: int = 10, in_stock_only: bool = False
    ) -> list[ComponentSummary]:
        keyword = keyword.strip()
        if not keyword:
            return []
        if _C_CODE_RE.match(keyword):
            # A bare C-code goes straight to the detail endpoint.
            return [await self.get_detail(keyword)]

        client = get_client()
        body = {"keyword": keyword, "currentPage": 1, "pageSize": max(1, min(int(limit), 50))}
        data = await client.post_json(
            f"{BASE}/ftps/wm/product/query/list", json_body=body, headers=_HEADERS, ttl=SEARCH_TTL
        )
        result = _check_envelope(data, f"search '{keyword}'")
        items = result.get("dataList") or []
        out = [ComponentSummary(**_fields_from_item(i)) for i in items if isinstance(i, dict)]
        if in_stock_only:
            out = [c for c in out if (c.stock or 0) > 0]
        return out[:limit]

    async def get_detail(self, product_id: str) -> ComponentDetail:
        code = product_id.strip()
        if not _C_CODE_RE.match(code):
            raise ProviderError(f"LCSC 商品编号格式应为 C 开头数字（如 C8734），收到: {product_id}")
        code = code.upper()
        client = get_client()
        data = await client.get_json(
            f"{BASE}/ftps/wm/product/detail",
            params={"productCode": code},
            headers=_HEADERS,
            ttl=DETAIL_TTL,
        )
        result = _check_envelope(data, f"detail {code}")
        if not result:
            raise ProviderError(f"LCSC 未找到编号 {code}（可能无效）")

        params = [
            Parameter(name=str(n), value=str(v))
            for n, v in (
                (
                    p.get("paramNameEn") or p.get("paramName"),
                    p.get("paramValueEn") if p.get("paramValueEn") is not None else p.get("paramValue"),
                )
                for p in result.get("paramVOList") or []
            )
            if n and v is not None
        ]

        alternatives: list[AlternativePart] = []
        for a in result.get("alternatePartList") or []:
            if not isinstance(a, dict):
                continue
            alt_code = a.get("productCode") or a.get("code")
            alternatives.append(
                AlternativePart(
                    source=self.name,
                    product_id=alt_code,
                    part_number=a.get("productModel") or a.get("model") or a.get("productCode"),
                    brand=a.get("brandNameEn") or a.get("brandName"),
                    product_url=(
                        f"https://www.lcsc.com/product-detail/{alt_code}.html" if alt_code else None
                    ),
                    note=a.get("replaceRemark") or a.get("remark"),
                )
            )

        images = result.get("productImages") or []
        fields = _fields_from_item(result)
        if not fields.get("description"):
            fields["description"] = result.get("productNameEn")
        datasheet_url = result.get("pdfUrl") or result.get("pdfLinkUrl")
        if datasheet_url and datasheet_url.startswith("//"):
            datasheet_url = "https:" + datasheet_url

        return ComponentDetail(
            **fields,
            parameters=params,
            alternative_parts=alternatives,
            image_url=images[0] if images else None,
            datasheet_url=datasheet_url,
            currency=result.get("currencySymbol") or "USD",
        )
