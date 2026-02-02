"""Sites module - academic site adapters."""

from .base import BaseSiteAdapter
from .nature import NatureAdapter
from .sciencedirect import ScienceDirectAdapter

__all__ = [
    "BaseSiteAdapter",
    "NatureAdapter",
    "ScienceDirectAdapter",
]
