"""Watchdogs module - browser event handlers."""

from .auth_watchdog import AuthWatchdog, InstitutionProfile
from .captcha_watchdog import CaptchaWatchdog, PageState
from .cookie_watchdog import CookieWatchdog

__all__ = [
    "AuthWatchdog",
    "InstitutionProfile",
    "CaptchaWatchdog",
    "CookieWatchdog",
    "PageState",
]
