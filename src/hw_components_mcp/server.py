"""FastMCP server: unified electronic-component tools over LCSC / JD IC / HQChip.

Tools:

- list_platforms                          平台元信息（币种/库存口径/区域要求）
- search_components(keyword, platform)    关键词搜索（platform='all' 聚合三平台）
- get_component_detail(product_id, platform)  完整参数/阶梯价/库存/datasheet
- check_bom(bom_lines, platform)          BOM 批量库存/价格核查
- download_datasheet(product_id, platform) 下载数据手册 PDF 到本地
- find_alternatives(product_id, platform) 替代料查询
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

from fastmcp import FastMCP

from . import __version__
from .http import HttpError, get_client
from .models import (
    BomLineResult,
    BomPlatformResult,
    BomReport,
    ComponentDetail,
    ComponentSummary,
    PriceTier,
)
from .providers import all_providers, get_provider, resolve_platform
from .providers.base import ProviderError

mcp = FastMCP(
    "hw-components",
    instructions=(
        "元器件检索工具集（立创商城 LCSC / 京东元器件 / 华秋商城）。"
        "典型流程：先 search_components 按型号或关键词找料 → 用返回的 product_id "
        "调 get_component_detail 看参数与阶梯价 → 需要读手册时 download_datasheet；"
        "整单 BOM 核库存用 check_bom。LCSC 数据最全（参数/替代料/datasheet），"
        "华秋有人民币自营价，京东适合核对国内现货渠道。"
    ),
)


def _platform_targets(platform: str) -> list[str]:
    resolved = resolve_platform(platform)
    return list(all_providers()) if resolved == "all" else [resolved]


def _unit_price_at_qty(tiers: list[PriceTier], qty: int) -> tuple[float | None, str | None]:
    """Applicable ladder price for a given quantity (largest tier <= qty)."""
    if not tiers:
        return None, None
    ordered = sorted(tiers, key=lambda t: t.quantity)
    chosen = ordered[0]
    for t in ordered:
        if t.quantity <= qty:
            chosen = t
        else:
            break
    return chosen.price, chosen.currency


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool
async def list_platforms() -> dict[str, Any]:
    """列出可接入的元器件平台及数据说明（币种口径、库存口径、区域/反爬要求）。"""
    return {
        name: {
            "display_name": p.display_name,
            "currency_note": p.currency_note,
            "region_note": p.region_note,
        }
        for name, p in all_providers().items()
    }


@mcp.tool
async def search_components(
    keyword: str,
    platform: str = "lcsc",
    limit: int = 10,
    in_stock_only: bool = False,
) -> dict[str, Any]:
    """按型号/关键词搜索元器件，返回统一格式结果列表。

    Args:
        keyword: 型号（如 STM32F103C8T6）、LCSC C 编号（如 C8734）或泛化描述（如 0603 10k）。
        platform: lcsc（立创，数据最全）/ jd（京东）/ hqchip（华秋，人民币自营价）/ all（聚合三平台）。
        limit: 每平台最多返回条数（1-50）。
        in_stock_only: 仅保留有现货的结果。
    """
    limit = max(1, min(int(limit), 50))

    async def one(name: str):
        try:
            items = await get_provider(name).search(keyword, limit=limit, in_stock_only=in_stock_only)
            return name, [i.model_dump() for i in items], None
        except (ProviderError, HttpError) as exc:
            return name, [], str(exc)

    results = await asyncio.gather(*(_oneguarded(one, n) for n in _platform_targets(platform)))
    return {name: {"items": items, "error": err} for name, items, err in results}


async def _oneguarded(one, name):
    try:
        return await one(name)
    except ValueError as exc:  # bad platform id etc.
        return name, [], str(exc)


@mcp.tool
async def get_component_detail(product_id: str, platform: str = "lcsc") -> dict[str, Any]:
    """获取元器件完整详情：参数表、阶梯价格、库存、起订量、数据手册链接、替代料。

    Args:
        product_id: 平台商品编号 —— LCSC 为 C 编号（如 C8734），京东为数字 sku，华秋为数字 goodsId。
        platform: lcsc / jd / hqchip。
    """
    try:
        name = resolve_platform(platform)
        if name == "all":
            return {"error": "get_component_detail 不支持 platform='all'，请指定具体平台"}
        detail = await get_provider(name).get_detail(product_id)
        return detail.model_dump()
    except (ProviderError, HttpError, ValueError) as exc:
        return {"error": str(exc)}


@mcp.tool
async def check_bom(bom_lines: str, platform: str = "lcsc") -> BomReport:
    """BOM 批量核查：多行「料号 数量」，逐行返回各平台库存、按量单价与缺口。

    Args:
        bom_lines: 多行文本，每行 "MPN 数量"（数量可省略，默认 1）；# 开头为注释。
        platform: lcsc / jd / hqchip / all（聚合多平台时每行含各平台结果）。
    """
    targets = _platform_targets(platform)

    parsed: list[tuple[str, str, int]] = []
    for raw in bom_lines.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        tokens = [t for t in re.split(r"[\s,;\t]+", line) if t]
        qty = 1
        if len(tokens) > 1 and tokens[-1].isdigit():
            qty = int(tokens[-1])
            tokens = tokens[:-1]
        if tokens:
            parsed.append((line, " ".join(tokens), qty))

    line_results: list[BomLineResult] = []

    async def check_line(raw: str, part: str, qty: int) -> BomLineResult:
        per_platform: list[BomPlatformResult] = []
        matched = False
        exact: bool | None = None
        errors: list[str] = []
        for name in targets:
            try:
                items = await get_provider(name).search(part, limit=5)
            except (ProviderError, HttpError) as exc:
                errors.append(f"{name}: {exc}")
                continue
            norm = part.replace(" ", "").upper()
            chosen = next(
                (i for i in items if i.part_number and i.part_number.replace(" ", "").upper() == norm),
                None,
            )
            if chosen is None and items:
                chosen = items[0]
            if chosen is None:
                continue
            is_exact = bool(
                chosen.part_number
                and chosen.part_number.replace(" ", "").upper() == norm
            )
            matched = True
            if exact is None and is_exact:
                exact = True
            unit_price, currency = _unit_price_at_qty(chosen.price_tiers, qty)
            shortage = None
            if chosen.stock is not None and chosen.stock < qty:
                shortage = qty - chosen.stock
            per_platform.append(
                BomPlatformResult(
                    source=name,
                    product_id=chosen.product_id,
                    part_number=chosen.part_number,
                    stock=chosen.stock,
                    unit_price=unit_price,
                    currency=currency,
                    shortage=shortage,
                    product_url=chosen.product_url,
                )
            )
            if not is_exact and exact is None:
                exact = False
        error = "; ".join(errors) if errors and not per_platform else (None if not errors else "; ".join(errors))
        return BomLineResult(
            raw_line=raw,
            part_number=part,
            quantity=qty,
            matched=matched,
            exact_match=exact,
            results=per_platform,
            error=error,
        )

    line_results = list(
        await asyncio.gather(*(check_line(raw, part, qty) for raw, part, qty in parsed))
    )

    return BomReport(
        platform=resolve_platform(platform),
        total_lines=len(line_results),
        matched_lines=sum(1 for r in line_results if r.matched),
        lines=line_results,
    )


@mcp.tool
async def download_datasheet(
    product_id: str, platform: str = "lcsc", save_dir: str = "./datasheets"
) -> dict[str, Any]:
    """下载数据手册 PDF 到本地并返回文件绝对路径（供后续用 pdf 工具精读）。

    Args:
        product_id: LCSC C 编号 / 京东 sku（京东通常无手册）/ 华秋 goodsId。
        platform: lcsc / jd / hqchip。
        save_dir: 保存目录，默认 ./datasheets。
    """
    try:
        name = resolve_platform(platform)
        if name == "all":
            return {"error": "download_datasheet 需要指定具体平台"}
        url = await get_provider(name).get_datasheet_url(product_id)
        if not url:
            return {"error": f"{name} 平台未提供 {product_id} 的数据手册链接"}
        dest = Path(save_dir) / f"{name}_{re.sub(r'[^A-Za-z0-9_.-]', '_', product_id)}.pdf"
        path = await get_client().download(url, dest)
        size = path.stat().st_size
        if size < 1024:
            return {"error": f"下载内容过小（{size}B），可能不是有效 PDF: {url}"}
        return {"path": str(path.resolve()), "url": url, "size_bytes": size}
    except (ProviderError, HttpError, ValueError) as exc:
        return {"error": str(exc)}


@mcp.tool
async def find_alternatives(product_id: str, platform: str = "lcsc") -> dict[str, Any]:
    """查询元器件的官方替代料/替换料（当前仅 LCSC 支持，来自详情接口 alternatePartList）。

    Args:
        product_id: LCSC C 编号（如 C8734）。
        platform: 仅 lcsc 支持替代料数据。
    """
    try:
        name = resolve_platform(platform)
        if name != "lcsc":
            return {
                "supported": False,
                "message": (
                    f"{name} 平台无替代料接口。建议：用 search_components 按同类别+关键参数"
                    "（封装/耐压/精度等）搜索近似型号，或改用 platform='lcsc'。"
                ),
            }
        detail: ComponentDetail = await get_provider("lcsc").get_detail(product_id)
        alts = [a.model_dump() for a in detail.alternative_parts]
        return {
            "supported": True,
            "product_id": product_id,
            "part_number": detail.part_number,
            "alternatives": alts,
            "note": "替代料为 LCSC 官方标注，设计替换前请核对参数与封装。" if alts else "LCSC 未标注替代料。",
        }
    except (ProviderError, HttpError, ValueError) as exc:
        return {"error": str(exc)}


def main() -> None:
    """stdio 入口。"""
    mcp.run()


if __name__ == "__main__":
    main()
