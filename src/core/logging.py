import logging
import sys

def setup_logging(level: str = "DEBUG"):
    """Setter opp logging til stdout med fast format."""
    root = logging.getLogger()
    if not root.handlers:
        handler = logging.StreamHandler(sys.stdout)
        formatter = logging.Formatter(
            "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
        )
        handler.setFormatter(formatter)
        root.addHandler(handler)

    root.setLevel(level.upper())
