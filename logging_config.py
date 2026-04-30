from __future__ import annotations

from graph.utils.logger import logger


def init_logging() -> None:
    """Initialize logging for the server.

    The repository already configures a colored console/file logger in `graph.utils.logger`.
    This function exists to keep `graph_server.py`'s import stable.
    """

    logger.info("Logging initialized")

