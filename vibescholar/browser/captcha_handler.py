"""Global CAPTCHA handling with lock mechanism.

This module provides a global CAPTCHA handler that:
1. Uses a global lock to prevent multiple browser windows
2. Switches from headless to visible browser for user verification
3. Saves authentication state after successful verification
4. Notifies waiting requests when verification is complete

Reference: huge-ai-search project's CAPTCHA handling approach.
"""

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .session import BrowserSession

logger = logging.getLogger(__name__)


class CaptchaHandler:
    """Global CAPTCHA handling with lock mechanism.

    This handler ensures only one visible browser window is opened at a time
    for CAPTCHA verification, preventing multiple popups when concurrent
    requests encounter CAPTCHAs.

    Usage:
        handler = CaptchaHandler(session)
        success = await handler.handle_captcha(url)
        if success:
            # CAPTCHA solved, continue with operation
        else:
            # CAPTCHA not solved, handle error
    """

    # Class-level lock and state for global coordination
    _lock = asyncio.Lock()
    _handling = False
    _wait_event: Optional[asyncio.Event] = None

    # CAPTCHA detection indicators
    CAPTCHA_INDICATORS = [
        "Are you a robot",
        "验证码",
        "captcha",
        "unusual traffic",
        "异常流量",
        "Please confirm you are a human",
        "completing the captcha challenge",
        "Reference number:",
        "challenges.cloudflare.com",
    ]

    def __init__(
        self,
        session: "BrowserSession",
        timeout: int = 300,
        check_interval: float = 1.5,
    ):
        """Initialize CAPTCHA handler.

        Args:
            session: The browser session that encountered CAPTCHA
            timeout: Maximum time to wait for user verification (seconds)
            check_interval: Time between CAPTCHA status checks (seconds)
        """
        self.session = session
        self.timeout = timeout
        self.check_interval = check_interval

    async def handle_captcha(self, url: str) -> bool:
        """Handle CAPTCHA by opening visible browser for user verification.

        This method:
        1. Acquires global lock (or waits if another request is handling)
        2. Closes headless browser
        3. Opens visible browser at CAPTCHA URL
        4. Waits for user to solve CAPTCHA
        5. Saves authentication state
        6. Releases lock to notify waiting requests

        Args:
            url: The URL where CAPTCHA was encountered

        Returns:
            True if CAPTCHA was solved successfully, False otherwise
        """
        # Try to acquire lock
        lock_result = await self._try_acquire_lock()

        if lock_result == "wait":
            # Another request handled CAPTCHA, we can retry
            logger.info("CAPTCHA was handled by another request")
            return True

        if lock_result == "timeout":
            logger.warning("Timeout waiting for CAPTCHA lock")
            return False

        # We acquired the lock, handle CAPTCHA
        logger.info("Acquired CAPTCHA lock, starting verification flow")

        try:
            return await self._handle_captcha_internal(url)
        finally:
            self._release_lock()

    async def _handle_captcha_internal(self, url: str) -> bool:
        """Internal CAPTCHA handling logic."""
        from .session import BrowserSession

        # Store original session info
        session_id = self.session.session_id
        browser_type = self.session.browser_type
        proxy = self.session.proxy

        # 1. Close headless browser
        logger.info("Closing headless browser...")
        try:
            await self.session.stop()
        except Exception as e:
            logger.warning(f"Error closing headless browser: {e}")

        # 2. Start visible browser
        logger.info("Starting visible browser for CAPTCHA verification...")
        visible_session = BrowserSession(
            session_id=session_id,
            headless=False,
            browser_type=browser_type,
            proxy=proxy,
        )

        try:
            await visible_session.start()

            # 3. Navigate to CAPTCHA page
            logger.info(f"Navigating to: {url}")
            await visible_session.page.goto(url, wait_until="domcontentloaded")

            # 4. Notify user and wait for verification
            self._print_user_notification()
            success = await self._wait_for_captcha_solved(visible_session)

            # 5. Save storage state if successful
            if success:
                logger.info("CAPTCHA solved, saving authentication state...")
                await visible_session.save_storage_state()
                print("\n验证成功！认证状态已保存。")
            else:
                print("\n验证超时或失败。")

            return success

        except Exception as e:
            logger.error(f"Error during CAPTCHA handling: {e}")
            return False

        finally:
            # Close visible browser
            try:
                await visible_session.stop()
            except Exception:
                pass

    async def _try_acquire_lock(self) -> str:
        """Try to acquire CAPTCHA lock.

        Returns:
            "acquired" - Lock acquired, proceed with CAPTCHA handling
            "wait" - Another request handled CAPTCHA, can retry
            "timeout" - Timeout waiting for lock
        """
        async with CaptchaHandler._lock:
            if not CaptchaHandler._handling:
                CaptchaHandler._handling = True
                CaptchaHandler._wait_event = asyncio.Event()
                logger.info("CAPTCHA lock acquired")
                return "acquired"

        # Lock is held by another request, wait for completion
        logger.info("CAPTCHA lock held by another request, waiting...")

        if CaptchaHandler._wait_event:
            try:
                await asyncio.wait_for(
                    CaptchaHandler._wait_event.wait(),
                    timeout=self.timeout,
                )
                logger.info("Other request completed CAPTCHA handling")
                return "wait"
            except asyncio.TimeoutError:
                logger.warning("Timeout waiting for CAPTCHA lock")
                return "timeout"

        return "wait"

    def _release_lock(self) -> None:
        """Release CAPTCHA lock and notify waiting requests."""
        logger.info("Releasing CAPTCHA lock")
        CaptchaHandler._handling = False
        if CaptchaHandler._wait_event:
            CaptchaHandler._wait_event.set()
            CaptchaHandler._wait_event = None

    async def _wait_for_captcha_solved(
        self,
        session: "BrowserSession",
    ) -> bool:
        """Wait for CAPTCHA to be solved by user.

        Args:
            session: The visible browser session

        Returns:
            True if CAPTCHA was solved, False if timeout
        """
        start_time = time.time()
        last_progress_time = 0

        while time.time() - start_time < self.timeout:
            try:
                # Check if CAPTCHA is still present
                content = await session.page.evaluate("document.body.innerText")
                if not self._is_captcha_page(content):
                    logger.info("CAPTCHA no longer detected, verification successful")
                    return True

                # Print progress every 10 seconds
                elapsed = int(time.time() - start_time)
                if elapsed >= last_progress_time + 10:
                    last_progress_time = elapsed
                    print(f"等待用户完成验证... ({elapsed}/{self.timeout}秒)")

            except Exception as e:
                logger.debug(f"Error checking page content: {e}")

            await asyncio.sleep(self.check_interval)

        logger.warning("Timeout waiting for CAPTCHA to be solved")
        return False

    def _is_captcha_page(self, content: str) -> bool:
        """Check if page content indicates CAPTCHA.

        Args:
            content: Page text content

        Returns:
            True if CAPTCHA indicators found
        """
        content_lower = content.lower()
        return any(
            indicator.lower() in content_lower
            for indicator in self.CAPTCHA_INDICATORS
        )

    def _print_user_notification(self) -> None:
        """Print user notification about CAPTCHA verification."""
        print("\n" + "=" * 60)
        print("检测到人机验证 (CAPTCHA)")
        print("")
        print("请在弹出的浏览器窗口中完成验证：")
        print("  1. 完成验证码挑战")
        print("  2. 如需登录，请登录您的账户")
        print("  3. 验证完成后系统将自动继续")
        print("")
        print(f"最长等待时间: {self.timeout} 秒")
        print("=" * 60 + "\n")


# Convenience function for quick CAPTCHA handling
async def handle_captcha_globally(
    session: "BrowserSession",
    url: str,
    timeout: int = 300,
) -> bool:
    """Handle CAPTCHA using global handler.

    Args:
        session: Browser session that encountered CAPTCHA
        url: URL where CAPTCHA was encountered
        timeout: Maximum wait time in seconds

    Returns:
        True if CAPTCHA was solved successfully
    """
    handler = CaptchaHandler(session, timeout=timeout)
    return await handler.handle_captcha(url)
