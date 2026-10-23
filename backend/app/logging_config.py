"""Console logging for direct API/provider actions; never enable HTTP/SQL payload logs."""

from logging.config import dictConfig


def configure_logging(level: str) -> None:
    """Configure console levels while keeping HTTP and SQL payload logging disabled."""
    dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {"console": {"format": "%(asctime)s %(levelname)s %(name)s %(message)s"}},
            "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "console"}},
            "root": {"level": "WARNING", "handlers": ["console"]},
            "loggers": {
                "payments": {"level": level, "handlers": ["console"], "propagate": False},
                "uvicorn": {"level": level, "handlers": ["console"], "propagate": False},
                "uvicorn.access": {"level": "WARNING", "handlers": [], "propagate": False},
                "httpx": {"level": "WARNING", "handlers": [], "propagate": True},
                "httpcore": {"level": "WARNING", "handlers": [], "propagate": True},
                "sqlalchemy": {"level": "WARNING", "handlers": [], "propagate": True},
            },
        }
    )
