"""Browser session management using Playwright.

This module provides:
1. BrowserSession - Core browser session with authentication persistence
2. SessionManager - Enhanced session manager with timeout cleanup and LRU eviction
3. Utility functions for browser detection and proxy configuration
"""

import asyncio
import json
import logging
import os
import socket
import sys
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import AsyncIterator, Dict, Optional

from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    async_playwright,
)

from ..config import settings

logger = logging.getLogger(__name__)


# =============================================================================
# Constants
# =============================================================================

# Browser paths by platform
BROWSER_PATHS = {
    "chrome": {
        "win32": [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        ],
        "darwin": ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"],
        "linux": [
            "/usr/bin/google-chrome",
            "/usr/bin/google-chrome-stable",
            "/usr/bin/chromium-browser",
        ],
    },
    "edge": {
        "win32": [
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        ],
        "darwin": ["/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"],
        "linux": ["/usr/bin/microsoft-edge", "/usr/bin/microsoft-edge-stable"],
    },
}

# Anti-automation detection args
BROWSER_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--disable-infobars",
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
]


# =============================================================================
# Utility Functions
# =============================================================================


def find_browser(browser_type: str = "chrome") -> str | None:
    """Find installed browser path.

    Args:
        browser_type: "chrome" or "edge"

    Returns:
        Path to browser executable or None if not found
    """
    platform = sys.platform
    paths = BROWSER_PATHS.get(browser_type, {}).get(platform, [])

    for path in paths:
        expanded_path = os.path.expandvars(path)
        if os.path.exists(expanded_path):
            logger.info(f"Found {browser_type} browser: {expanded_path}")
            return expanded_path

    return None


def detect_proxy() -> str | None:
    """Auto-detect local proxy (v2ray, clash, etc.).

    Returns:
        Proxy URL or None if not detected
    """
    if not settings.auto_detect_proxy:
        return settings.proxy_url

    if settings.proxy_url:
        return settings.proxy_url

    # Common proxy ports to check
    proxy_ports = [
        (7890, "http"),  # Clash HTTP
        (10809, "http"),  # v2ray HTTP
        (7891, "socks5"),  # Clash SOCKS5
        (10808, "socks5"),  # v2ray SOCKS5
        (1080, "socks5"),  # Generic SOCKS5
    ]

    for port, protocol in proxy_ports:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(0.5)
            result = sock.connect_ex(("127.0.0.1", port))
            sock.close()
            if result == 0:
                proxy_url = f"{protocol}://127.0.0.1:{port}"
                logger.info(f"Auto-detected proxy: {proxy_url}")
                return proxy_url
        except Exception:
            continue

    return None


# =============================================================================
# BrowserSession Class
# =============================================================================


