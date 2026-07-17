"""
Rate limiting module for the Discord AI chatbot.

This module handles rate limiting for Groq API calls and Discord messages,
including retry logic for transient failures and graceful degradation.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, Callable, TypeVar, ParamSpec
from functools import wraps

logger = logging.getLogger(__name__)


T = TypeVar("T")
P = ParamSpec("P")


@dataclass
class RateLimitState:
    """
    Tracks rate limit state for an API or action.

    Attributes:
        calls_made: Number of calls made in the current window.
        window_start: When the current window started.
        window_seconds: Size of the rate limit window.
        max_calls: Maximum allowed calls per window.
        is_limited: Whether currently rate limited.
        limited_until: When the rate limit will be lifted.
    """

    calls_made: int = 0
    window_start: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    window_seconds: int = 60
    max_calls: int = 30
    is_limited: bool = False
    limited_until: datetime | None = None

    def can_proceed(self) -> bool:
        """Check if a new call can proceed."""
        if self.is_limited and self.limited_until:
            if datetime.now(timezone.utc) < self.limited_until:
                return False
            # Reset after limit period
            self.is_limited = False
            self.limited_until = None
            self.calls_made = 0
            self.window_start = datetime.now(timezone.utc)

        # Check if we need to reset the window
        now = datetime.now(timezone.utc)
        elapsed = (now - self.window_start).total_seconds()

        if elapsed >= self.window_seconds:
            # Reset window
            self.calls_made = 0
            self.window_start = now

        return self.calls_made < self.max_calls

    def record_call(self) -> None:
        """Record that a call was made."""
        self.calls_made += 1

        if self.calls_made >= self.max_calls:
            self.is_limited = True
            remaining = (
                self.window_seconds
                - (datetime.now(timezone.utc) - self.window_start).total_seconds()
            )
            self.limited_until = datetime.now(timezone.utc) + timedelta(
                seconds=max(remaining, 0)
            )
            logger.warning(f"Rate limit reached. Limited until {self.limited_until}")

    def get_retry_after(self) -> float:
        """Get seconds until rate limit is lifted."""
        if not self.limited_until:
            return 0.0

        delta = self.limited_until - datetime.now(timezone.utc)
        return max(delta.total_seconds(), 0.0)


class RateLimiter:
    """
    Manages rate limiting for multiple endpoints/actions.

    Provides centralized rate limit tracking with automatic retry handling.
    """

    def __init__(
        self,
        default_max_calls: int = 30,
        default_window_seconds: int = 60,
    ) -> None:
        """
        Initialize the rate limiter.

        Args:
            default_max_calls: Default maximum calls per window.
            default_window_seconds: Default window size in seconds.
        """
        self._states: dict[str, RateLimitState] = {}
        self._default_max = default_max_calls
        self._default_window = default_window_seconds

        logger.info(
            f"RateLimiter initialized: {default_max_calls} calls per "
            f"{default_window_seconds}s"
        )

    def get_state(self, key: str) -> RateLimitState:
        """
        Get or create rate limit state for a key.

        Args:
            key: Identifier for the rate-limited resource.

        Returns:
            The rate limit state for this key.
        """
        if key not in self._states:
            self._states[key] = RateLimitState(
                window_seconds=self._default_window,
                max_calls=self._default_max,
            )
        return self._states[key]

    def set_limits(self, key: str, max_calls: int, window_seconds: int) -> None:
        """
        Set custom limits for a specific key.

        Args:
            key: Identifier for the rate-limited resource.
            max_calls: Maximum calls per window.
            window_seconds: Window size in seconds.
        """
        state = self.get_state(key)
        state.max_calls = max_calls
        state.window_seconds = window_seconds
        logger.debug(f"Set rate limits for {key}: {max_calls}/{window_seconds}s")

    def can_proceed(self, key: str) -> bool:
        """
        Check if a call can proceed for a given key.

        Args:
            key: Identifier for the rate-limited resource.

        Returns:
            True if the call can proceed.
        """
        state = self.get_state(key)
        return state.can_proceed()

    def record_call(self, key: str) -> None:
        """
        Record a call for a given key.

        Args:
            key: Identifier for the rate-limited resource.
        """
        state = self.get_state(key)
        state.record_call()

    async def wait_if_limited(self, key: str) -> None:
        """
        Wait if currently rate limited.

        Args:
            key: Identifier for the rate-limited resource.
        """
        state = self.get_state(key)

        while not state.can_proceed():
            retry_after = state.get_retry_after()
            if retry_after > 0:
                logger.info(f"Rate limited for {key}. Waiting {retry_after:.2f}s")
                await asyncio.sleep(retry_after)
            else:
                break

    def get_all_stats(self) -> dict[str, dict]:
        """
        Get statistics for all rate-limited resources.

        Returns:
            Dictionary of stats keyed by resource identifier.
        """
        stats = {}
        for key, state in self._states.items():
            stats[key] = {
                "calls_made": state.calls_made,
                "max_calls": state.max_calls,
                "window_seconds": state.window_seconds,
                "is_limited": state.is_limited,
                "limited_until": (
                    state.limited_until.isoformat() if state.limited_until else None
                ),
            }
        return stats


async def with_retry(
    func: Callable[P, T],
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    exponential_backoff: bool = True,
    retryable_exceptions: tuple[type[Exception], ...] = (Exception,),
) -> Callable[P, T]:
    """
    Decorator for adding retry logic to async functions.

    Args:
        func: The async function to wrap.
        max_retries: Maximum number of retry attempts.
        base_delay: Initial delay between retries in seconds.
        max_delay: Maximum delay between retries in seconds.
        exponential_backoff: Whether to use exponential backoff.
        retryable_exceptions: Tuple of exception types to retry on.

    Returns:
        Wrapped function with retry logic.
    """

    @wraps(func)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
        last_exception: Exception | None = None

        for attempt in range(max_retries + 1):
            try:
                return await func(*args, **kwargs)

            except retryable_exceptions as e:
                last_exception = e

                if attempt == max_retries:
                    logger.error(
                        f"Max retries ({max_retries}) exceeded for {func.__name__}"
                    )
                    raise

                # Calculate delay
                if exponential_backoff:
                    delay = min(base_delay * (2**attempt), max_delay)
                else:
                    delay = base_delay

                logger.warning(
                    f"Attempt {attempt + 1}/{max_retries + 1} failed for "
                    f"{func.__name__}: {e}. Retrying in {delay:.2f}s"
                )

                await asyncio.sleep(delay)

        # Should never reach here, but just in case
        raise last_exception  # type: ignore[misc]

    return wrapper  # type: ignore[return-value]


class APIRateLimitHandler:
    """
    Specialized handler for Groq API rate limits.

    Handles both client-side rate limiting and server-side 429 responses.
    """

    def __init__(self) -> None:
        """Initialize the API rate limit handler."""
        self._limiter = RateLimiter(default_max_calls=30, default_window_seconds=60)
        self._request_times: list[datetime] = []

    async def acquire(self) -> None:
        """Acquire permission to make an API request."""
        await self._limiter.wait_if_limited("groq_api")

    def record_request(self) -> None:
        """Record that an API request was made."""
        self._limiter.record_call("groq_api")
        self._request_times.append(datetime.now(timezone.utc))

        # Keep only recent requests for analysis
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=5)
        self._request_times = [t for t in self._request_times if t > cutoff]

    def handle_rate_limit_response(self, retry_after: float | None = None) -> None:
        """
        Handle a 429 rate limit response from the API.

        Args:
            retry_after: Seconds to wait before retrying (if provided by API).
        """
        state = self._limiter.get_state("groq_api")
        state.is_limited = True

        if retry_after:
            state.limited_until = datetime.now(timezone.utc) + timedelta(
                seconds=retry_after
            )
            logger.warning(f"API rate limited. Retry after {retry_after}s")
        else:
            # Default backoff
            state.limited_until = datetime.now(timezone.utc) + timedelta(seconds=60)
            logger.warning("API rate limited. Using default 60s backoff")

    def get_request_rate(self) -> float:
        """
        Get the current request rate (requests per second).

        Returns:
            Requests per second over the last minute.
        """
        if len(self._request_times) < 2:
            return 0.0

        now = datetime.now(timezone.utc)
        recent = [t for t in self._request_times if (now - t).total_seconds() < 60]

        if len(recent) < 2:
            return 0.0

        time_span = (recent[-1] - recent[0]).total_seconds()
        if time_span <= 0:
            return 0.0

        return len(recent) / time_span

    def get_stats(self) -> dict[str, Any]:
        """
        Get statistics about API rate limiting.

        Returns:
            Dictionary with rate limit statistics.
        """
        return {
            "rate_limiter": self._limiter.get_all_stats().get("groq_api", {}),
            "request_rate": self.get_request_rate(),
            "total_recent_requests": len(self._request_times),
        }
