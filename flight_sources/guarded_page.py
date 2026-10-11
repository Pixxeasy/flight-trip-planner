"""guarded_page.py — a Playwright page wrapper that transparently handles
Skyscanner's bot-check (CAPTCHA) pages.

Wrapping the page makes captcha handling un-forgettable: every navigation
goes through `GuardedPage.goto()`, which waits out any challenge (for a
bounded amount of time) before returning. All other Page attributes are
forwarded, so locators, waits, clicks, etc. behave exactly as before.
"""

from __future__ import annotations

import re
from typing import Optional

from patchright.sync_api import Page, TimeoutError as PlaywrightTimeoutError

# URLs that indicate a bot check is blocking the page (Skyscanner's
# reCAPTCHA / "sorry" interstitial).
_CAPTCHA_URL = re.compile(r"captcha-v2|challenge|/sorry/", re.I)

# How long a human / the NopeCHA extension may take per solve attempt.
_CAPTCHA_TIMEOUT_MS = 60_000
# How many solve attempts before giving up with an error instead of hanging.
_MAX_ATTEMPTS = 3
# Small pause between attempts.
_SETTLE_DELAY_MS = 500


def _beep() -> None:
    """Alert the operator that a challenge needs solving (best effort)."""
    try:
        import winsound

        winsound.Beep(880, 300)
    except Exception:
        pass


class GuardedPage:
    """Proxy around a patchright/playwright Page whose `goto()` waits out
    captchas, so no caller can forget to handle them."""

    def __init__(self, page: Page) -> None:
        self._page = page
        self._handling = False

    # -- proxying ---------------------------------------------------------

    def __getattr__(self, name):
        return getattr(self._page, name)

    # -- navigation -------------------------------------------------------

    def goto(self, url, **kwargs):
        self._page.goto(url, **kwargs)
        self.settle(intended_url=url)

    # -- captcha handling -------------------------------------------------

    def is_captcha(self) -> bool:
        return bool(_CAPTCHA_URL.search(self._page.url))

    def settle(self, intended_url: Optional[str] = None) -> bool:
        """Wait out a present captcha.

        Returns True if the page is usable afterwards, False if the
        challenge could not be cleared within the attempt/retry budget.
        When a captcha was seen and the page drifted away from
        `intended_url` after solving, it is navigated back there (with a
        raw goto, outside the wrapper, to avoid re-entry).
        """
        if self._handling:
            return not self.is_captcha()
        self._handling = True
        try:
            saw_captcha = False
            for _ in range(_MAX_ATTEMPTS):
                if self.is_captcha():
                    saw_captcha = True
                    print("Captcha erkannt:", self._page.url)
                    _beep()
                    self._page.wait_for_timeout(_SETTLE_DELAY_MS)
                    try:
                        # Waits until the challenge URL has been left.
                        self._page.wait_for_url(
                            lambda u: not _CAPTCHA_URL.search(u),
                            timeout=_CAPTCHA_TIMEOUT_MS,
                        )
                    except PlaywrightTimeoutError:
                        print("Captcha nicht gelöst, neuer Versuch ...")
                        continue
                    except Exception:
                        return False
                if intended_url and saw_captcha and self._page.url != intended_url:
                    try:
                        self._page.goto(intended_url)  # raw, no re-entry
                    except Exception:
                        return False
                if not self.is_captcha():
                    if saw_captcha:
                        print("Captcha gelöst:", self._page.url)
                    return True
                # Still blocked (e.g. the recovery navigation was blocked
                # too) - try again.
            print("Captcha konnte nicht gelöst werden:", self._page.url)
            return False
        finally:
            self._handling = False