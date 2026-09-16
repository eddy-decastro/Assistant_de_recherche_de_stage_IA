"""Package de scraping unifié et résilient (WTTJ, LinkedIn, JobTeaser).

Les exports sont **paresseux** (PEP 562) : ``from scrapers.models import RawJob``
ne charge que le modèle Pydantic, sans tirer httpx / BeautifulSoup ni les scrapers.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .manager import ScraperManager
    from .models import RawJob, ScrapeResult, ScraperConfig

__all__ = ["ScraperManager", "RawJob", "ScrapeResult", "ScraperConfig"]

_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    "ScraperManager": (".manager", "ScraperManager"),
    "RawJob": (".models", "RawJob"),
    "ScrapeResult": (".models", "ScrapeResult"),
    "ScraperConfig": (".models", "ScraperConfig"),
}


def __getattr__(name: str) -> Any:
    """Résout les exports paresseux du package (PEP 562)."""
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    module = importlib.import_module(target[0], __name__)
    value = getattr(module, target[1])
    globals()[name] = value
    return value

