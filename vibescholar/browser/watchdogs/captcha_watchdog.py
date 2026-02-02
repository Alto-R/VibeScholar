"""CAPTCHA/human verification detection and handling watchdog."""

import asyncio
import logging
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.async_api import Page

logger = logging.getLogger(__name__)


class PageState(str, Enum):
    """Detected page state for verification detection."""
    CAPTCHA = "captcha"
    SEARCH_RESULTS = "search_results"
    NO_RESULTS = "no_results"
    ARTICLE_PAGE = "article_page"
    LOGIN_REQUIRED = "login_required"
    UNKNOWN = "unknown"


# Common CAPTCHA element selectors
DEFAULT_CAPTCHA_SELECTORS = [
    # Cloudflare Turnstile CAPTCHA
    "iframe[src*='challenges.cloudflare.com']",
    "iframe[src*='turnstile']",
    "iframe[title*='Cloudflare']",
    # PerimeterX bot detection
    "#px-captcha",
    "#px-captcha-wrapper",
    # Generic CAPTCHA containers
    "#captcha-box",
    ".captcha-container",
    ".error-card",
    # reCAPTCHA
    "iframe[src*='recaptcha']",
    ".g-recaptcha",
    "#recaptcha",
    # "Are you a robot?" heading
    "h1:has-text('Are you a robot')",
]

# Common CAPTCHA text indicators
DEFAULT_TEXT_INDICATORS = [
    "Are you a robot",
    "Please confirm you are a human",
    "completing the captcha challenge",
    "Reference number:",
]


class CaptchaWatchdog:
    """
    CAPTCHA/human verification detector and handler.

    Detects various types of CAPTCHAs and bot detection systems:
    - Cloudflare Turnstile
    - PerimeterX
    - Generic "Are you a robot?" pages

    Usage:
        watchdog = CaptchaWatchdog(page)
        state = await watchdog.detect_page_state()
        if state == PageState.CAPTCHA:
            state = await watchdog.wait_for_ready_state(timeout=120000)
    """

    def __init__(
        self,
        page: "Page",
        captcha_selectors: list[str] | None = None,
        text_indicators: list[str] | None = None,
        search_result_selectors: list[str] | None = None,
        no_results_selectors: list[str] | None = None,
        article_selectors: list[str] | None = None,
    ):
        """
        Initialize CAPTCHA watchdog.

        Args:
            page: Playwright page to monitor
            captcha_selectors: Custom CAPTCHA element selectors
            text_indicators: Custom CAPTCHA text indicators
            search_result_selectors: Selectors for search results
            no_results_selectors: Selectors for no results page
            article_selectors: Selectors for article page
        """
        self.page = page
        self.captcha_selectors = captcha_selectors or DEFAULT_CAPTCHA_SELECTORS
        self.text_indicators = text_indicators or DEFAULT_TEXT_INDICATORS
        self.search_result_selectors = search_result_selectors or []
        self.no_results_selectors = no_results_selectors or []
        self.article_selectors = article_selectors or []

    async def detect_captcha(self) -> bool:
        """
        Detect if current page has CAPTCHA.

        Returns:
            True if CAPTCHA detected
        """
        # Check for CAPTCHA elements
        for selector in self.captcha_selectors:
            try:
                elem = await self.page.query_selector(selector)
                if elem:
                    logger.info(f"CAPTCHA element detected: {selector}")
                    return True
            except Exception:
                pass

        # Check page content for CAPTCHA text indicators
        try:
            body_text = await self.page.evaluate(
                "() => document.body.innerText.substring(0, 2000)"
            )
            for indicator in self.text_indicators:
                if indicator.lower() in body_text.lower():
                    logger.info(f"CAPTCHA text found: {indicator}")
                    return True
        except Exception:
            pass

        return False

    async def detect_page_state(self) -> PageState:
        """
        Detect the current page state.

        Returns:
            PageState enum value
        """
        # Check for CAPTCHA first (most specific)
        if await self.detect_captcha():
            return PageState.CAPTCHA

        # Check for search results
        for selector in self.search_result_selectors:
            try:
                elems = await self.page.query_selector_all(selector)
                if elems and len(elems) > 0:
                    logger.info(f"Search results detected: {len(elems)} items")
                    return PageState.SEARCH_RESULTS
            except Exception:
                pass

        # Check for no results
        for selector in self.no_results_selectors:
            try:
                elem = await self.page.query_selector(selector)
                if elem:
                    return PageState.NO_RESULTS
            except Exception:
                pass

        # Check for article page
        for selector in self.article_selectors:
            try:
                elem = await self.page.query_selector(selector)
                if elem:
                    return PageState.ARTICLE_PAGE
            except Exception:
                pass

        return PageState.UNKNOWN

    async def wait_for_ready_state(
        self,
        timeout: int = 120000,
        check_interval: float = 2.0,
    ) -> PageState:
        """
        Wait for page to be in a ready state (not CAPTCHA).

        When CAPTCHA is detected, brings browser to foreground for user to solve manually.

        Args:
            timeout: Maximum wait time in milliseconds
            check_interval: Time between checks in seconds

        Returns:
            The detected page state when ready
        """
        start_time = asyncio.get_event_loop().time()
        timeout_seconds = timeout / 1000
        user_notified = False
        last_progress_time = 0

        while True:
            state = await self.detect_page_state()

            if state == PageState.CAPTCHA:
                elapsed = asyncio.get_event_loop().time() - start_time
                if elapsed > timeout_seconds:
                    logger.warning("Timeout waiting for CAPTCHA to be solved")
                    print("\n等待超时，用户未完成验证码")
                    return state

                # Bring browser to front and notify user (only once)
                if not user_notified:
                    await self.page.bring_to_front()
                    print("\n" + "=" * 60)
                    print("检测到 Cloudflare 人机验证 (Are you a robot?)")
                    print("请在浏览器中手动完成验证")
                    print(f"等待时间: {int(timeout_seconds)} 秒")
                    print("完成后系统将自动继续...")
                    print("=" * 60 + "\n")
                    user_notified = True

                # Print progress every 10 seconds
                if int(elapsed) >= last_progress_time + 10:
                    last_progress_time = int(elapsed)
                    print(f"等待用户完成验证... ({int(elapsed)}/{int(timeout_seconds)}秒)")

                logger.info(f"Waiting for CAPTCHA to be solved... ({elapsed:.0f}s)")
                await asyncio.sleep(check_interval)
                continue

            # Any other state means we can proceed
            if user_notified:
                print("\n验证完成，继续执行...")
            return state
