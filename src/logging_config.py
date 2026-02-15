import logging
import os


def configure_logging() -> None:
    """Configure app logging.

    Defaults to INFO. Override with LOG_LEVEL env var.
    """
    level_name = os.getenv("LOG_LEVEL", "INFO").upper().strip()
    level = getattr(logging, level_name, logging.INFO)

    # Avoid double-configuring if something already attached handlers.
    root = logging.getLogger()
    if root.handlers:
        root.setLevel(level)
        return

    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
