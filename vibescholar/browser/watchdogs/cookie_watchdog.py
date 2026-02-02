"""Cookie consent watchdog for automatically handling cookie popups."""

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.async_api import Page

logger = logging.getLogger(__name__)


# Common cookie consent button selectors
COOKIE_CONSENT_SELECTORS = [
    # Generic accept buttons
    'button:has-text("Accept all cookies")',
    'button:has-text("Accept All")',
    'button:has-text("Accept all")',
    'button:has-text("I Accept")',
    'button:has-text("I agree")',
    'button:has-text("同意")',
    'button:has-text("接受")',
    '#accept-all-cookies',
    'button[id*="accept"]',
    # OneTrust specific
    '#onetrust-accept-btn-handler',
    'button.onetrust-close-btn-handler',
    # Cookiebot
    '#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll',
    # GDPR generic
    '.gdpr-accept',
    '[data-testid="cookie-accept"]',
]


class CookieWatchdog:
    """
    Background watchdog that monitors and handles cookie consent popups.

    Usage:
        watchdog = CookieWatchdog(page)
        await watchdog.start()
        # ... do your work ...
        await watchdog.stop()

    Or use as context manager:
        async with CookieWatchdog(page):
            # ... do your work ...
    """

    def __init__(self, page: "Page", check_interval: float = 1.0):
        """
        Initialize cookie watchdog.

        Args:
            page: Playwright page to monitor
            check_interval: How often to check for popups (seconds)
        """
        self.page = page
        self.check_interval = check_interval
        self._task: asyncio.Task | None = None
        self._running = False

    async def __aenter__(self):
        """Context manager entry."""
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        await self.stop()
        return False

    async def start(self) -> None:
        """Start the background cookie monitor."""
        if self._running:
            return

        self._running = True
        self._task = asyncio.create_task(self._monitor_loop())
        logger.info("Cookie watchdog started")

    async def stop(self) -> None:
        """Stop the background cookie monitor."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("Cookie watchdog stopped")

    async def _monitor_loop(self) -> None:
        """Background loop that monitors for cookie popups."""
        while self._running:
            try:
                await self._handle_popups()
                await asyncio.sleep(self.check_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"Cookie watchdog error (ignored): {e}")
                await asyncio.sleep(self.check_interval * 2)

    async def _handle_popups(self) -> bool:
        """
        Handle cookie consent popups.

        Returns:
            True if any popup was handled
        """
        handled = False

        # Try to click accept buttons
        for selector in COOKIE_CONSENT_SELECTORS:
            try:
                button = await self.page.query_selector(selector)
                if button and await button.is_visible():
                    await button.click()
                    await asyncio.sleep(0.5)
                    handled = True
                    logger.info(f"Cookie watchdog: clicked {selector}")
                    break
            except Exception:
                pass

        # Also try to remove overlays via JavaScript
        try:
            removed = await self.page.evaluate('''() => {
                let removed = false;

                // Remove OneTrust overlay elements
                const overlay = document.querySelector('.onetrust-pc-dark-filter');
                if (overlay) { overlay.remove(); removed = true; }

                const banner = document.querySelector('#onetrust-banner-sdk');
                if (banner) { banner.remove(); removed = true; }

                const consent = document.querySelector('#onetrust-consent-sdk');
                if (consent && consent.style.display !== 'none') {
                    consent.style.display = 'none';
                    removed = true;
                }

                // Remove generic cookie banners
                const cookieBanners = document.querySelectorAll(
                    '[class*="cookie-banner"], [class*="cookie-consent"], ' +
                    '[id*="cookie-banner"], [id*="cookie-consent"], ' +
                    '[class*="gdpr"], [id*="gdpr"]'
                );
                cookieBanners.forEach(el => {
                    if (getComputedStyle(el).position === 'fixed') {
                        el.remove();
                        removed = true;
                    }
                });

                // Remove blocking overlays
                document.querySelectorAll('[class*="overlay"], [class*="modal-backdrop"]').forEach(el => {
                    const style = getComputedStyle(el);
                    if (style.position === 'fixed' && style.zIndex > 1000) {
                        el.remove();
                        removed = true;
                    }
                });

                return removed;
            }''')
            if removed:
                handled = True
        except Exception:
            pass

        return handled

    async def handle_once(self) -> bool:
        """
        Handle cookie popups once (not in background).

        Returns:
            True if any popup was handled
        """
        return await self._handle_popups()
