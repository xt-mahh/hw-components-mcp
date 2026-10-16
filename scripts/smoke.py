"""Live smoke test against the real platforms (keep request count low!).

Run manually:  uv run python scripts/smoke.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from hw_components_mcp.server import (  # noqa: E402
    check_bom,
    download_datasheet,
    find_alternatives,
    get_component_detail,
    search_components,
)


def show(title: str, data) -> None:
    print(f"\n=== {title} ===")
    text = json.dumps(data, ensure_ascii=False, indent=1, default=str)
    print(text[:1200] + ("\n...[truncated]" if len(text) > 1200 else ""))


async def main() -> None:
    show("LCSC search", await search_components("STM32F103C8T6", platform="lcsc", limit=3))
    show("LCSC detail C8734", await get_component_detail("C8734", platform="lcsc"))
    show("JD search", await search_components("STM32F103C8T6", platform="jd", limit=3))
    show("HQChip search", await search_components("STM32F103C8T6", platform="hqchip", limit=3))
    show(
        "BOM check (lcsc)",
        await check_bom("STM32F103C8T6 250\nLM358DR 100", platform="lcsc"),
    )
    show("datasheet download", await download_datasheet("C8734", platform="lcsc"))
    show("alternatives", await find_alternatives("C8734", platform="lcsc"))


if __name__ == "__main__":
    asyncio.run(main())
