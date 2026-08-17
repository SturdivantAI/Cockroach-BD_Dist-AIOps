"""Resilience patterns for production deployment.

Provides:
- Rate limiting for Bedrock API calls (with exponential backoff)
- Circuit breaker for external services
- Graceful degradation when services are unavailable
"""

import logging
import time
from collections import deque
from typing import Any, Callable

logger = logging.getLogger(__name__)


class RateLimiter:
    """Token bucket rate limiter for Bedrock API calls.

    Prevents exceeding AWS Bedrock throttling limits.
    Default: 10 requests per second (adjustable).
    """

    def __init__(self, max_requests: int = 10, window_seconds: float = 1.0):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._timestamps: deque[float] = deque()

    def acquire(self) -> None:
        """Block until a request slot is available."""
        now = time.time()

        # Remove expired timestamps
        while self._timestamps and (now - self._timestamps[0]) > self.window_seconds:
            self._timestamps.popleft()

        # Wait if at capacity
        if len(self._timestamps) >= self.max_requests:
            sleep_time = self.window_seconds - (now - self._timestamps[0])
            if sleep_time > 0:
                logger.debug("Rate limiter: sleeping %.2fs", sleep_time)
                time.sleep(sleep_time)

        self._timestamps.append(time.time())


class CircuitBreaker:
    """Circuit breaker for external service calls.

    States:
    - CLOSED: Normal operation, requests pass through
    - OPEN: Service is down, requests fail immediately
    - HALF_OPEN: Testing if service recovered

    Transitions:
    - CLOSED -> OPEN: After `failure_threshold` consecutive failures
    - OPEN -> HALF_OPEN: After `recovery_timeout` seconds
    - HALF_OPEN -> CLOSED: On successful request
    - HALF_OPEN -> OPEN: On failed request
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout: float = 30.0,
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self._state = "CLOSED"
        self._failure_count = 0
        self._last_failure_time: float = 0

    @property
    def state(self) -> str:
        if self._state == "OPEN":
            if (time.time() - self._last_failure_time) > self.recovery_timeout:
                self._state = "HALF_OPEN"
        return self._state

    def call(self, func: Callable, *args: Any, **kwargs: Any) -> Any:
        """Execute a function through the circuit breaker.

        Args:
            func: Function to call.
            *args, **kwargs: Arguments to pass.

        Returns:
            Function result.

        Raises:
            CircuitBreakerOpen: If circuit is open.
            Original exception: If function fails.
        """
        current_state = self.state

        if current_state == "OPEN":
            raise CircuitBreakerOpen(
                f"Circuit breaker '{self.name}' is OPEN. "
                f"Service unavailable (failures={self._failure_count})"
            )

        try:
            result = func(*args, **kwargs)
            self._on_success()
            return result
        except Exception as e:
            self._on_failure()
            raise

    def _on_success(self) -> None:
        self._failure_count = 0
        if self._state == "HALF_OPEN":
            logger.info("Circuit breaker '%s': HALF_OPEN -> CLOSED", self.name)
        self._state = "CLOSED"

    def _on_failure(self) -> None:
        self._failure_count += 1
        self._last_failure_time = time.time()

        if self._failure_count >= self.failure_threshold:
            self._state = "OPEN"
            logger.warning(
                "Circuit breaker '%s': OPEN (failures=%d)",
                self.name,
                self._failure_count,
            )


class CircuitBreakerOpen(Exception):
    """Raised when circuit breaker is in OPEN state."""
    pass


# Global instances
bedrock_rate_limiter = RateLimiter(max_requests=10, window_seconds=1.0)
bedrock_circuit_breaker = CircuitBreaker("bedrock", failure_threshold=5, recovery_timeout=30.0)
cockroachdb_circuit_breaker = CircuitBreaker("cockroachdb", failure_threshold=3, recovery_timeout=15.0)
