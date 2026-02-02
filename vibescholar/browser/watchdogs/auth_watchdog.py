"""Authentication watchdog for managing login states."""

import json
import logging
import re
from pathlib import Path
from typing import Literal

from playwright.async_api import Page
from pydantic import BaseModel

from ...config import settings

logger = logging.getLogger(__name__)


class InstitutionProfile(BaseModel):
    """Configuration for institutional authentication."""

    institution_id: str
    institution_name: str
    login_url: str | None = None
    auth_method: Literal["shibboleth", "ezproxy", "openathens", "direct"] = "shibboleth"
    proxy_prefix: str | None = None  # e.g., "https://proxy.library.edu/login?url="
    domains: list[str] = []  # Domains that use this institution's auth


# Common login page indicators
LOGIN_INDICATORS = [
    # Shibboleth
    r"shibboleth",
    r"idp\..*\.edu",
    r"login\..*\.edu",
    r"wayf",
    r"discovery",
    # EZProxy
    r"ezproxy",
    r"proxy.*login",
    # OpenAthens
    r"openathens",
    r"my\.openathens",
    # Generic
    r"sign.?in",
    r"log.?in",
    r"authenticate",
    r"institutional.*access",
    r"access.*denied",
    r"subscription.*required",
]

# Paywall indicators - patterns verified on actual paywall pages
PAYWALL_INDICATORS = [
    r"access\s+through\s+your\s+institution",  # Nature paywall indicator
    r"access\s+through\s+your\s+organization",  # ScienceDirect paywall indicator
    r"buy\s+this\s+article",  # Nature paywall
    r"access\s+options",  # Nature paywall heading
]


class AuthWatchdog:
    """Manages authentication state for academic sites."""

    def __init__(self, session_id: str = "default"):
        self.session_id = session_id
        self._auth_states: dict[str, dict] = {}

    def _get_site_key(self, url: str) -> str:
        """Extract site key from URL."""
        from urllib.parse import urlparse

        parsed = urlparse(url)
        # Use domain without www
        domain = parsed.netloc.lower()
        if domain.startswith("www."):
            domain = domain[4:]
        return domain

    def storage_state_path(self, site_key: str) -> Path:
        """Get storage state path for a site."""
        return settings.storage_state_dir / f"{self.session_id}_{site_key}_auth.json"

    async def load_auth_state(self, site_key: str) -> dict | None:
        """Load saved authentication state for a site."""
        path = self.storage_state_path(site_key)
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    state = json.load(f)
                self._auth_states[site_key] = state
                logger.info(f"Loaded auth state for {site_key}")
                return state
            except Exception as e:
                logger.warning(f"Failed to load auth state for {site_key}: {e}")
        return None

    async def save_auth_state(self, site_key: str, state: dict) -> None:
        """Save authentication state for a site."""
        path = self.storage_state_path(site_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2)
            self._auth_states[site_key] = state
            logger.info(f"Saved auth state for {site_key}")
        except Exception as e:
            logger.error(f"Failed to save auth state for {site_key}: {e}")

    async def detect_login_required(self, page: Page) -> bool:
        """Detect if the current page requires login."""
        url = page.url.lower()
        content = await page.content()
        content_lower = content.lower()

        # Check URL patterns
        for pattern in LOGIN_INDICATORS:
            if re.search(pattern, url, re.IGNORECASE):
                logger.info(f"Login required detected in URL: {pattern}")
                return True

        # Check page content
        for pattern in LOGIN_INDICATORS:
            if re.search(pattern, content_lower, re.IGNORECASE):
                logger.info(f"Login required detected in content: {pattern}")
                return True

        return False

    async def detect_paywall(self, page: Page) -> bool:
        """Detect if the current page shows a paywall."""
        content = await page.content()
        content_lower = content.lower()

        for pattern in PAYWALL_INDICATORS:
            if re.search(pattern, content_lower, re.IGNORECASE):
                logger.info(f"Paywall detected: {pattern}")
                return True

        return False

    async def detect_pdf_available(self, page: Page) -> bool:
        """Detect if PDF download is available on the page."""
        # Look for PDF download links
        pdf_selectors = [
            'a[href*=".pdf"]',
            'a[href*="/pdf/"]',
            'a[href*="pdf."]',
            'button:has-text("PDF")',
            'a:has-text("Download PDF")',
            'a:has-text("Full Text PDF")',
            '[data-track-action="download pdf"]',
        ]

        for selector in pdf_selectors:
            try:
                element = await page.query_selector(selector)
                if element:
                    return True
            except Exception:
                continue

        return False

    async def prompt_manual_login(
        self,
        page: Page,
        site_name: str,
        timeout: int = 300,
    ) -> bool:
        """
        Prompt user to manually login.

        Opens a visible browser window and waits for user to complete login.
        Returns True if login appears successful.
        """
        logger.info(f"Manual login required for {site_name}")
        logger.info(f"Please complete login in the browser window (timeout: {timeout}s)")

        # Wait for navigation away from login page
        initial_url = page.url
        try:
            # Wait for URL to change (indicating successful login)
            await page.wait_for_function(
                f"window.location.href !== '{initial_url}'",
                timeout=timeout * 1000,
            )

            # Check if we're still on a login page
            if not await self.detect_login_required(page):
                logger.info("Login appears successful")
                return True
            else:
                logger.warning("Still on login page after navigation")
                return False

        except Exception as e:
            logger.error(f"Login timeout or error: {e}")
            return False

    async def get_institution_login_url(
        self,
        site_url: str,
        institution: InstitutionProfile,
    ) -> str:
        """Get the institutional login URL for a site."""
        if institution.proxy_prefix:
            # EZProxy style
            return f"{institution.proxy_prefix}{site_url}"
        elif institution.login_url:
            return institution.login_url
        else:
            return site_url
