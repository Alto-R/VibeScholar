"""ScienceDirect (Elsevier) adapter for searching and downloading papers."""

import asyncio
import logging
import re
from datetime import datetime
from enum import Enum
from urllib.parse import quote_plus, urljoin

from playwright.async_api import TimeoutError as PlaywrightTimeout

from ..browser.session import BrowserSession
from ..browser.watchdogs import AuthWatchdog
from ..papers.models import Author, DownloadResult, Paper, PaperSource, SearchQuery, SearchResult
from .base import BaseSiteAdapter

logger = logging.getLogger(__name__)


class PageState(str, Enum):
    """Detected page state for ScienceDirect."""
    CAPTCHA = "captcha"
    SEARCH_RESULTS = "search_results"
    NO_RESULTS = "no_results"
    ARTICLE_PAGE = "article_page"
    LOGIN_REQUIRED = "login_required"
    UNKNOWN = "unknown"


class ScienceDirectAdapter(BaseSiteAdapter):
    """Adapter for ScienceDirect (Elsevier) journals."""

    name = "ScienceDirect"
    source = PaperSource.SCIENCEDIRECT
    base_url = "https://www.sciencedirect.com"
    search_url = "https://www.sciencedirect.com/search"

    requires_auth = True
    supports_institutional_login = True

    # Rate limiting - ScienceDirect is stricter
    requests_per_minute = 20
    min_request_interval = 1.0

    # Page detection selectors
    CAPTCHA_SELECTORS = [
        "iframe[src*='captcha']",
        "iframe[src*='challenge']",
        "#px-captcha",
        ".challenge-form",
        "[data-testid='challenge']",
        "div[class*='captcha']",
        # PerimeterX bot detection
        "#px-captcha-wrapper",
        "div[id*='px']",
    ]

    # Bot detection / waiting page indicators
    BOT_DETECTION_INDICATORS = [
        "请稍后",  # Chinese "please wait"
        "Please wait",
        "Checking your browser",
        "Just a moment",
        "Verifying",
    ]

    SEARCH_RESULT_SELECTORS = [
        "a[href*='/science/article/']",
        ".result-item-content",
        ".ResultItem",
        ".search-result",
    ]

    NO_RESULTS_SELECTORS = [
        ".no-results",
        "[data-testid='no-results']",
        "text='No results found'",
    ]

    # Cookie consent button selectors
    COOKIE_CONSENT_SELECTORS = [
        "button#onetrust-accept-btn-handler",  # OneTrust (common)
        "button[data-testid='accept-cookies']",
        "button.accept-cookies",
        "button[aria-label*='Accept']",
        "button[aria-label*='accept']",
        "#accept-cookies",
        ".cookie-accept",
        "button:has-text('Accept')",
        "button:has-text('Accept all')",
        "button:has-text('I agree')",
    ]

    def __init__(self, session: BrowserSession):
        super().__init__(session)
        self.auth_watchdog = AuthWatchdog(session.session_id)

    async def _handle_cookie_consent(self) -> bool:
        """Try to accept cookie consent popup if present. Keeps trying until no more popups."""
        page = self.session.page
        accepted_any = False

        # Keep trying until no more cookie popups are found
        max_attempts = 5
        for attempt in range(max_attempts):
            found_popup = False

            for selector in self.COOKIE_CONSENT_SELECTORS:
                try:
                    button = await page.query_selector(selector)
                    if button and await button.is_visible():
                        logger.info(f"Found cookie consent button: {selector}")
                        await button.click()
                        await asyncio.sleep(1)  # Wait for popup to close
                        accepted_any = True
                        found_popup = True
                        break  # Check again from the beginning
                except Exception:
                    pass

            if not found_popup:
                break  # No more popups found

        if accepted_any:
            # Save storage state after accepting cookies
            await self.session.save_storage_state()
            logger.info("Saved storage state after accepting cookies")

        return accepted_any

    async def _detect_page_state(self) -> PageState:
        """Detect the current page state."""
        page = self.session.page

        # Check page title for bot detection indicators
        try:
            title = await page.title()
            for indicator in self.BOT_DETECTION_INDICATORS:
                if indicator.lower() in title.lower():
                    logger.info(f"Bot detection page detected: {title}")
                    return PageState.CAPTCHA
        except Exception:
            pass

        # Check for CAPTCHA elements
        for selector in self.CAPTCHA_SELECTORS:
            try:
                elem = await page.query_selector(selector)
                if elem:
                    logger.info(f"CAPTCHA element detected: {selector}")
                    return PageState.CAPTCHA
            except Exception:
                pass

        # Check page content for bot detection text
        try:
            body_text = await page.evaluate("() => document.body.innerText.substring(0, 500)")
            for indicator in self.BOT_DETECTION_INDICATORS:
                if indicator.lower() in body_text.lower():
                    logger.info(f"Bot detection text found: {indicator}")
                    return PageState.CAPTCHA
        except Exception:
            pass

        # Check for search results
        for selector in self.SEARCH_RESULT_SELECTORS:
            try:
                elems = await page.query_selector_all(selector)
                if elems and len(elems) > 0:
                    logger.info(f"Search results detected: {len(elems)} items")
                    return PageState.SEARCH_RESULTS
            except Exception:
                pass

        # Check for no results
        for selector in self.NO_RESULTS_SELECTORS:
            try:
                elem = await page.query_selector(selector)
                if elem:
                    return PageState.NO_RESULTS
            except Exception:
                pass

        # Check for article page
        article_selectors = ["h1.title-text", ".article-header", "#article"]
        for selector in article_selectors:
            try:
                elem = await page.query_selector(selector)
                if elem:
                    return PageState.ARTICLE_PAGE
            except Exception:
                pass

        return PageState.UNKNOWN

    async def _wait_for_ready_state(self, timeout: int = 120000, check_interval: float = 2.0) -> PageState:
        """
        Wait for page to be in a ready state (not CAPTCHA).

        Args:
            timeout: Maximum wait time in milliseconds
            check_interval: Time between checks in seconds

        Returns:
            The detected page state when ready
        """
        start_time = asyncio.get_event_loop().time()
        timeout_seconds = timeout / 1000

        while True:
            state = await self._detect_page_state()

            if state == PageState.CAPTCHA:
                elapsed = asyncio.get_event_loop().time() - start_time
                if elapsed > timeout_seconds:
                    logger.warning("Timeout waiting for CAPTCHA to be solved")
                    return state
                logger.info(f"Waiting for CAPTCHA to be solved... ({elapsed:.0f}s)")
                await asyncio.sleep(check_interval)
                continue

            # Any other state means we can proceed
            return state

        return PageState.UNKNOWN

    async def search(
        self,
        query: str,
        max_results: int = 20,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        article_type: str | None = None,
        **kwargs,
    ) -> SearchResult:
        """
        Search ScienceDirect for papers.

        Mimics human behavior by navigating to homepage and using the search box.

        Args:
            query: Search query
            max_results: Maximum results to return
            date_from: Filter by start date
            date_to: Filter by end date
            article_type: Filter by article type
        """
        import time

        start_time = time.time()
        papers = []

        try:
            # Step 1: Navigate to homepage (like a human would)
            logger.info("Navigating to ScienceDirect homepage...")
            await self._navigate(self.base_url)

            # Wait for page to be ready
            page_state = await self._wait_for_ready_state(timeout=120000)
            if page_state == PageState.CAPTCHA:
                logger.warning("CAPTCHA on homepage - waiting for user")

            # Step 1.5: Handle cookie consent popup if present
            if await self._handle_cookie_consent():
                logger.info("Accepted cookie consent")

            # Step 2: Find and interact with search box
            page = self.session.page

            # Wait for search input to be available
            search_input = await page.wait_for_selector(
                'input[type="search"], input[name="qs"], input[placeholder*="Search"], #qs',
                timeout=30000
            )

            if not search_input:
                logger.error("Could not find search input")
                return self._empty_result(query, max_results, time.time() - start_time)

            # Step 3: Clear and type query (human-like)
            logger.info(f"Typing search query: {query}")
            await search_input.click()
            await asyncio.sleep(0.3)  # Small delay like human
            await search_input.fill("")  # Clear first
            await search_input.type(query, delay=50)  # Type with delay like human

            # Step 4: Submit search (press Enter or click button)
            await asyncio.sleep(0.5)  # Small pause before submit

            # Get current URL before submitting
            current_url = page.url

            await search_input.press("Enter")

            # Step 5: Wait for URL to change (navigation to search results page)
            logger.info("Waiting for navigation to search results...")
            try:
                await page.wait_for_url(
                    lambda url: url != current_url and "/search" in url,
                    timeout=30000
                )
            except Exception as e:
                logger.warning(f"URL change timeout: {e}")

            # Wait a bit for the page to fully load
            await asyncio.sleep(2)

            # Step 6: Wait for search results page to be ready
            logger.info("Waiting for search results to load...")
            page_state = await self._wait_for_ready_state(timeout=120000)

            if page_state == PageState.CAPTCHA:
                logger.warning("CAPTCHA not solved within timeout")
                return self._empty_result(query, max_results, time.time() - start_time)

            if page_state == PageState.NO_RESULTS:
                logger.info("No search results found")
                return self._empty_result(query, max_results, time.time() - start_time)

            # Step 6: Extract search results using JavaScript
            results_data = await self.session.page.evaluate('''() => {
                const results = [];
                document.querySelectorAll('a[href*="/science/article/"]').forEach(link => {
                    const text = link.innerText.trim();
                    if (!text || text.length < 10) return;

                    const container = link.closest('.result-item-content') ||
                                     link.closest('.ResultItem') ||
                                     link.closest('li') ||
                                     link.parentElement;
                    if (!container) return;

                    const href = link.href;
                    if (results.some(r => r.href === href)) return;

                    const authorElems = container.querySelectorAll('.author, .Authors .author, [class*="author"] span');
                    const authors = Array.from(authorElems)
                        .map(a => a.innerText.trim().replace(/,\\s*$/, ''))
                        .filter(a => a && a !== '...' && a !== 'et al.' && a.length > 1);

                    const journalElem = container.querySelector('.srctitle-date-fields .anchor, .SubType, [class*="source"]');
                    const journal = journalElem ? journalElem.innerText.trim() : null;

                    const dateElem = container.querySelector('.srctitle-date-fields span, [class*="date"]');
                    const date = dateElem ? dateElem.innerText.trim() : null;

                    results.push({
                        title: text,
                        href: href,
                        authors: authors.slice(0, 10),
                        journal: journal,
                        date: date
                    });
                });
                return results;
            }''')

            for data in results_data[:max_results]:
                try:
                    paper = self._parse_search_data(data)
                    if paper:
                        papers.append(paper)
                except Exception as e:
                    logger.warning(f"Failed to parse search result: {e}")
                    continue

        except PlaywrightTimeout as e:
            logger.warning(f"Timeout during search: {e}")
        except Exception as e:
            logger.error(f"Search failed: {e}")

        search_time = time.time() - start_time

        return SearchResult(
            papers=papers,
            total_count=len(papers),
            query=SearchQuery(query=query, max_results=max_results),
            search_time=search_time,
            source=self.source,
            has_more=len(papers) >= max_results,
        )

    def _empty_result(self, query: str, max_results: int, search_time: float) -> SearchResult:
        """Return an empty search result."""
        return SearchResult(
            papers=[],
            total_count=0,
            query=SearchQuery(query=query, max_results=max_results),
            search_time=search_time,
            source=self.source,
            has_more=False,
        )

    def _parse_search_data(self, data: dict) -> Paper | None:
        """Parse search result data from JavaScript extraction."""
        try:
            title = data.get("title", "").strip()
            href = data.get("href", "")
            if not title or not href:
                return None

            url = urljoin(self.base_url, href)

            # Parse authors
            authors = [Author(name=name) for name in data.get("authors", []) if name]

            # Parse journal
            journal = data.get("journal")

            # Parse date
            published_date = None
            date_str = data.get("date")
            if date_str:
                published_date = self._parse_date_text(date_str)

            # Extract DOI from URL or PII
            doi = self._extract_doi_from_url(url)

            return Paper(
                title=title,
                authors=authors,
                url=url,
                doi=doi,
                journal=journal.strip() if journal else None,
                published_date=published_date,
                source=self.source,
            )

        except Exception as e:
            logger.warning(f"Error parsing search data: {e}")
            return None

    def _parse_date_text(self, date_text: str) -> datetime | None:
        """Parse date from text like 'January 2024' or '2024'."""
        import calendar

        date_text = date_text.strip()

        # Try full date formats
        formats = [
            "%B %Y",  # January 2024
            "%b %Y",  # Jan 2024
            "%Y",  # 2024
            "%d %B %Y",  # 15 January 2024
            "%B %d, %Y",  # January 15, 2024
        ]

        for fmt in formats:
            try:
                return datetime.strptime(date_text, fmt)
            except ValueError:
                continue

        # Try to extract year
        year_match = re.search(r"(\d{4})", date_text)
        if year_match:
            try:
                return datetime(int(year_match.group(1)), 1, 1)
            except ValueError:
                pass

        return None

    async def get_paper_details(self, url: str) -> Paper:
        """Get detailed paper information from article page."""
        await self._navigate(url)

        # Wait for page to be ready (handles CAPTCHA)
        page_state = await self._wait_for_ready_state(timeout=120000)

        if page_state == PageState.CAPTCHA:
            logger.warning("CAPTCHA not solved - returning minimal paper info")
            return Paper(
                title="CAPTCHA blocked",
                authors=[],
                url=url,
                source=self.source,
            )

        page = self.session.page

        # Get title
        title_elem = await page.query_selector(
            "h1.title-text, .title-text, span.title-text"
        )
        title = await title_elem.inner_text() if title_elem else "Unknown Title"

        # Get abstract
        abstract_elem = await page.query_selector(
            "#abstracts .abstract, .abstract.author, div[id='abs0010']"
        )
        abstract = None
        if abstract_elem:
            abstract = await abstract_elem.inner_text()

        # Get authors
        authors = []
        author_elems = await page.query_selector_all(
            ".author-group .author .content span.text, .AuthorGroups .author"
        )
        for author_elem in author_elems:
            name = await author_elem.inner_text()
            name = name.strip()
            if name:
                authors.append(Author(name=name))

        # Get DOI
        doi_elem = await page.query_selector(
            "a.doi, a[href*='doi.org']"
        )
        doi = None
        if doi_elem:
            doi_href = await doi_elem.get_attribute("href")
            if doi_href:
                doi = self._extract_doi_from_url(doi_href)

        # Get journal
        journal_elem = await page.query_selector(
            ".publication-title-link, .title-link"
        )
        journal = await journal_elem.inner_text() if journal_elem else None

        # Get publication date
        date_elem = await page.query_selector(
            ".publication-volume .text-xs, .volIssue"
        )
        published_date = None
        if date_elem:
            date_text = await date_elem.inner_text()
            published_date = self._parse_date_text(date_text)

        # Get PDF URL
        pdf_elem = await page.query_selector(
            "a.pdf-download, a[href*='/pdf/'], .PdfLink a"
        )
        pdf_url = None
        if pdf_elem:
            pdf_href = await pdf_elem.get_attribute("href")
            if pdf_href:
                pdf_url = urljoin(self.base_url, pdf_href)

        return Paper(
            title=title.strip(),
            authors=authors,
            abstract=abstract.strip() if abstract else None,
            url=url,
            doi=doi,
            journal=journal.strip() if journal else None,
            published_date=published_date,
            pdf_url=pdf_url,
            source=self.source,
        )

    async def download_pdf(self, paper: Paper, save_path: str) -> DownloadResult:
        """Download PDF for a paper."""
        if not paper.pdf_url and not paper.url:
            return DownloadResult(
                paper_id=paper.id,
                success=False,
                error="No PDF URL available",
            )

        try:
            # Navigate to paper page if needed
            if not paper.pdf_url:
                await self._navigate(paper.url)
                # Try to find PDF link
                pdf_elem = await self.session.page.query_selector(
                    "a.pdf-download, a[href*='/pdf/'], .PdfLink a"
                )
                if pdf_elem:
                    pdf_href = await pdf_elem.get_attribute("href")
                    paper.pdf_url = urljoin(self.base_url, pdf_href) if pdf_href else None

            if not paper.pdf_url:
                return DownloadResult(
                    paper_id=paper.id,
                    success=False,
                    error="Could not find PDF download link",
                )

            # Check for paywall
            if await self.auth_watchdog.detect_paywall(self.session.page):
                return DownloadResult(
                    paper_id=paper.id,
                    success=False,
                    error="Paywall detected - institutional login may be required",
                )

            # Download PDF
            async with self.session.page.expect_download() as download_info:
                await self.session.page.goto(paper.pdf_url)

            download = await download_info.value
            await download.save_as(save_path)

            # Get file size
            from pathlib import Path

            file_size = Path(save_path).stat().st_size

            return DownloadResult(
                paper_id=paper.id,
                success=True,
                pdf_path=save_path,
                file_size=file_size,
            )

        except Exception as e:
            logger.error(f"PDF download failed: {e}")
            return DownloadResult(
                paper_id=paper.id,
                success=False,
                error=str(e),
            )

    async def check_access(self, url: str) -> bool:
        """Check if we have access to the full text."""
        await self._navigate(url)

        # Check for paywall indicators
        if await self.auth_watchdog.detect_paywall(self.session.page):
            return False

        # Check for PDF availability
        if await self.auth_watchdog.detect_pdf_available(self.session.page):
            return True

        return False
