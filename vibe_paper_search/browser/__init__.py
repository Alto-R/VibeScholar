"""Browser module - browser automation and session management."""

from .session import BrowserSession, SessionManager, browser_session, session_manager

__all__ = [
    "BrowserSession",
    "SessionManager",
    "browser_session",
    "session_manager",
]
