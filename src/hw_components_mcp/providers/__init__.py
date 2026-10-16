"""Provider registry: platform aliases -> singleton provider instances."""

from __future__ import annotations

from .base import Provider, ProviderError
from .hqchip import HqchipProvider
from .jd import JdProvider
from .lcsc import LcscProvider

_ALIASES: dict[str, str] = {
    "lcsc": "lcsc",
    "szlcsc": "lcsc",
    "立创": "lcsc",
    "立创商城": "lcsc",
    "jd": "jd",
    "jingdong": "jd",
    "ic.jd": "jd",
    "京东": "jd",
    "京东元器件": "jd",
    "hqchip": "hqchip",
    "huaqiu": "hqchip",
    "华秋": "hqchip",
    "华秋商城": "hqchip",
    "all": "all",
}

_CLASSES: dict[str, type[Provider]] = {
    "lcsc": LcscProvider,
    "jd": JdProvider,
    "hqchip": HqchipProvider,
}

_INSTANCES: dict[str, Provider] = {}


def resolve_platform(name: str) -> str:
    key = (name or "").strip().lower()
    if key in _ALIASES:
        return _ALIASES[key]
    raise ValueError(
        f"未知平台 '{name}'；可选: lcsc(立创) / jd(京东) / hqchip(华秋) / all"
    )


def get_provider(name: str) -> Provider:
    key = resolve_platform(name)
    if key == "all":
        raise ValueError("'all' 不是单个平台；请用 all_providers() 或在工具参数中传 platform='all'")
    if key not in _INSTANCES:
        _INSTANCES[key] = _CLASSES[key]()
    return _INSTANCES[key]


def all_providers() -> dict[str, Provider]:
    return {name: get_provider(name) for name in _CLASSES}


__all__ = [
    "Provider",
    "ProviderError",
    "all_providers",
    "get_provider",
    "resolve_platform",
]
