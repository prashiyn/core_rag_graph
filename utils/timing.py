# coding=utf-8
import time
import functools
import logging
from typing import Optional, Callable, Any
from datetime import datetime


def timing_decorator(logger,
                     task_name: Optional[str] = None,
                     log_level: str = 'INFO',
                     include_args: bool = False,
                     ) -> Callable:
    """
    Decorator that logs wall-clock time for a function call.

    Args:
        task_name: Label for logs; defaults to the function name.
        log_level: Logger method name: 'DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'.
        include_args: If True, log positional and keyword arguments.

    Examples:
        @timing_decorator()
        def my_function():
            pass

        @timing_decorator("custom_task")
        def another_function():
            pass

        @timing_decorator(log_level='DEBUG', include_args=True)
        def function_with_args(x, y):
            pass
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            # Task label for logs
            actual_task_name = task_name if task_name is not None else func.__name__

            # Start timestamp
            start_time = time.perf_counter()
            start_datetime = datetime.now()

            try:
                # Run wrapped function
                result = func(*args, **kwargs)
                success = True
            except Exception as e:
                success = False
                raise e
            finally:
                # Elapsed time
                end_time = time.perf_counter()
                execution_time = end_time - start_time

                # Log line
                log_message = f"task [{actual_task_name}] "
                if include_args:
                    log_message += f"args: args={args}, kwargs={kwargs} "
                log_message += f"elapsed: {execution_time:.3f}s"
                if not success:
                    log_message += " (failed)"

                # Start time
                log_message += f" (started: {start_datetime.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]})"

                # Emit at requested level
                log_func = getattr(logger, log_level.lower(), logger.info)
                log_func(log_message)

            return result

        return wrapper

    return decorator


# Preset decorators for common log levels
def timing_debug(logger, task_name: Optional[str] = None, include_args: bool = False) -> Callable:
    """Timing decorator at DEBUG level."""
    return timing_decorator(logger, task_name, 'DEBUG', include_args)


def timing_info(logger, task_name: Optional[str] = None, include_args: bool = False) -> Callable:
    """Timing decorator at INFO level."""
    return timing_decorator(logger, task_name, 'INFO', include_args)


def timing_warning(logger, task_name: Optional[str] = None, include_args: bool = False) -> Callable:
    """Timing decorator at WARNING level."""
    return timing_decorator(logger, task_name, 'WARNING', include_args)


# Example usage
if __name__ == "__main__":
    pass