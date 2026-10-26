import logging
import os
from datetime import datetime

CONSOLE_FORMAT = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
MQTT_CONSOLE_FORMAT = logging.Formatter("[MQTT] %(asctime)s %(levelname)s %(message)s")
FILE_FORMAT = logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s")


def setup_loggers() -> None:
    """Every logger writes to logs/. They differ only in what reaches the console.

    "app" (and root): WARNING+. "console": INFO+. "file": nothing. "mqtt": ERROR+.
    "mqtt" is also the logger aiomqtt writes to internally.
    """
    os.makedirs("logs", exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = f"logs/mqtt_app_{timestamp}.log"

    file_handler = logging.FileHandler(log_file)
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(FILE_FORMAT)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.handlers.clear()
    root.addHandler(file_handler)
    root.addHandler(_console_handler(logging.WARNING, CONSOLE_FORMAT))

    _own_handlers("console", logging.INFO, file_handler, _console_handler(logging.INFO, CONSOLE_FORMAT))
    _own_handlers("file", logging.DEBUG, file_handler)
    _own_handlers("mqtt", logging.INFO, file_handler, _console_handler(logging.ERROR, MQTT_CONSOLE_FORMAT))

    logging.getLogger("app").warning("Logging initialized. Full logs writing to %s", log_file)


def _console_handler(level: int, formatter: logging.Formatter) -> logging.Handler:
    handler = logging.StreamHandler()
    handler.setLevel(level)
    handler.setFormatter(formatter)
    return handler


def _own_handlers(name: str, level: int, *handlers: logging.Handler) -> None:
    """Give a logger its own handlers. propagate=False stops root printing it a second time."""
    logger = logging.getLogger(name)
    logger.setLevel(level)
    logger.handlers.clear()
    for handler in handlers:
        logger.addHandler(handler)
    logger.propagate = False
