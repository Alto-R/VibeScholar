"""Nature.com adapter for searching and downloading papers."""

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


class NatureAdapter(BaseSiteAdapter):
    """Adapter for Nature.com and related journals."""

    name = "Nature"
    source = PaperSource.NATURE
    base_url = "https://www.nature.com"
    search_url = "https://www.nature.com/search"

    requires_auth = True
    supports_institutional_login = True

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
        Search Nature.com for papers.

        Args:
            query: Search query
            max_results: Maximum results to return
            date_from: Filter by start date
            date_to: Filter by end date
            article_type: Filter by article type (e.g., 'research', 'review')
        """
        import time

        start_time = time.time()
        papers = []

        # Build search URL
        params = [f"q={quote_plus(query)}"]

        if date_from:
            params.append(f"date_range={date_from.year}-{date_to.year if date_to else datetime.now().year}")

        if article_type:
            params.append(f"article_type={article_type}")

        search_url = f"{self.search_url}?{'&'.join(params)}"

        try:
            await self._navigate(search_url)

            # Wait for search results - look for article links
            await self.session.page.wait_for_selector(
                'a[data-track-action="view article"]',
                timeout=15000,
            )

            # Extract search results using JavaScript for better reliability
            results_data = await self.session.page.evaluate('''() => {
                const results = [];
                document.querySelectorAll('a[data-track-action="view article"]').forEach(link => {
                    const card = link.closest('article') || link.closest('.c-card') || link.parentElement;
                    if (!card) return;

                    // Get title
                    const title = link.innerText.trim();
                    const href = link.href;

                    // Get authors
                    const authorElems = card.querySelectorAll('.c-author-list__item, [itemprop="author"], .c-card__author-list span');
                    const authors = Array.from(authorElems).map(a => a.innerText.trim().replace(/,\\s*$/, '')).filter(a => a && a !== '...');

                    // Get journal
                    const journalElem = card.querySelector('.c-meta__item, [data-test="journal-title"], .c-card__journal');
                    const journal = journalElem ? journalElem.innerText.trim() : null;

                    // Get date
                    const timeElem = card.querySelector('time[datetime]');
                    const date = timeElem ? timeElem.getAttribute('datetime') : null;

                    if (title && href) {
                        results.push({ title, href, authors, journal, date });
                    }
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
                try:
                    published_date = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
                except ValueError:
                    pass

            # Extract DOI from URL
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

    async def _parse_search_result(self, article) -> Paper | None:
        """Parse a search result article element."""
        try:
            # Get title and URL
            title_elem = await article.query_selector("h3 a, h2 a, .c-card__title a")
            if not title_elem:
                return None

            title = await title_elem.inner_text()
            href = await title_elem.get_attribute("href")
            url = urljoin(self.base_url, href) if href else ""

            # Get authors
            authors = []
            author_elems = await article.query_selector_all(".c-author-list__item, .c-card__author-list span")
            for author_elem in author_elems:
                name = await author_elem.inner_text()
                name = name.strip().rstrip(",")
                if name and name != "...":
                    authors.append(Author(name=name))

            # Get journal
            journal_elem = await article.query_selector(".c-meta__item, .c-card__journal")
            journal = await journal_elem.inner_text() if journal_elem else None

            # Get date
            date_elem = await article.query_selector("time, .c-meta__item time")
            published_date = None
            if date_elem:
                date_str = await date_elem.get_attribute("datetime")
                if date_str:
                    try:
                        published_date = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
                    except ValueError:
                        pass

            # Extract DOI from URL
            doi = self._extract_doi_from_url(url)

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

    async def get_paper_details(self, url: str) -> Paper:
        """Get detailed paper information from article page."""
        await self._navigate(url)

        page = self.session.page

        # Get title
        title_elem = await page.query_selector("h1.c-article-title, h1[data-test='article-title']")
        title = await title_elem.inner_text() if title_elem else "Unknown Title"

        # Get abstract
        abstract_elem = await page.query_selector(
            "#Abs1-content, .c-article-section__content[data-test='abstract']"
        )
        abstract = await abstract_elem.inner_text() if abstract_elem else None

        # Get authors
        authors = []
        author_elems = await page.query_selector_all(
            ".c-article-author-list__item a, [data-test='author-name']"
        )
        for author_elem in author_elems:
            name = await author_elem.inner_text()
            authors.append(Author(name=name.strip()))

        # Get DOI
        doi_elem = await page.query_selector(
            "a[data-track-action='view doi'], .c-bibliographic-information__value a[href*='doi.org']"
        )
        doi = None
        if doi_elem:
            doi_href = await doi_elem.get_attribute("href")
            if doi_href:
                doi = self._extract_doi_from_url(doi_href)

        # Get journal
        journal_elem = await page.query_selector(
            ".c-article-info-details__journal-title, [data-test='journal-title']"
        )
        journal = await journal_elem.inner_text() if journal_elem else None

        # Get publication date
        date_elem = await page.query_selector("time[datetime], .c-article-info-details time")
        published_date = None
        if date_elem:
            date_str = await date_elem.get_attribute("datetime")
            if date_str:
                try:
                    published_date = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
                except ValueError:
                    pass

        # Get PDF URL
        pdf_elem = await page.query_selector(
            "a[data-track-action='download pdf'], a[href*='/pdf/']"
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
                    "a[data-track-action='download pdf'], a[href*='/pdf/']"
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
