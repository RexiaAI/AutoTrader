from __future__ import annotations

import logging
import os
import threading
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

_cache_lock = threading.Lock()
_cached: dict[str, Any] | None = None
_cached_path: str | None = None


def _project_root() -> Path:
    # src/utils/config_loader.py -> src/utils -> src -> project root
    return Path(__file__).resolve().parents[2]


def default_config_path() -> Path:
    return _project_root() / "config" / "config.yaml"


def _apply_env_overrides(cfg: dict[str, Any]) -> None:
    """
    Override selected YAML settings with environment variables.

    This keeps runtime configuration flexible without duplicating config parsing logic.
    """
    broker = cfg.setdefault("broker", {})
    if os.getenv("AUTOTRADER_BROKER_HOST"):
        broker["host"] = os.environ["AUTOTRADER_BROKER_HOST"]
    if os.getenv("AUTOTRADER_BROKER_PORT"):
        broker["port"] = int(os.environ["AUTOTRADER_BROKER_PORT"])
    if os.getenv("AUTOTRADER_BROKER_CLIENT_ID"):
        broker["client_id"] = int(os.environ["AUTOTRADER_BROKER_CLIENT_ID"])

    ai = cfg.setdefault("ai", {})
    if os.getenv("AUTOTRADER_AI_MODEL"):
        ai["model"] = os.environ["AUTOTRADER_AI_MODEL"]

    intraday = cfg.setdefault("intraday", {})
    if os.getenv("AUTOTRADER_CYCLE_INTERVAL_SECONDS"):
        intraday["cycle_interval_seconds"] = int(os.environ["AUTOTRADER_CYCLE_INTERVAL_SECONDS"])


def validate_config(cfg: dict[str, Any]) -> None:
    """
    Fail fast if the configuration is missing required sections.
    Keep this minimal and pragmatic; avoid over-engineering.
    """
    required_top = ["broker", "trading", "ai"]
    missing = [k for k in required_top if k not in cfg]
    if missing:
        raise ValueError(f"Missing required config sections: {', '.join(missing)}")

    broker = cfg.get("broker") or {}
    for k in ["host", "port", "client_id"]:
        if k not in broker:
            raise ValueError(f"Missing broker.{k} in config")


def load_config(config_path: str | Path | None = None, *, force_reload: bool = False) -> dict[str, Any]:
    """
    Load the YAML config once and reuse it across the process.

    - Reads `config/config.yaml` by default.
    - Applies environment overrides for a small set of operational settings.
    - Returns a deep copy so callers can safely mutate local copies.
    """
    global _cached, _cached_path

    path = Path(config_path) if config_path else default_config_path()
    path_str = str(path.resolve())

    with _cache_lock:
        if not force_reload and _cached is not None and _cached_path == path_str:
            return deepcopy(_cached)

        if not path.exists():
            raise FileNotFoundError(f"Config file not found: {path}")

        with path.open("r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}

        if not isinstance(cfg, dict):
            raise ValueError(f"Config must be a YAML mapping (dict); got {type(cfg).__name__}")

        _apply_env_overrides(cfg)
        validate_config(cfg)

        _cached = cfg
        _cached_path = path_str
        logger.info("Loaded config from %s", path_str)
        return deepcopy(cfg)




