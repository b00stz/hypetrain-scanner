"""Config + logging setup shared by main.py and backtest.py."""
from __future__ import annotations

import logging
from pathlib import Path

import yaml
from dotenv import load_dotenv


def load_config(path: str = "config.yaml") -> dict:
    load_dotenv()  # populate os.environ from .env if present; no-op if already set
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def setup_logging(config: dict) -> None:
    log_cfg = config.get("logging", {})
    level = getattr(logging, str(log_cfg.get("level", "INFO")).upper(), logging.INFO)
    log_file = log_cfg.get("file")

    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file))

    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        handlers=handlers,
        force=True,
    )
