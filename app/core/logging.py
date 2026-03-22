"""
Structured Logging Configuration for Drishti Intelligence Platform
"""

import logging
import sys
import json
import traceback
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from contextvars import ContextVar
from logging.handlers import RotatingFileHandler
from pathlib import Path

request_id_var: ContextVar[Optional[str]] = ContextVar("request_id", default=None)
user_id_var: ContextVar[Optional[str]] = ContextVar("user_id", default=None)
request_extra_var: ContextVar[Dict[str, Any]] = ContextVar("request_extra", default=None)


class CustomJsonFormatter(logging.Formatter):
    def __init__(self, *args, **kwargs):
        self.include_extra = kwargs.pop("include_extra", True)
        self.timestamp_format = kwargs.pop("timestamp_format", "iso")
        super().__init__(*args, **kwargs)

    def add_fields(self, log_record: Dict[str, Any], record: logging.LogRecord, message_dict: Dict[str, Any]) -> None:
        # Python 3.11+ logging.Formatter doesn't have add_fields, so we implement our own
        if self.timestamp_format == "iso":
            log_record["timestamp"] = datetime.now(timezone.utc).isoformat()
        else:
            log_record["timestamp"] = record.created

        log_record["level"] = record.levelname
        log_record["logger"] = record.name

        request_id = request_id_var.get()
        if request_id:
            log_record["request_id"] = request_id

        user_id = user_id_var.get()
        if user_id:
            log_record["user_id"] = user_id

        request_extra = request_extra_var.get()
        if request_extra and self.include_extra:
            log_record["extra_data"] = request_extra

        if record.exc_info:
            log_record["exception"] = {
                "type": record.exc_info[0].__name__ if record.exc_info[0] else None,
                "message": str(record.exc_info[1]) if record.exc_info[1] else None,
                "traceback": traceback.format_exception(*record.exc_info) if record.exc_info else None,
            }

        if "message" not in log_record:
            log_record["message"] = record.getMessage()

    def format(self, record: logging.LogRecord) -> str:
        log_record: Dict[str, Any] = {}
        self.add_fields(log_record, record, {})
        return json.dumps(log_record, default=str)


class StandardFormatter(logging.Formatter):
    grey = "\x1b[38;21m"
    blue = "\x1b[38;5;39m"
    yellow = "\x1b[38;5;226m"
    red = "\x1b[38;5;196m"
    reset = "\x1b[0m"

    FORMATS = {
        logging.DEBUG: grey + "[%(asctime)s] %(levelname)s | %(name)s | %(message)s" + reset,
        logging.INFO: blue + "[%(asctime)s] %(levelname)s | %(name)s | %(message)s" + reset,
        logging.WARNING: yellow + "[%(asctime)s] %(levelname)s | %(name)s | %(message)s" + reset,
        logging.ERROR: red + "[%(asctime)s] %(levelname)s | %(name)s | %(message)s" + reset,
        logging.CRITICAL: "\x1b[31;1m" + "[%(asctime)s] %(levelname)s | %(name)s | %(message)s" + reset,
    }

    def format(self, record: logging.LogRecord) -> str:
        log_fmt = self.FORMATS.get(record.levelno, self.FORMATS[logging.INFO])
        request_id = request_id_var.get()
        user_id = user_id_var.get()
        extra_info = []
        if request_id:
            extra_info.append(f"req_id={request_id}")
        if user_id:
            extra_info.append(f"user_id={user_id}")
        if extra_info:
            record.message = f"{record.getMessage()} | {' | '.join(extra_info)}"
        return logging.Formatter(log_fmt, datefmt="%Y-%m-%d %H:%M:%S").format(record)


def setup_logging(log_level: Optional[str] = None, log_file: Optional[str] = None, json_format: bool = True) -> None:
    import os
    from .config import settings

    if log_level is None:
        log_level = os.getenv("LOG_LEVEL", "INFO")

    if os.getenv("LOG_FORMAT", "json").lower() == "standard":
        json_format = False

    root_logger = logging.getLogger()
    root_logger.handlers.clear()

    numeric_level = getattr(logging, log_level.upper(), logging.INFO)
    root_logger.setLevel(numeric_level)

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("uvicorn").setLevel(logging.INFO)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

    formatter = CustomJsonFormatter() if json_format else StandardFormatter()

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(numeric_level)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=10_000_000,  # 10 MB
            backupCount=5,
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(CustomJsonFormatter())
        root_logger.addHandler(file_handler)
    elif os.getenv("LOG_FILE"):
        log_file = os.getenv("LOG_FILE")
        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=10_000_000,  # 10 MB
            backupCount=5,
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(CustomJsonFormatter())
        root_logger.addHandler(file_handler)


class LoggerAdapter(logging.LoggerAdapter):
    def process(self, msg: str, kwargs: Dict[str, Any]) -> tuple:
        extra = kwargs.get("extra", {}).copy()
        request_id = request_id_var.get()
        if request_id and "request_id" not in extra:
            extra["request_id"] = request_id
        user_id = user_id_var.get()
        if user_id and "user_id" not in extra:
            extra["user_id"] = user_id
        request_extra = request_extra_var.get()
        if request_extra:
            extra["extra_data"] = request_extra
        kwargs["extra"] = extra
        return msg, kwargs


def get_logger(name: str) -> LoggerAdapter:
    logger = logging.getLogger(name)
    return LoggerAdapter(logger, {})


def get_processor_logger() -> LoggerAdapter:
    return get_logger("app.services.processor")


def get_api_logger() -> LoggerAdapter:
    return get_logger("app.api")


class logging_context:
    def __init__(self, request_id: Optional[str] = None, user_id: Optional[str] = None, **extra):
        self.request_id = request_id
        self.user_id = user_id
        self.extra = extra if extra else None
        self._tokens = []

    def __enter__(self):
        if self.request_id:
            self._tokens.append(("request_id", request_id_var.set(self.request_id)))
        if self.user_id:
            self._tokens.append(("user_id", user_id_var.set(self.user_id)))
        if self.extra:
            current_extra = request_extra_var.get() or {}
            current_extra.update(self.extra)
            self._tokens.append(("extra", request_extra_var.set(current_extra)))
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        for name, token in reversed(self._tokens):
            if name == "request_id":
                request_id_var.reset(token)
            elif name == "user_id":
                user_id_var.reset(token)
            elif name == "extra":
                request_extra_var.reset(token)
