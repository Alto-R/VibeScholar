"""Nature.com adapter for searching and downloading papers."""

import asyncio
import logging
import re
from datetime import datetime
from urllib.parse import quote_plus, urljoin

from playwright.async_api import TimeoutError as PlaywrightTimeout

from ..browser.session import BrowserSession
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
            # Step 1: Navigate to paper page
            await self._navigate(paper.url)

            # Step 2: Handle cookie consent popup first (may block other elements)
            await self._handle_cookie_consent()
            await asyncio.sleep(1)

            # Step 3: Check for paywall FIRST - before looking for PDF link
            # This ensures users have a chance to authenticate before we give up
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

                # Re-navigate after authentication to get the authenticated page
                await self._navigate(paper.url)
                await self._handle_cookie_consent()
                await asyncio.sleep(1)

                # Check if institution doesn't have access (shows "Access to this article via X is not available")
                page_content = await self.session.page.evaluate("document.body.innerText")
                if "is not available" in page_content.lower() and "access to this article via" in page_content.lower():
                    logger.warning("Institution does not have access to this journal")
                    print("\n" + "=" * 60)
                    print("您的机构没有订阅此期刊的权限")
                    print("请确认机构订阅范围")
                    print("=" * 60 + "\n")
                    return DownloadResult(
                        paper_id=paper.id,
                        success=False,
                        error="Institution does not have access to this journal - please contact your library",
                    )

            # Step 4: Now try to find PDF link (after potential authentication)
            logger.info(f"Current URL: {self.session.page.url}")
            print(f"DEBUG: Current URL: {self.session.page.url}")

            nature_selectors = [
                'a[data-track-action="download pdf"]',
                'a.c-pdf-download__link',
                'a[href*="_reference.pdf"]',
                'a[href*="/pdf/"]',
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

            # Step 5: Download PDF by clicking the actual button
            # This is more reliable than JavaScript-created links for Nature
            if pdf_download_link:
                try:
                    print("正在点击下载按钮...")
                    async with self.session.page.expect_download(timeout=60000) as download_info:
                        await pdf_download_link.click()

                    download = await download_info.value
                    await download.save_as(save_path)

                    from pathlib import Path
                    file_size = Path(save_path).stat().st_size
                    print(f"PDF 下载成功! 文件大小: {file_size / 1024:.1f} KB")

                    return DownloadResult(
                        paper_id=paper.id,
                        success=True,
                        pdf_path=save_path,
                        file_size=file_size,
                    )
                except Exception as e:
                    logger.warning(f"Button click download failed: {e}, trying JavaScript method...")
                    print(f"按钮点击下载失败，尝试 JavaScript 方法...")

            # Fallback: Download using JavaScript method
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
            try:
                await institution_link.click()
            except Exception as e:
                # Click may trigger navigation, causing element to detach - this is expected
                logger.warning(f"Click triggered navigation (expected): {e}")
            await asyncio.sleep(2)  # Wait for navigation to start

        # Step 2: Bring browser to foreground and wait for user to complete login
        await page.bring_to_front()
        print("\n" + "=" * 60)
        print("请在浏览器窗口中完成机构登录")
        print(f"等待时间: {timeout} 秒")
        print("登录完成后，系统将自动检测并下载 PDF")
        print("=" * 60 + "\n")

        # Step 3: Wait for authentication to complete
        # Check for: paywall gone AND PDF available, OR navigated to PDF page
        elapsed = 0
        check_interval = 2

        while elapsed < timeout:
            try:
                # Method 1: Check if paywall is gone and PDF is available
                has_paywall = await self.auth_watchdog.detect_paywall(page)
                has_pdf = await self.auth_watchdog.detect_pdf_available(page)

                if not has_paywall and has_pdf:
                    logger.info("Authentication successful - paywall gone, PDF available")
                    print("\n检测到已获得访问权限，继续下载...")
                    # Save storage state after successful authentication
                    await self.session.save_storage_state()
                    print("已保存认证状态，下次下载无需重新登录")
                    return True

                # Method 2: Check if we navigated to a PDF page directly
                if ".pdf" in page.url.lower():
                    print("\n检测到 PDF 页面，继续下载...")
                    await self.session.save_storage_state()
                    print("已保存认证状态，下次下载无需重新登录")
                    return True

                # Method 3: Check if we're back on the article page (not on WAYF/login page)
                # and the page has a PDF download button
                current_url = page.url.lower()
                if "nature.com/articles" in current_url and not has_paywall:
                    # We're on the article page without paywall - check for any PDF link
                    pdf_link = await page.query_selector('a[href*="/pdf/"], a[href*=".pdf"]')
                    if pdf_link:
                        logger.info("Back on article page with PDF access")
                        print("\n检测到已返回文章页面并获得访问权限...")
                        await self.session.save_storage_state()
                        print("已保存认证状态，下次下载无需重新登录")
                        return True

            except Exception:
                # Page is navigating during login, continue waiting
                pass

            await asyncio.sleep(check_interval)
            elapsed += check_interval

            # Print progress every 10 seconds
            if elapsed % 10 == 0:
                print(f"等待用户完成机构登录... ({elapsed}/{timeout}秒)")

        print("\n等待超时，用户未完成认证")
        return False