class BrowserSession:
    """Manages a Playwright browser session with authentication persistence.

    Features:
    - Automatic storage state persistence (cookies, localStorage)
    - Proxy auto-detection
    - Support for Chrome, Edge, and Chromium browsers

    Usage:
        session = BrowserSession(session_id="sciencedirect", headless=False)
        await session.start()
        # Use session.page for browser automation
        await session.stop()
    """

    def __init__(
        self,
        session_id: str = "default",
        headless: bool | None = None,
        proxy: str | None = None,
        browser_type: str = "chrome",
    ):
        """Initialize browser session.

        Args:
            session_id: Unique identifier for this session (used for storage state)
            headless: Run browser in headless mode (default from settings)
            proxy: Proxy URL (auto-detected if not provided)
            browser_type: "chrome", "edge", or "chromium"
        """
        self.session_id = session_id
        self.headless = headless if headless is not None else settings.headless
        self.proxy = proxy or detect_proxy()
        self.browser_type = browser_type

        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None

    @property
    def storage_state_path(self) -> Path:
        """Path to storage state file for this session."""
        return settings.storage_state_dir / f"{self.session_id}_storage.json"

    @property
    def shared_storage_state_path(self) -> Path:
        """Path to shared storage state file."""
        return settings.storage_state_dir / "shared_storage.json"

    @property
    def is_connected(self) -> bool:
        """Check if browser is connected."""
        return self._browser is not None and self._browser.is_connected()

    def _load_storage_state(self) -> str | None:
        """Load storage state, preferring session-specific, falling back to shared."""
        if self.storage_state_path.exists():
            logger.info(f"Loading session storage state: {self.storage_state_path}")
            return str(self.storage_state_path)

        if self.shared_storage_state_path.exists():
            try:
                import shutil

                shutil.copy(self.shared_storage_state_path, self.storage_state_path)
                logger.info(
                    f"Copied shared storage state to session: {self.storage_state_path}"
                )
                return str(self.storage_state_path)
            except Exception as e:
                logger.warning(f"Failed to copy shared storage state: {e}")
                return str(self.shared_storage_state_path)

        logger.info("No storage state found, starting fresh session")
        return None

    async def start(self) -> None:
        """Start the browser session."""
        if self.is_connected:
            return

        settings.ensure_dirs()

        self._playwright = await async_playwright().start()

        launch_options = {
            "headless": self.headless,
            "args": BROWSER_ARGS,
        }

        if self.browser_type in ("chrome", "edge"):
            browser_path = find_browser(self.browser_type)
            if browser_path:
                launch_options["executable_path"] = browser_path
                logger.info(f"Using installed {self.browser_type}: {browser_path}")
            else:
                logger.warning(
                    f"{self.browser_type} not found, using Playwright's Chromium"
                )

        if self.proxy:
            launch_options["proxy"] = {"server": self.proxy}
            logger.info(f"Using proxy: {self.proxy}")

        self._browser = await self._playwright.chromium.launch(**launch_options)

        context_options = {"viewport": {"width": 1920, "height": 1080}}

        storage_state = self._load_storage_state()
        if storage_state:
            context_options["storage_state"] = storage_state

        self._context = await self._browser.new_context(**context_options)
        self._page = await self._context.new_page()

        logger.info(f"Browser session '{self.session_id}' started")

    async def stop(self) -> None:
        """Stop the browser session and save state."""
        if self._context:
            await self.save_storage_state()
            await self._context.close()
            self._context = None
            self._page = None

        if self._browser:
            await self._browser.close()
            self._browser = None

        if self._playwright:
            await self._playwright.stop()
            self._playwright = None

        logger.info(f"Browser session '{self.session_id}' stopped")

    async def save_storage_state(self) -> None:
        """Save current storage state (cookies, localStorage)."""
        if self._context:
            try:
                state = await self._context.storage_state()
                self.storage_state_path.parent.mkdir(parents=True, exist_ok=True)
                with open(self.storage_state_path, "w", encoding="utf-8") as f:
                    json.dump(state, f, indent=2)
                logger.info(f"Saved storage state to {self.storage_state_path}")
            except Exception as e:
                logger.error(f"Failed to save storage state: {e}")

    async def clear_storage_state(self) -> None:
        """Clear saved storage state."""
        if self.storage_state_path.exists():
            self.storage_state_path.unlink()
            logger.info(f"Cleared storage state: {self.storage_state_path}")

    @property
    def page(self) -> Page:
        """Get the current page."""
        if self._page is None:
            raise RuntimeError("Browser session not started")
        return self._page

    @property
    def context(self) -> BrowserContext:
        """Get the browser context."""
        if self._context is None:
            raise RuntimeError("Browser session not started")
        return self._context

    async def new_page(self) -> Page:
        """Create a new page in the current context."""
        if self._context is None:
            raise RuntimeError("Browser session not started")
        return await self._context.new_page()

    async def goto(self, url: str, **kwargs) -> None:
        """Navigate to a URL."""
        await self.page.goto(url, **kwargs)

    async def wait_for_load(self, timeout: int = 30000) -> None:
        """Wait for page to finish loading."""
        try:
            await self.page.wait_for_load_state("domcontentloaded", timeout=timeout)
        except Exception:
            pass

    async def screenshot(
        self, path: str | None = None, full_page: bool = False
    ) -> bytes:
        """Take a screenshot."""
        options = {"full_page": full_page}
        if path:
            options["path"] = path
        return await self.page.screenshot(**options)

    async def get_cookies(self, urls: list[str] | None = None) -> list[dict]:
        """Get cookies for specified URLs or all cookies."""
        if urls:
            return await self._context.cookies(urls)
        return await self._context.cookies()

    async def set_cookies(self, cookies: list[dict]) -> None:
        """Set cookies."""
        await self._context.add_cookies(cookies)


