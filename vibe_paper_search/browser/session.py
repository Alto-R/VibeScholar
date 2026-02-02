"""Browser session management using Playwright."""

import asyncio
import json
import logging
import os
import socket
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright

from ..config import settings

logger = logging.getLogger(__name__)


# Browser paths by platform
BROWSER_PATHS = {
    "chrome": {
        "win32": [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        ],
        "darwin": ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"],
        "linux": ["/usr/bin/google-chrome", "/usr/bin/google-chrome-stable", "/usr/bin/chromium-browser"],
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


def find_browser(browser_type: str = "chrome") -> str | None:
    """Find installed browser path.

    Args:
        browser_type: "chrome" or "edge"
    """
    platform = sys.platform
    paths = BROWSER_PATHS.get(browser_type, {}).get(platform, [])

    for path in paths:
        # Expand environment variables
        expanded_path = os.path.expandvars(path)
        if os.path.exists(expanded_path):
            logger.info(f"Found {browser_type} browser: {expanded_path}")
            return expanded_path

    return None


def find_edge_browser() -> str | None:
    """Find installed Edge browser path. (Legacy function for compatibility)"""
    return find_browser("edge")


def detect_proxy() -> str | None:
    """Auto-detect local proxy (v2ray, clash, etc.)."""
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


class BrowserSession:
    """Manages a Playwright browser session with authentication persistence."""

    def __init__(
        self,
        session_id: str = "default",
        headless: bool | None = None,
        proxy: str | None = None,
        browser_type: str = "chrome",  # "chrome", "edge", or "chromium" (bundled)
    ):
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
        # 1. Check session-specific storage state
        if self.storage_state_path.exists():
            logger.info(f"Loading session storage state: {self.storage_state_path}")
            return str(self.storage_state_path)

        # 2. Try to copy from shared storage state
        if self.shared_storage_state_path.exists():
            try:
                import shutil
                shutil.copy(self.shared_storage_state_path, self.storage_state_path)
                logger.info(f"Copied shared storage state to session: {self.storage_state_path}")
                return str(self.storage_state_path)
            except Exception as e:
                logger.warning(f"Failed to copy shared storage state: {e}")
                # Fall back to using shared state directly
                return str(self.shared_storage_state_path)

        logger.info("No storage state found, starting fresh session")
        return None

    async def start(self) -> None:
        """Start the browser session."""
        if self.is_connected:
            return

        settings.ensure_dirs()

        self._playwright = await async_playwright().start()

        # Browser launch options
        launch_options = {
            "headless": self.headless,
            "args": BROWSER_ARGS,
        }

        # Try to use installed browser (chrome or edge)
        if self.browser_type in ("chrome", "edge"):
            browser_path = find_browser(self.browser_type)
            if browser_path:
                launch_options["executable_path"] = browser_path
                logger.info(f"Using installed {self.browser_type} browser: {browser_path}")
            else:
                logger.warning(f"{self.browser_type} not found, using Playwright's bundled Chromium")

        if self.proxy:
            launch_options["proxy"] = {"server": self.proxy}
            logger.info(f"Using proxy: {self.proxy}")

        # Launch browser using chromium (works with Edge executable)
        self._browser = await self._playwright.chromium.launch(**launch_options)

        # Create context with storage state if exists
        context_options = {
            "viewport": {"width": 1920, "height": 1080},
        }

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
            pass  # Continue even if timeout

    async def screenshot(self, path: str | None = None, full_page: bool = False) -> bytes:
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


@asynccontextmanager
async def browser_session(
    session_id: str = "default",
    headless: bool | None = None,
    proxy: str | None = None,
    browser_type: str = "chrome",  # "chrome", "edge", or "chromium"
) -> AsyncIterator[BrowserSession]:
    """Context manager for browser sessions."""
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


# Global session manager
class SessionManager:
    """Manages multiple browser sessions."""

    def __init__(self):
        self._sessions: dict[str, BrowserSession] = {}
        self._lock = asyncio.Lock()

    async def get_session(
        self,
        session_id: str = "default",
        headless: bool | None = None,
        proxy: str | None = None,
        browser_type: str = "chrome",  # "chrome", "edge", or "chromium"
    ) -> BrowserSession:
        """Get or create a browser session."""
        async with self._lock:
            if session_id not in self._sessions:
                session = BrowserSession(
                    session_id=session_id,
                    headless=headless,
                    proxy=proxy,
                    browser_type=browser_type,
                )
                await session.start()
                self._sessions[session_id] = session
            return self._sessions[session_id]

    async def close_session(self, session_id: str) -> None:
        """Close a specific session."""
        async with self._lock:
            if session_id in self._sessions:
                await self._sessions[session_id].stop()
                del self._sessions[session_id]

    async def close_all(self) -> None:
        """Close all sessions."""
        async with self._lock:
            for session in self._sessions.values():
                await session.stop()
            self._sessions.clear()

    def list_sessions(self) -> list[str]:
        """List active session IDs."""
        return list(self._sessions.keys())


# Global session manager instance
session_manager = SessionManager()
