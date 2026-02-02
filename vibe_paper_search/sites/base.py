"""Base adapter interface for academic sites."""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from ..papers.models import DownloadResult, Paper, PaperSource, SearchResult

if TYPE_CHECKING:
    from ..browser.session import BrowserSession


class BaseSiteAdapter(ABC):
    """Base class for academic site adapters."""

    # Site identification
    name: str
    source: PaperSource
    base_url: str

    # Authentication requirements
    requires_auth: bool = False
    supports_institutional_login: bool = False

    # Rate limiting
    requests_per_minute: int = 30
    min_request_interval: float = 0.5  # seconds

    def __init__(self, session: "BrowserSession"):
        """Initialize adapter with a browser session."""
        self.session = session
        self._last_request_time: float = 0

    @abstractmethod
    async def search(
        self,
        query: str,
        max_results: int = 20,
        **kwargs,
    ) -> SearchResult:
        """
        Search for papers.

        Args:
            query: Search query string
            max_results: Maximum number of results to return
            **kwargs: Additional search parameters (date range, filters, etc.)

        Returns:
            SearchResult containing list of papers and metadata
        """
        pass

    @abstractmethod
    async def get_paper_details(self, url: str) -> Paper:
        """
        Get detailed information about a paper.

        Args:
            url: URL of the paper page

        Returns:
            Paper with full metadata
        """
        pass

    @abstractmethod
    async def download_pdf(
        self,
        paper: Paper,
        save_path: str,
    ) -> DownloadResult:
        """
        Download PDF for a paper.

        Args:
            paper: Paper to download
            save_path: Path to save the PDF

        Returns:
            DownloadResult with success status and file path
        """
        pass

    @abstractmethod
    async def check_access(self, url: str) -> bool:
        """
        Check if we have access to the full text.

        Args:
            url: URL to check

        Returns:
            True if full text is accessible
        """
        pass

    async def login(self, credentials: dict | None = None) -> bool:
        """
        Perform login if required.

        Args:
            credentials: Optional credentials dict

        Returns:
            True if login successful or not required
        """
        if not self.requires_auth:
            return True
        # Default implementation - subclasses should override
        return False

    async def _rate_limit(self) -> None:
        """Apply rate limiting between requests."""
        import asyncio
        import time

        elapsed = time.time() - self._last_request_time
        if elapsed < self.min_request_interval:
            await asyncio.sleep(self.min_request_interval - elapsed)
        self._last_request_time = time.time()

    async def _navigate(self, url: str, wait_for_load: bool = True) -> None:
        """Navigate to URL with rate limiting."""
        await self._rate_limit()
        await self.session.goto(url)
        if wait_for_load:
            await self.session.wait_for_load()

    def _extract_doi_from_url(self, url: str) -> str | None:
        """Extract DOI from URL if present."""
        import re

        # Common DOI patterns in URLs
        patterns = [
            r"doi\.org/(10\.\d{4,}/[^\s&?#]+)",
            r"doi/(10\.\d{4,}/[^\s&?#]+)",
            r"doi=(10\.\d{4,}/[^\s&?#]+)",
        ]

        for pattern in patterns:
            match = re.search(pattern, url, re.IGNORECASE)
            if match:
                return match.group(1)

        return None
