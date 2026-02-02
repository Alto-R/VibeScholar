"""Nature.com adapter for searching and downloading papers."""

import asyncio
import logging
import re
from datetime import datetime
from urllib.parse import quote_plus, urljoin

from playwright.async_api import TimeoutError as PlaywrightTimeout

from ..browser.session import BrowserSession
from ..browser.watchdogs import AuthWatchdog, CookieWatchdog
from ..config import settings
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
        self.cookie_watchdog: CookieWatchdog | None = None

    async def _start_cookie_monitor(self) -> None:
        """Start background cookie consent monitor using CookieWatchdog."""
        if self.cookie_watchdog is None:
            self.cookie_watchdog = CookieWatchdog(self.session.page)
        await self.cookie_watchdog.start()

    async def _stop_cookie_monitor(self) -> None:
        """Stop background cookie consent monitor."""
        if self.cookie_watchdog:
            await self.cookie_watchdog.stop()

    async def _handle_cookie_consent(self) -> bool:
        """Handle cookie consent popup once using CookieWatchdog."""
        if self.cookie_watchdog is None:
            self.cookie_watchdog = CookieWatchdog(self.session.page)
        return await self.cookie_watchdog.handle_once()

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

        # Start cookie monitor in background
        await self._start_cookie_monitor()

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

            # Check for paywall - Nature-specific handling
            if await self.auth_watchdog.detect_paywall(self.session.page):
                logger.info("Paywall detected, initiating institutional access flow...")

                # Handle Nature paywall with institutional access
                auth_result = await self._handle_nature_paywall(timeout=300)
                if not auth_result:
                    return DownloadResult(
                        paper_id=paper.id,
                        success=False,
                        error="Authentication timeout - user did not complete login",
                    )

                # Re-check for PDF URL after authentication
                pdf_elem = await self.session.page.query_selector(
                    'a[data-article-pdf="true"], a[data-track-action="download pdf"], a[href*="/pdf/"]'
                )
                if pdf_elem:
                    pdf_href = await pdf_elem.get_attribute("href")
                    paper.pdf_url = urljoin(self.base_url, pdf_href) if pdf_href else paper.pdf_url

            # Download PDF - must click the download link, not navigate to PDF URL
            # Navigating directly to PDF URL opens the browser's PDF viewer instead of triggering download
            await self._navigate(paper.url)

            # Handle cookie consent popup first (may block download button)
            await self._handle_cookie_consent()

            # Wait for page to be fully loaded
            await asyncio.sleep(1)

            # Debug: print current URL
            logger.info(f"Current URL: {self.session.page.url}")
            print(f"DEBUG: Current URL: {self.session.page.url}")

            # Find the download PDF link using base class method
            nature_selectors = [
                'a[data-track-action="download pdf"]',
                'a.c-pdf-download__link',
                'a[href*="_reference.pdf"]',
            ]
            pdf_url, pdf_download_link = await self.find_pdf_link(extra_selectors=nature_selectors)

            if not pdf_url:
                # Take screenshot for debugging
                screenshot_path = str(settings.data_dir / "debug_no_download_button.png")
                await self.session.page.screenshot(path=screenshot_path)
                print(f"DEBUG: Screenshot saved to {screenshot_path}")

                return DownloadResult(
                    paper_id=paper.id,
                    success=False,
                    error="Could not find PDF download button on page",
                )

            logger.info(f"Full PDF URL: {pdf_url}")
            print(f"DEBUG: Full PDF URL: {pdf_url}")

            # Download using base class method
            result = await self.download_pdf_via_js(pdf_url, save_path)
            result.paper_id = paper.id
            return result

        except Exception as e:
            logger.error(f"PDF download failed: {e}")
            return DownloadResult(
                paper_id=paper.id,
                success=False,
                error=str(e),
            )
        finally:
            # Stop cookie monitor
            await self._stop_cookie_monitor()

    async def _handle_nature_paywall(self, timeout: int = 300) -> bool:
        """
        Handle Nature-specific paywall with institutional access.

        Flow:
        1. Find and click "Access through your institution" link
        2. Bring browser to foreground for user to complete login
        3. Wait for PDF download button to appear
        4. Click the download button

        Returns:
            True if authentication successful and PDF available, False on timeout
        """
        page = self.session.page

        # Step 1: Find and click "Access through your institution" link
        institution_link = await page.query_selector(
            'a:has-text("Access through your institution"), '
            'button:has-text("Access through your institution"), '
            '[data-track-action="institution access"]'
        )

        if institution_link:
            logger.info("Found 'Access through your institution' link, clicking...")
            print("\n" + "=" * 60)
            print("检测到付费墙，正在点击机构访问链接...")
            print("=" * 60)

            # Bring browser to front before clicking
            await page.bring_to_front()
            await institution_link.click()
            await asyncio.sleep(1)  # Wait for navigation/popup

        # Step 2: Bring browser to foreground and wait for user to complete login
        await page.bring_to_front()
        print("\n" + "=" * 60)
        print("请在浏览器窗口中完成机构登录")
        print(f"等待时间: {timeout} 秒")
        print("登录完成后，系统将自动检测并下载 PDF")
        print("=" * 60 + "\n")

        # Step 3: Wait for PDF download button to appear
        pdf_download_selector = (
            'a[data-article-pdf="true"], '
            'a.c-pdf-download__link[data-test="download-pdf"], '
            'a[data-track-action="download pdf"]'
        )

        elapsed = 0
        check_interval = 2

        while elapsed < timeout:
            # Check if PDF download button is available
            pdf_button = await page.query_selector(pdf_download_selector)

            if pdf_button:
                # Verify the button is visible and clickable
                is_visible = await pdf_button.is_visible()
                if is_visible:
                    logger.info("PDF download button detected, clicking...")
                    print("\n检测到 PDF 下载按钮，正在点击...")

                    # Step 4: Click the download button
                    pdf_href = await pdf_button.get_attribute("href")
                    if pdf_href:
                        # Update the page URL to the PDF URL for download
                        logger.info(f"PDF URL found: {pdf_href}")
                        return True

            # Also check if we navigated to a PDF page directly
            if ".pdf" in page.url.lower():
                print("\n检测到 PDF 页面，继续下载...")
                return True

            await asyncio.sleep(check_interval)
            elapsed += check_interval

            # Print progress every 10 seconds
            if elapsed % 10 == 0:
                print(f"等待用户完成机构登录... ({elapsed}/{timeout}秒)")

        print("\n等待超时，用户未完成认证")
        return False

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
