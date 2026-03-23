import time
import random
from typing import Callable, Any
from functools import wraps
from src.utils.logger import logger

def retry_with_backoff(
    retries: int = 5,
    backoff_in_seconds: float = 1,
    jitter: bool = True
) -> Callable:
    """
    Decorator for retrying a function with exponential backoff and optional jitter.
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            x = 0
            while True:
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    if x == retries:
                        logger.error(f"Function {func.__name__} failed after {retries} retries: {e}")
                        raise
                    
                    sleep_time = (backoff_in_seconds * 2 ** x)
                    if jitter:
                        sleep_time += random.uniform(0, 1)
                    
                    logger.warning(f"Retrying {func.__name__} in {sleep_time:.2f}s... (Attempt {x+1}/{retries})")
                    time.sleep(sleep_time)
                    x += 1
        return wrapper
    return decorator
