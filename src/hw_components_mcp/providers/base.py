"""Provider abstraction: each platform implements search + detail."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import ComponentDetail, ComponentSummary


class ProviderError(Exception):
    """Platform request/parse failure with an agent-friendly Chinese message."""


class Provider(ABC):
    #: short platform id, also used in tool arguments: lcsc / jd / hqchip
    name: str = ""
    #: human-readable platform name
    display_name: str = ""
    #: note about currency / stock semantics shown by list_platforms()
    currency_note: str = ""
    #: note about network region requirements shown by list_platforms()
    region_note: str = ""

    @abstractmethod
    async def search(
        self, keyword: str, limit: int = 10, in_stock_only: bool = False
    ) -> list[ComponentSummary]:
        """Search components by keyword / part number."""

    @abstractmethod
    async def get_detail(self, product_id: str) -> ComponentDetail:
        """Fetch full detail for a platform product id (C-code / sku / goods id)."""

    async def get_datasheet_url(self, product_id: str) -> str | None:
        detail = await self.get_detail(product_id)
        return detail.datasheet_url
