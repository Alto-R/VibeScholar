"""Base adapter interface for academic sites."""

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from ..papers.models import DownloadResult, Paper, PaperSource, SearchResult

if TYPE_CHECKING:
    from ..browser.session import BrowserSession

logger = logging.getLogger(__name__)


# Common cookie consent button selectors
# Only click "Accept all cookies" button to avoid triggering cookie management pages
COOKIE_CONSENT_SELECTORS = [
    'button:has-text("Accept all cookies")',
]


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
        """Navigate to URL with rate limiting and auto-accept cookies."""
        await self._rate_limit()
        await self.session.goto(url)
        if wait_for_load:
            await self.session.wait_for_load()
        # Auto-accept cookies after page load
        await self._try_accept_cookies()

    async def _detect_cookie_dialog(self) -> bool:
        """Detect if a cookie consent dialog is present."""
        page = self.session.page
        for selector in COOKIE_CONSENT_SELECTORS:
            try:
                button = await page.query_selector(selector)
                if button and await button.is_visible():
                    logger.info(f"Cookie dialog detected with selector: {selector}")
                    return True
            except Exception:
                continue
        return False

    async def _try_accept_cookies(self) -> bool:
        """Try to automatically accept cookie consent dialog if present."""
        page = self.session.page

        for selector in COOKIE_CONSENT_SELECTORS:
            try:
                button = await page.query_selector(selector)
                if button and await button.is_visible():
                    # Bring browser to front so user can see the action
                    await page.bring_to_front()
                    await button.click()
                    logger.info(f"Auto-accepted cookies using selector: {selector}")
                    print(f"已自动接受 Cookie: {selector}")
                    await asyncio.sleep(0.5)  # Wait for dialog to close
                    return True
            except Exception:
                continue
        return False

    async def wait_for_user_auth(
        self,
        timeout: int = 300,
        check_interval: int = 2,
    ) -> bool:
        """
        Wait for user to complete authentication/login.

        Shows a message and waits for the page to become accessible.
        Returns True if PDF becomes available, False on timeout.

        Args:
            timeout: Maximum wait time in seconds
            check_interval: How often to check for access in seconds
        """
        from ..browser.watchdogs import AuthWatchdog

        page = self.session.page
        watchdog = AuthWatchdog(self.session.session_id)

        print("\n" + "=" * 60)
        print("需要登录/认证才能下载此论文")
        print("请在浏览器窗口中完成登录操作")
        print(f"等待时间: {timeout} 秒")
        print("=" * 60 + "\n")

        elapsed = 0
        while elapsed < timeout:
            # Check if paywall is gone and PDF is available
            has_paywall = await watchdog.detect_paywall(page)
            has_pdf = await watchdog.detect_pdf_available(page)

            if not has_paywall and has_pdf:
                print("\n检测到已获得访问权限，继续下载...")
                return True

            # Also check if we navigated to a PDF page directly
            if ".pdf" in page.url.lower():
                print("\n检测到 PDF 页面，继续下载...")
                return True

            await asyncio.sleep(check_interval)
            elapsed += check_interval

            # Print progress every 10 seconds
            if elapsed % 10 == 0:
                print(f"等待用户操作... ({elapsed}/{timeout}秒)")

        print("\n等待超时，用户未完成认证")
        return False

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
