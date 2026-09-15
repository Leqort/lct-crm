"""Application logging configured from a single place."""

from __future__ import annotations

import logging
import sys

from app.core.config import settings

_FORMAT = "%(asctime)s %(levelname)-8s [%(name)s] %(message)s"


def configure_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(_FORMAT))

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(settings.log_level.upper())

    # Access logs duplicate our own request middleware output.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(
        logging.INFO if settings.db_echo else logging.WARNING
    )
