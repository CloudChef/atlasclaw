"""

Retry strategy

implement HTTP Retry strategy:
- default 3, 30s, 10%
- step(multistep)
- execute etc.
"""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from typing import Callable, TypeVar, Awaitable, Optional

T = TypeVar("T")


@dataclass
class RetryConfig:
    """

Retry configuration
    
    Attributes:
        at tempts:count
        min_delay_ms:count
        max_delay_ms:count
        jitter:(0-1)
        retryable_errors:typelist
    
"""
    attempts: int = 3
    min_delay_ms: int = 1000
    max_delay_ms: int = 30000
    jitter: float = 0.1
    retryable_errors: list[type] = None
    retryable_status_codes: list[int] = None
    
    def __post_init__(self):
        if self.retryable_errors is None:
            self.retryable_errors = [
                TimeoutError,
                ConnectionError,
            ]
        if self.retryable_status_codes is None:
            self.retryable_status_codes = [
                429,  # Too Many Requests
                500,  # Internal Server Error
                502,  # Bad Gateway
                503,  # Service Unavailable
                504,  # Gateway Timeout
            ]


class RetryStrategy:
    """

Retry strategyexecute
    
    Example usage:
        ```python
        retry = RetryStrategy(RetryConfig(attempts=3))
        
        result = await retry.execute(
            lambda:api_client.get("/endpoint"),
        )
        ```
    
"""
    
    def __init__(self, config: RetryConfig):
        """
initializeRetry strategy
        
        Args:
            config:Retry configuration
        
"""
        self.config = config
    
    async def execute(
        self,
        func: Callable[[], Awaitable[T]],
        *,
        on_retry: Optional[Callable[[int, Exception], Awaitable[None]]] = None,
    ) -> T:
        """

execute count
        
        Args:
            func:execute count
            on_retry:(countand)
            
        Returns:
            countexecute
            
        Raises:
            
        
"""
        last_error: Optional[Exception] = None
        
        for attempt in range(1, self.config.attempts + 1):
            try:
                return await func()
            except Exception as e:
                last_error = e
                
                # check
                if not self._is_retryable(e):
                    raise
                
                # ,
                if attempt >= self.config.attempts:
                    raise
                
                # calculate
                delay = self._calculate_delay(attempt)
                
                # 
                if on_retry:
                    await on_retry(attempt, e)
                
                # etc.
                await asyncio.sleep(delay / 1000.0)
        
        # to
        if last_error:
            raise last_error
        raise RuntimeError("Retry loop completed without result")
    
    def _is_retryable(self, error: Exception) -> bool:
        """

check
        
        Args:
            error:
            
        Returns:
            
        
"""
        # check type
        for error_type in self.config.retryable_errors:
            if isinstance(error, error_type):
                return True
        
        # check HTTP(such as httpx)
        if hasattr(error, "response") and hasattr(error.response, "status_code"):
            status_code = error.response.status_code
            if status_code in self.config.retryable_status_codes:
                return True
        
        return False
    
    def _calculate_delay(self, attempt: int) -> int:
        """

calculate(exponential backoff +)
        
        Args:
            at tempt:count
            
        Returns:
            count
        
"""
        # exponential backoff
        base_delay = self.config.min_delay_ms * (2 ** (attempt - 1))
        
        # 
        delay = min(base_delay, self.config.max_delay_ms)
        
        # 
        jitter_range = delay * self.config.jitter
        jitter = random.uniform(-jitter_range, jitter_range)
        
        return int(delay + jitter)
    
    @classmethod
    def default(cls) -> "RetryStrategy":
        """createdefaultRetry strategy"""
        return cls(RetryConfig())
    
    @classmethod
    def aggressive(cls) -> "RetryStrategy":
        """create Retry strategy(multi,)"""
        return cls(RetryConfig(
            attempts=5,
            min_delay_ms=500,
            max_delay_ms=10000,
            jitter=0.2,
        ))
    
    @classmethod
    def conservative(cls) -> "RetryStrategy":
        """create Retry strategy(,)"""
        return cls(RetryConfig(
            attempts=2,
            min_delay_ms=2000,
            max_delay_ms=60000,
            jitter=0.05,
        ))
