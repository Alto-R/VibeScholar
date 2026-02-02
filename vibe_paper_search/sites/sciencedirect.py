"""ScienceDirect (Elsevier) adapter for searching and downloading papers."""

import logging
import re
from datetime import datetime
from urllib.parse import quote_plus, urljoin

from playwright.async_api import TimeoutError as PlaywrightTimeout

from ..browser.session import BrowserSession
from ..browser.watchdogs import AuthWatchdog
from ..papers.models import Author, DownloadResult, Paper, PaperSource, SearchQuery, SearchResult
from .base import BaseSiteAdapter

logger = logging.getLogger(__name__)


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

    def __init__(self, session: BrowserSession):
        super().__init__(session)
        self.auth_watchdog = AuthWatchdog(session.session_id)

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

        # Build search URL
        params = [f"qs={quote_plus(query)}"]

        if date_from:
            params.append(f"date={date_from.year}-{date_to.year if date_to else datetime.now().year}")

        if article_type:
            params.append(f"articleTypes={article_type}")

        # Add show parameter for results count
        params.append(f"show={min(max_results, 100)}")

        search_url = f"{self.search_url}?{'&'.join(params)}"

        try:
            await self._navigate(search_url)

            # Wait for search results
            await self.session.page.wait_for_selector(
                ".result-item-content, .ResultItem",
                timeout=15000,
            )

            # Extract search results
            articles = await self.session.page.query_selector_all(
                ".result-item-content, .ResultItem"
            )

            for article in articles[:max_results]:
                try:
                    paper = await self._parse_search_result(article)
                    if paper:
                        papers.append(paper)
                except Exception as e:
                    logger.warning(f"Failed to parse search result: {e}")
                    continue

        except PlaywrightTimeout:
            logger.warning("Search results timeout - page may have no results")
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

    async def _parse_search_result(self, article) -> Paper | None:
        """Parse a search result article element."""
        try:
            # Get title and URL
            title_elem = await article.query_selector(
                "h2 a, .result-list-title-link, .ResultItem-title a"
            )
            if not title_elem:
                return None

            title = await title_elem.inner_text()
            href = await title_elem.get_attribute("href")
            url = urljoin(self.base_url, href) if href else ""

            # Get authors
            authors = []
            author_elems = await article.query_selector_all(
                ".author, .Authors .author, .result-item-authors span"
            )
            for author_elem in author_elems:
                name = await author_elem.inner_text()
                name = name.strip().rstrip(",").strip()
                if name and name not in ["...", "et al."]:
                    authors.append(Author(name=name))

            # Get journal
            journal_elem = await article.query_selector(
                ".srctitle-date-fields .anchor, .SubType, .result-item-source"
            )
            journal = await journal_elem.inner_text() if journal_elem else None

            # Get publication date
            date_elem = await article.query_selector(
                ".srctitle-date-fields span, .result-item-date"
            )
            published_date = None
            if date_elem:
                date_text = await date_elem.inner_text()
                # Try to parse date like "January 2024" or "2024"
                published_date = self._parse_date_text(date_text)

            # Extract DOI from URL
            doi = self._extract_doi_from_url(url)
            if not doi:
                # Try to extract PII and convert to DOI
                pii_match = re.search(r"/pii/([A-Z0-9]+)", url)
                if pii_match:
                    # PII is not DOI, but we can store it
                    pass

            return Paper(
                title=title.strip(),
                authors=authors,
                url=url,
                doi=doi,
                journal=journal.strip() if journal else None,
                published_date=published_date,
                source=self.source,
            )

        except Exception as e:
            logger.warning(f"Error parsing search result: {e}")
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
