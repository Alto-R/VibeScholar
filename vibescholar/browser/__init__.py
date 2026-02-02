"""Browser module - browser automation and session management."""

from .session import (
    # Core classes
    BrowserSession,
    SessionManager,
    # Context manager
    browser_session,
    # Global instance
    session_manager,
    cleanup_session_manager,
    # Utility functions
    find_browser,
    detect_proxy,
    # Constants
    BROWSER_PATHS,
    BROWSER_ARGS,
)
from .captcha_handler import CaptchaHandler, handle_captcha_globally
from .dom_service import DOMService, DOMElement

__all__ = [
    # Session management
    "BrowserSession",
    "SessionManager",
    "browser_session",
    "session_manager",
    "cleanup_session_manager",
    # Utilities
    "find_browser",
    "detect_proxy",
    "BROWSER_PATHS",
    "BROWSER_ARGS",
    # CAPTCHA handling
    "CaptchaHandler",
    "handle_captcha_globally",
    # DOM service
    "DOMService",
    "DOMElement",
]
