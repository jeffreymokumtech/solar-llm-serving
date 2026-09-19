from __future__ import annotations

import time


class TokenBucket:
    """Per-key token bucket: `rate` requests per minute, burst = rate."""

    def __init__(self, rate_per_minute: int) -> None:
        self.capacity = float(rate_per_minute)
        self.tokens = float(rate_per_minute)
        self.refill_per_s = rate_per_minute / 60.0
        self.last = time.monotonic()

    def take(self) -> bool:
        now = time.monotonic()
        self.tokens = min(self.capacity, self.tokens + (now - self.last) * self.refill_per_s)
        self.last = now
        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return True
        return False
