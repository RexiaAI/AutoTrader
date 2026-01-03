from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from typing import Any, Callable, TypeVar

T = TypeVar("T")


class SymbolTimeout(Exception):
    """Raised when processing a single symbol takes too long."""


def process_with_timeout(func: Callable[..., T], timeout_seconds: float, *args: Any, **kwargs: Any) -> T:
    """
    Execute a function with a timeout. If it takes longer than timeout_seconds, raise SymbolTimeout.

    Note: this uses a thread pool; the function should be thread-safe.
    The underlying function continues running in the background if it times out.
    """
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(func, *args, **kwargs)
        try:
            return future.result(timeout=timeout_seconds)
        except FuturesTimeoutError as exc:
            raise SymbolTimeout(f"Processing timed out after {timeout_seconds}s") from exc


