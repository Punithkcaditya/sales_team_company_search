"""Retry policy shared by the model providers.

Free tiers meter requests per minute and say how long to wait, so a 429 is a
pause rather than a dead end. Honouring the stated cooldown turns a burst of
requests into a short delay instead of a failed briefing.
"""

from __future__ import annotations

import asyncio
import logging
import re

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
MAX_BACKOFF_SECONDS = 30.0

# Providers state their own cooldown in the error body, e.g. Gemini's
# "Please retry in 17.47s" or Groq's "try again in 1m13.6s".
_RETRY_HINT = re.compile(r"(?:retry|try again) in (?:(\d+)m)?([\d.]+)\s*s", re.IGNORECASE)

# Fallback when a 429 arrives with no stated delay.
_DEFAULT_RATE_LIMIT_WAIT = 5.0

_TRANSIENT_NAMES = {"APIConnectionError", "APITimeoutError"}


def status_of(exc: Exception) -> int | None:
    """The HTTP status, wherever a given SDK's error type keeps it.

    Some use `status_code`, others `code`; either may be absent or non-numeric.
    """
    for attribute in ("status_code", "code"):
        value = getattr(exc, attribute, None)
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
    return None


def is_transient(exc: Exception) -> bool:
    """A dropped connection or a 5xx is worth another attempt; a 400 is not."""
    status = status_of(exc)
    return type(exc).__name__ in _TRANSIENT_NAMES or (status is not None and status >= 500)


def stated_delay(exc: Exception) -> float | None:
    """The cooldown the provider asked for, capped so one call cannot hang."""
    match = _RETRY_HINT.search(str(exc))
    if not match:
        return None
    minutes = float(match.group(1) or 0)
    seconds = float(match.group(2))
    return min(minutes * 60 + seconds + 0.5, MAX_BACKOFF_SECONDS)


async def wait_to_retry(exc: Exception, attempt: int) -> bool:
    """Sleep if the call is worth retrying, and report whether it is."""
    if attempt >= MAX_ATTEMPTS - 1:
        return False

    if status_of(exc) == 429:
        delay = stated_delay(exc) or _DEFAULT_RATE_LIMIT_WAIT
    elif is_transient(exc):
        delay = 1.0 * (attempt + 1)
    else:
        return False

    logger.warning("Retrying in %.1fs after: %s", delay, str(exc)[:120])
    await asyncio.sleep(delay)
    return True
