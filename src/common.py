"""
Shared configuration, logging and database access for the IPEDS warehouse.

Every script in this project imports from here rather than building its own
connection string or its own logger. Two reasons:

  1. One place to change. When the server name, driver version or database
     name changes, it changes once, not in a dozen scripts.
  2. One log, one format. A pipeline is auditable only if every stage writes
     to the same place in the same shape. "Which script wrote this line and
     when" should never be a guess.

Configuration comes from config/config.yaml, which is committed. Anything
sensitive comes from .env, which is not. Environment variables win over the
YAML file, so a different machine can point somewhere else without editing
a tracked file.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
from pathlib import Path
from typing import Any

import pyodbc
import yaml
from dotenv import load_dotenv

# The project root is the parent of the directory holding this file.
# Resolving it this way means scripts work regardless of the directory they
# are invoked from, which matters because the pipeline is run from the root
# but individual scripts are often run from an editor.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config" / "config.yaml"

load_dotenv(PROJECT_ROOT / ".env")

_config_cache: dict[str, Any] | None = None
_logging_configured = False


def get_config() -> dict[str, Any]:
    """Load config/config.yaml once and reuse it."""
    global _config_cache
    if _config_cache is None:
        with open(CONFIG_PATH, "r", encoding="utf-8") as handle:
            _config_cache = yaml.safe_load(handle)
    return _config_cache


def resolve_path(key: str) -> Path:
    """
    Turn a path key from config.yaml into an absolute path and make sure the
    directory exists. Paths in the config file are relative to the project
    root so the repository stays portable.
    """
    config = get_config()
    relative = config["paths"][key]
    absolute = PROJECT_ROOT / relative
    absolute.mkdir(parents=True, exist_ok=True)
    return absolute


def get_connection_string() -> str:
    """
    Build the ODBC connection string.

    Environment variables override the YAML values so that a machine with a
    different server, or one using SQL authentication instead of Windows
    authentication, needs no code change.
    """
    database_config = get_config()["database"]

    driver = database_config["driver"]
    server = os.getenv("DB_SERVER") or database_config["server"]
    name = os.getenv("DB_NAME") or database_config["name"]
    timeout = database_config.get("connect_timeout_seconds", 30)

    parts = [
        f"DRIVER={{{driver}}}",
        f"SERVER={server}",
        f"DATABASE={name}",
        f"Connection Timeout={timeout}",
    ]

    if database_config.get("trust_server_certificate", False):
        parts.append("TrustServerCertificate=yes")

    user = os.getenv("DB_USER")
    password = os.getenv("DB_PASSWORD")

    if database_config.get("trusted_connection", True) and not user:
        parts.append("Trusted_Connection=yes")
    else:
        # SQL authentication path. Credentials come from .env only, never
        # from the committed config file.
        if not user or not password:
            raise RuntimeError(
                "SQL authentication is configured but DB_USER or DB_PASSWORD "
                "is missing from .env"
            )
        parts.append(f"UID={user}")
        parts.append(f"PWD={password}")

    return ";".join(parts) + ";"


def get_connection() -> pyodbc.Connection:
    """
    Open a connection to the warehouse.

    Use as a context manager so the connection is always closed:

        with get_connection() as connection:
            ...
    """
    return pyodbc.connect(get_connection_string())


def get_logger(name: str) -> logging.Logger:
    """
    Return a logger that writes to both the console and the rotating pipeline
    log file. Safe to call from every module; handlers are attached once.

    The file log is the audit trail. It is appended to, never truncated, and
    every line carries a timestamp, the module that wrote it, and the level.
    """
    global _logging_configured

    if not _logging_configured:
        config = get_config()
        logging_config = config.get("logging", {})

        level = getattr(logging, logging_config.get("level", "INFO").upper())
        log_file = PROJECT_ROOT / logging_config.get(
            "file", "data/logs/pipeline.log"
        )
        log_file.parent.mkdir(parents=True, exist_ok=True)

        formatter = logging.Formatter(
            fmt="%(asctime)s | %(levelname)-8s | %(name)-24s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)

        file_handler = logging.handlers.RotatingFileHandler(
            filename=log_file,
            maxBytes=logging_config.get("rotate_max_bytes", 10_485_760),
            backupCount=logging_config.get("rotate_backup_count", 5),
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)

        root_logger = logging.getLogger()
        root_logger.setLevel(level)
        root_logger.addHandler(console_handler)
        root_logger.addHandler(file_handler)

        _logging_configured = True

    return logging.getLogger(name)


if __name__ == "__main__":
    # Running this module directly is a self test of the whole arrangement:
    # config loads, paths resolve, logging writes, the database answers.
    log = get_logger("common.selftest")

    config = get_config()
    log.info("Config loaded from %s", CONFIG_PATH)
    log.info(
        "Ingest years configured: %s to %s",
        config["ingest"]["year_start"],
        config["ingest"]["year_end"],
    )

    for key in ("raw", "interim", "logs", "reports"):
        log.info("Path %-8s resolves to %s", key, resolve_path(key))

    with get_connection() as connection:
        cursor = connection.cursor()
        cursor.execute("SELECT DB_NAME(), SUSER_NAME();")
        database_name, login = cursor.fetchone()
        log.info("Connected to %s as %s", database_name, login)

    log.info("Self test complete. Config, paths, logging and database all working.")
