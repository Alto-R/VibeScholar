"""ScienceDirect (Elsevier) adapter for searching and downloading papers."""

import asyncio
import logging
import re
from datetime import datetime
from urllib.parse import quote_plus, urljoin

from playwright.async_api import TimeoutError as PlaywrightTimeout

from ..browser.session import BrowserSession
from ..browser.watchdogs import CaptchaWatchdog, PageState
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

    # Page detection selectors
    # ScienceDirect specific CAPTCHA page selectors
    CAPTCHA_SELECTORS = [
        # Cloudflare Turnstile CAPTCHA
        "iframe[src*='challenges.cloudflare.com']",
        "iframe[src*='turnstile']",
        "iframe[title*='Cloudflare']",
        # ScienceDirect CAPTCHA page structure
        ".error-card",  # Error card container
        "#captcha-box",  # CAPTCHA container
        "h1:has-text('Are you a robot')",  # "Are you a robot?" heading
        # PerimeterX bot detection
        "#px-captcha",
        "#px-captcha-wrapper",
    ]

    # ScienceDirect specific CAPTCHA page text indicators
    # These are checked in page content
    CAPTCHA_TEXT_INDICATORS = [
        "Are you a robot",
        "Please confirm you are a human",
        "completing the captcha challenge",
        "Reference number:",  # This appears on the CAPTCHA page
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

    def __init__(self, session: BrowserSession):
        super().__init__(session)
        self.captcha_watchdog: CaptchaWatchdog | None = None

    async def _handle_captcha_globally(self) -> bool:
        """Handle CAPTCHA using global lock mechanism from base class."""
        return await self.handle_captcha()

    def _get_captcha_watchdog(self) -> CaptchaWatchdog:
        """Get or create CaptchaWatchdog instance."""
        if self.captcha_watchdog is None:
            self.captcha_watchdog = CaptchaWatchdog(
                page=self.session.page,
                captcha_selectors=self.CAPTCHA_SELECTORS,
                text_indicators=self.CAPTCHA_TEXT_INDICATORS,
                search_result_selectors=self.SEARCH_RESULT_SELECTORS,
                no_results_selectors=self.NO_RESULTS_SELECTORS,
                article_selectors=["h1.title-text", ".article-header", "#article"],
            )
        return self.captcha_watchdog

    async def _wait_for_ready_state(self, timeout: int = 120000) -> PageState:
        """Wait for page to be in a ready state using CaptchaWatchdog."""
        watchdog = self._get_captcha_watchdog()
        return await watchdog.wait_for_ready_state(timeout=timeout)

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

        # Start cookie monitor in background
        await self._start_cookie_monitor()

        try:
            # Step 1: Navigate to homepage (like a human would)
            logger.info("Navigating to ScienceDirect homepage...")
            await self._navigate(self.base_url)

            # Wait for page to be ready (handles CAPTCHA with user notification)
            page_state = await self._wait_for_ready_state(timeout=120000)
            if page_state == PageState.CAPTCHA:
                logger.warning("CAPTCHA not solved on homepage - returning empty result")
                return self._empty_result(query, max_results, time.time() - start_time)

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
        finally:
            # Stop cookie monitor
            await self._stop_cookie_monitor()

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
        """
        Download PDF for a paper.

        Flow:
        1. Navigate to paper page, handle CAPTCHA if appears
        2. Find and click PDF link
        3. Handle second CAPTCHA if appears
        4. Download from web PDF viewer page
        """
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
            logger.info(f"Navigating to paper page: {paper.url}")
            await self._navigate(paper.url)

            # Wait for page to be ready (handles first CAPTCHA)
            logger.info("Waiting for article page to load (first CAPTCHA check)...")
            page_state = await self._wait_for_ready_state(timeout=120000)
            if page_state == PageState.CAPTCHA:
                return DownloadResult(
                    paper_id=paper.id,
                    success=False,
                    error="CAPTCHA not solved - user did not complete verification",
                )

            # Step 2: Handle cookie consent popup before clicking PDF link
            await self._handle_cookie_consent()

            # Step 2.5: Check for paywall and handle authentication if needed
            if await self.auth_watchdog.detect_paywall(self.session.page):
                logger.info("Paywall detected, initiating institutional access flow...")

                # Click institution access link if available
                institution_link = await self.session.page.query_selector(
                    'a:has-text("Access through your institution"), '
                    'a:has-text("Access through your organization")'
                )
                if institution_link:
                    await self.session.page.bring_to_front()
                    try:
                        await institution_link.click()
                    except Exception as e:
                        # Click may trigger navigation, causing element to detach
                        logger.warning(f"Click triggered navigation (expected): {e}")
                    await asyncio.sleep(2)

                # Use base class method to wait for user authentication
                if not await self.wait_for_user_auth(timeout=300):
                    return DownloadResult(
                        paper_id=paper.id,
                        success=False,
                        error="Authentication timeout - user did not complete login",
                    )

                # Save storage state after successful authentication
                await self.session.save_storage_state()
                print("已保存认证状态，下次下载无需重新登录")

                # Check if institution doesn't have access after authentication
                page_content = await self.session.page.evaluate("document.body.innerText")
                page_content_lower = page_content.lower()
                # ScienceDirect may show different messages for no access
                no_access_indicators = [
                    "is not available",
                    "not subscribed",
                    "no access",
                    "purchase this article",
                    "get access",
                ]
                if any(indicator in page_content_lower for indicator in no_access_indicators):
                    # Double check - if we still see paywall after auth, institution has no access
                    if await self.auth_watchdog.detect_paywall(self.session.page):
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

            # Find PDF link using base class method
            logger.info("Looking for PDF download link...")
            sd_selectors = [
                "a[href*='pdfft']",
            ]
            pdf_url, pdf_elem = await self.find_pdf_link(extra_selectors=sd_selectors)

            if not pdf_url:
                # No PDF link found - likely no access
                return DownloadResult(
                    paper_id=paper.id,
                    success=False,
                    error="No access to this paper - requires purchase or institutional login",
                )

            # Update paper.pdf_url
            paper.pdf_url = pdf_url
            logger.info(f"Found PDF link: {paper.pdf_url}")

            # Step 3: Click PDF link to navigate to viewer (with retry and force click if needed)
            if pdf_elem:
                print("\n正在点击 View PDF 链接...")
                await self.session.page.bring_to_front()

                try:
                    await pdf_elem.click(timeout=10000)
                except Exception as click_error:
                    logger.warning(f"Normal click failed: {click_error}, trying force click...")
                    await self.session.page.evaluate('(el) => el.click()', pdf_elem)

                await asyncio.sleep(2)  # Wait for navigation

            # Wait for second CAPTCHA if it appears
            logger.info("Checking for second CAPTCHA on PDF page...")
            page_state = await self._wait_for_ready_state(timeout=120000)
            if page_state == PageState.CAPTCHA:
                return DownloadResult(
                    paper_id=paper.id,
                    success=False,
                    error="CAPTCHA not solved on PDF page - user did not complete verification",
                )

            # Step 4: Now we should be on the web PDF viewer page
            logger.info("Attempting to download PDF from viewer page...")
            print("进入 PDF 查看器页面，正在尝试下载...")

            # Try to extract PDF URL from viewer page
            current_url = self.session.page.url
            print(f"DEBUG: Current URL in viewer: {current_url}")

            final_pdf_url = await self._extract_pdf_url_from_viewer()

            if not final_pdf_url:
                # If no PDF URL found, check if current URL is a direct PDF URL
                if ".pdf" in current_url.lower() and "reader" not in current_url.lower():
                    final_pdf_url = current_url
                elif "/pdf/" in current_url or "pdfft" in current_url:
                    final_pdf_url = current_url.replace("/reader/", "/")

            if not final_pdf_url:
                return DownloadResult(
                    paper_id=paper.id,
                    success=False,
                    error="Could not find PDF URL to download",
                )

            # Download using base class method
            result = await self.download_pdf_via_js(final_pdf_url, save_path)
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

    async def _extract_pdf_url_from_viewer(self) -> str | None:
        """Extract PDF URL from web PDF viewer page."""
        page = self.session.page

        # Try to find PDF URL in various places
        # 1. Check for iframe with PDF
        iframe = await page.query_selector("iframe[src*='.pdf'], iframe[src*='pdf']")
        if iframe:
            src = await iframe.get_attribute("src")
            if src:
                return urljoin(self.base_url, src)

        # 2. Check for embed or object element
        embed = await page.query_selector("embed[src*='.pdf'], object[data*='.pdf']")
        if embed:
            src = await embed.get_attribute("src") or await embed.get_attribute("data")
            if src:
                return urljoin(self.base_url, src)

        # 3. Try to extract from JavaScript or page source
        try:
            pdf_url = await page.evaluate('''() => {
                // Check for common PDF viewer variables
                if (window.defined && window.defined.pdfUrl) return window.defined.pdfUrl;
                if (window.defined && window.defined.pdfSrc) return window.defined.pdfSrc;

                // Check for links in the page
                const links = document.querySelectorAll('a[href*=".pdf"]');
                for (let link of links) {
                    if (!link.href.includes('reader')) return link.href;
                }

                // Check meta tags
                const meta = document.querySelector('meta[name="citation_pdf_url"]');
                if (meta) return meta.content;

                return null;
            }''')
            if pdf_url:
                return pdf_url
        except Exception:
            pass

        return None

    async def _extract_search_results_via_dom_service(self) -> list[dict]:
        """
        Extract search results using DOMService for more reliable extraction.

        Returns:
            List of dicts with title, href, authors, journal, date
        """
        # Use DOMService from base class
        dom = self.dom_service

        # Extract article links
        links = await dom.extract_links(
            selector="a[href*='/science/article/']",
            filter_pattern=r"/science/article/"
        )

        results = []
        seen_hrefs = set()

        for link in links:
            href = link.get("href", "")
            title = link.get("text", "").strip()

            # Skip duplicates and short titles
            if href in seen_hrefs or not title or len(title) < 10:
                continue
            seen_hrefs.add(href)

            results.append({
                "title": title,
                "href": href,
                "authors": [],  # Will be extracted separately if needed
                "journal": None,
                "date": None
            })

        return results

    async def _extract_paper_details_via_dom_service(self) -> dict:
        """
        Extract paper details using DOMService.

        Returns:
            Dict with title, abstract, authors, doi, journal, date, pdf_url
        """
        dom = self.dom_service

        # Extract title
        title_elements = await dom.extract_elements(
            selectors=["h1.title-text", ".title-text", "span.title-text"],
            include_children=False
        )
        title = title_elements[0].text if title_elements else "Unknown Title"

        # Extract abstract
        abstract_elements = await dom.extract_elements(
            selectors=["#abstracts .abstract", ".abstract.author", "div[id='abs0010']"],
            include_children=False
        )
        abstract = abstract_elements[0].text if abstract_elements else None

        # Extract authors
        author_elements = await dom.extract_elements(
            selectors=[".author-group .author .content span.text", ".AuthorGroups .author"],
            include_children=False
        )
        authors = [elem.text.strip() for elem in author_elements if elem.text.strip()]

        # Extract DOI link
        doi_links = await dom.extract_links(
            selector="a.doi, a[href*='doi.org']",
            filter_pattern=r"doi\.org"
        )
        doi = None
        if doi_links:
            doi = self._extract_doi_from_url(doi_links[0].get("href", ""))

        # Extract journal
        journal_elements = await dom.extract_elements(
            selectors=[".publication-title-link", ".title-link"],
            include_children=False
        )
        journal = journal_elements[0].text.strip() if journal_elements else None

        # Extract PDF URL
        pdf_links = await dom.extract_links(
            selector="a.pdf-download, a[href*='/pdf/'], .PdfLink a",
            filter_pattern=r"/pdf/|pdfft"
        )
        pdf_url = pdf_links[0].get("href") if pdf_links else None

        return {
            "title": title,
            "abstract": abstract,
            "authors": authors,
            "doi": doi,
            "journal": journal,
            "pdf_url": pdf_url
        }