# =============================================================================
# Context Manager
# =============================================================================


@asynccontextmanager
async def browser_session(
    session_id: str = "default",
    headless: bool | None = None,
    proxy: str | None = None,
    browser_type: str = "chrome",
) -> AsyncIterator[BrowserSession]:
    """Context manager for browser sessions.

    Usage:
        async with browser_session("sciencedirect", headless=False) as session:
            await session.goto("https://www.sciencedirect.com")
            # Do something with session.page
    """
    session = BrowserSession(
        session_id=session_id,
        headless=headless,
        proxy=proxy,
        browser_type=browser_type,
    )
    try:
        await session.start()
        yield session
    finally:
        await session.stop()


# =============================================================================
# SessionManager Class
# =============================================================================


class SessionManager:
    """Enhanced session manager with timeout cleanup and activity tracking.

    Features:
    - Per-site session management with automatic reuse
    - Automatic cleanup of idle sessions
    - Maximum session limit with LRU eviction
    - Activity tracking for session reuse
    - Storage state persistence per site

    Usage:
        manager = SessionManager(max_sessions=5, session_timeout=600)
        session = await manager.get_session("sciencedirect", headless=False)
        # Use session...
        await manager.close_all()
    """

    def __init__(
        self,
        max_sessions: int = 5,
        session_timeout: int = 600,
        headless: bool = False,
        browser_type: str = "chrome",
        proxy: Optional[str] = None,
    ):
        """Initialize session manager.

        Args:
            max_sessions: Maximum number of concurrent sessions
            session_timeout: Session idle timeout in seconds (default 10 minutes)
            headless: Default headless mode for new sessions
            browser_type: Default browser type ("chrome", "edge", "chromium")
            proxy: Default proxy URL for all sessions
        """
        self.max_sessions = max_sessions
        self.session_timeout = session_timeout
        self.headless = headless
        self.browser_type = browser_type
        self.proxy = proxy

        self._sessions: Dict[str, BrowserSession] = {}
        self._last_activity: Dict[str, datetime] = {}
        self._lock = asyncio.Lock()

    async def get_session(
        self,
        site: str = "default",
        headless: Optional[bool] = None,
        browser_type: Optional[str] = None,
        proxy: Optional[str] = None,
    ) -> BrowserSession:
        """Get or create a session for a site.

        Args:
            site: Site identifier (e.g., "sciencedirect", "nature")
            headless: Override default headless setting
            browser_type: Override default browser type
            proxy: Override default proxy

        Returns:
            Browser session for the site
        """
        async with self._lock:
            await self._cleanup_expired()

            if site in self._sessions:
                session = self._sessions[site]
                if session.is_connected:
                    self._last_activity[site] = datetime.now()
                    logger.info(f"Reusing existing session for {site}")
                    return session
                else:
                    logger.info(f"Session for {site} disconnected, removing")
                    await self._remove_session(site)

            if len(self._sessions) >= self.max_sessions:
                await self._cleanup_oldest()

            session = await self._create_session(
                site,
                headless=headless,
                browser_type=browser_type,
                proxy=proxy,
            )
            self._sessions[site] = session
            self._last_activity[site] = datetime.now()
            logger.info(f"Created new session for {site}")
            return session

    async def _create_session(
        self,
        site: str,
        headless: Optional[bool] = None,
        browser_type: Optional[str] = None,
        proxy: Optional[str] = None,
    ) -> BrowserSession:
        """Create a new browser session."""
        session = BrowserSession(
            session_id=site,  # Use site as session_id for storage persistence
            headless=headless if headless is not None else self.headless,
            browser_type=browser_type or self.browser_type,
            proxy=proxy or self.proxy,
        )
        await session.start()
        return session

    async def _cleanup_expired(self) -> None:
        """Remove sessions that have been idle too long."""
        now = datetime.now()
        expired = []

        for site, last_time in self._last_activity.items():
            idle_seconds = (now - last_time).total_seconds()
            if idle_seconds > self.session_timeout:
                logger.info(f"Session for {site} expired (idle {idle_seconds:.0f}s)")
                expired.append(site)

        for site in expired:
            await self._remove_session(site)

    async def _cleanup_oldest(self) -> None:
        """Remove the oldest (least recently used) session."""
        if not self._last_activity:
            return

        oldest_site = min(self._last_activity, key=self._last_activity.get)
        logger.info(f"Removing oldest session: {oldest_site}")
        await self._remove_session(oldest_site)

    async def _remove_session(self, site: str) -> None:
        """Remove and close a session."""
        if site in self._sessions:
            try:
                await self._sessions[site].stop()
            except Exception as e:
                logger.warning(f"Error closing session for {site}: {e}")
            del self._sessions[site]

        if site in self._last_activity:
            del self._last_activity[site]

    async def close_session(self, site: str) -> None:
        """Close a specific session."""
        async with self._lock:
            await self._remove_session(site)

    async def close_all(self) -> None:
        """Close all sessions."""
        async with self._lock:
            sites = list(self._sessions.keys())
            for site in sites:
                await self._remove_session(site)
            logger.info("All sessions closed")

    def has_session(self, site: str) -> bool:
        """Check if a session exists for a site."""
        return site in self._sessions and self._sessions[site].is_connected

    def list_sessions(self) -> list[str]:
        """List active session site identifiers."""
        return list(self._sessions.keys())

    def get_session_info(self, site: str) -> Optional[dict]:
        """Get information about a session."""
        if site not in self._sessions:
            return None

        session = self._sessions[site]
        last_activity = self._last_activity.get(site)
        idle_seconds = (
            (datetime.now() - last_activity).total_seconds() if last_activity else 0
        )

        return {
            "site": site,
            "session_id": session.session_id,
            "is_connected": session.is_connected,
            "headless": session.headless,
            "browser_type": session.browser_type,
            "last_activity": last_activity.isoformat() if last_activity else None,
            "idle_seconds": idle_seconds,
        }

    async def refresh_session(self, site: str) -> BrowserSession:
        """Refresh a session by closing and recreating it."""
        async with self._lock:
            old_session = self._sessions.get(site)
            headless = old_session.headless if old_session else self.headless
            browser_type = old_session.browser_type if old_session else self.browser_type
            proxy = old_session.proxy if old_session else self.proxy

            await self._remove_session(site)

            session = await self._create_session(
                site,
                headless=headless,
                browser_type=browser_type,
                proxy=proxy,
            )
            self._sessions[site] = session
            self._last_activity[site] = datetime.now()
            logger.info(f"Refreshed session for {site}")
            return session

    async def __aenter__(self) -> "SessionManager":
        """Async context manager entry."""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Async context manager exit - close all sessions."""
        await self.close_all()


# =============================================================================
# Global Instance
# =============================================================================

# Global session manager instance
session_manager = SessionManager(
    max_sessions=5,
    session_timeout=600,  # 10 minutes
    headless=False,  # Default to visible mode for CAPTCHA handling
    browser_type="chrome",
)


async def cleanup_session_manager() -> None:
    """Clean up the global session manager."""
    await session_manager.close_all()
