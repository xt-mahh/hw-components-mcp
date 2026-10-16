"""Unified data models shared across platform providers.

Every provider maps its raw platform payload into these models so the MCP
tools always emit one schema regardless of the source platform.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class PriceTier(BaseModel):
    """One rung of a quantity-based ladder price."""

    quantity: int = Field(description="Minimum quantity required for this price")
    price: float
    currency: str = Field(description="Currency symbol or code, e.g. USD / CNY / ¥")


class Parameter(BaseModel):
    """A datasheet-style parameter row, e.g. Supply Voltage = 3.3V."""

    name: str
    value: str


class AlternativePart(BaseModel):
    """A substitute/replacement part for a given component."""

    source: str
    product_id: str | None = None
    part_number: str | None = None
    brand: str | None = None
    product_url: str | None = None
    note: str | None = None


class ComponentSummary(BaseModel):
    """A component as returned by a search result."""

    source: str = Field(description="Platform id: lcsc / jd / hqchip")
    product_id: str = Field(
        description="Platform-specific id (LCSC C-code, JD sku, HQChip goods id)"
    )
    part_number: str | None = Field(default=None, description="Manufacturer part number (MPN)")
    brand: str | None = None
    package: str | None = Field(default=None, description="Package / footprint, e.g. LQFP-48, 0603")
    description: str | None = None
    stock: int | None = Field(default=None, description="Stock quantity, null when unknown")
    price_tiers: list[PriceTier] = Field(default_factory=list)
    min_order_qty: int | None = Field(default=None, description="Minimum order quantity")
    product_url: str | None = None
    datasheet_url: str | None = None


class ComponentDetail(ComponentSummary):
    """Full component detail incl. parameter table and alternatives."""

    parameters: list[Parameter] = Field(default_factory=list)
    alternative_parts: list[AlternativePart] = Field(default_factory=list)
    image_url: str | None = None
    currency: str | None = None


class BomPlatformResult(BaseModel):
    """One platform's stock/price answer for one BOM line."""

    source: str
    product_id: str | None = None
    part_number: str | None = None
    stock: int | None = None
    unit_price: float | None = None
    currency: str | None = None
    shortage: int | None = Field(
        default=None,
        description="quantity minus stock; only set when both are known and stock is insufficient",
    )
    product_url: str | None = None


class BomLineResult(BaseModel):
    """Result of looking up one BOM line (part number + quantity)."""

    raw_line: str
    part_number: str
    quantity: int
    matched: bool = Field(default=False, description="Whether any platform returned a candidate")
    exact_match: bool | None = Field(
        default=None,
        description="Whether the chosen candidate's MPN equals the requested part number",
    )
    results: list[BomPlatformResult] = Field(default_factory=list)
    error: str | None = None


class BomReport(BaseModel):
    """Aggregated answer for a whole BOM check."""

    platform: str
    total_lines: int
    matched_lines: int
    lines: list[BomLineResult] = Field(default_factory=list)
