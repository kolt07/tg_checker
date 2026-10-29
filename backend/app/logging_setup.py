import logging
import sys

from app.security import redact


class SecretLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        # Uvicorn's access formatter unpacks record.args. Leave that record alone.
        if record.name == "uvicorn.access":
            return True
        try:
            rendered = record.getMessage()
        except Exception:
            rendered = str(record.msg)
        clean = redact(rendered)
        record.msg = clean
        record.args = ()
        record.message = clean
        return True


def setup_logging() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8")
        except Exception:
            continue
    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(SecretLogFilter())
    logging.basicConfig(
        level=logging.INFO,
        handlers=[handler],
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        force=True,
    )
    secret_filter = SecretLogFilter()
    root = logging.getLogger()
    root.addFilter(secret_filter)
    for name in ("uvicorn", "uvicorn.error", "telethon"):
        logger = logging.getLogger(name)
        logger.addFilter(secret_filter)
    logging.getLogger("telethon").setLevel(logging.WARNING)
