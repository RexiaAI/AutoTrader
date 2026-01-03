from __future__ import annotations

from copy import deepcopy
from typing import Any


def default_runtime_config() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "overrides": {},
        "strategies": [{"name": "Default", "overrides": {}}],
        "active_strategy": "Default",
    }


def normalise_runtime_config(doc: dict[str, Any] | None) -> dict[str, Any]:
    if doc is None:
        return default_runtime_config()
    if not isinstance(doc, dict):
        raise ValueError(f"runtime config must be an object; got {type(doc).__name__}")

    out = deepcopy(doc)
    if "schema_version" not in out:
        out["schema_version"] = 1
    if "overrides" not in out or out["overrides"] is None:
        out["overrides"] = {}
    if "strategies" not in out or out["strategies"] is None:
        out["strategies"] = [{"name": "Default", "overrides": {}}]
    if "active_strategy" not in out:
        out["active_strategy"] = "Default"

    # Migrate/remove deprecated keys that could break validation after upgrades.
    for holder in [out.get("overrides"), *[(s or {}).get("overrides") for s in (out.get("strategies") or [])]]:
        if not isinstance(holder, dict):
            continue
        ai = holder.get("ai")
        if isinstance(ai, dict):
            # Migrate trade_decision_system_prompt -> shortlist_system_prompt
            if "trade_decision_system_prompt" in ai and "shortlist_system_prompt" not in ai:
                ai["shortlist_system_prompt"] = ai["trade_decision_system_prompt"]
            # Remove deprecated keys
            for k in (
                "trade_decision_enabled",
                "sentiment_threshold",
                "sentiment_analysis_enabled",
                "trade_decision_system_prompt",
                "trade_decision_prompt_addendum",
                "buy_selection_prompt_addendum",
                "position_review_prompt_addendum",
                "order_review_prompt_addendum",
            ):
                ai.pop(k, None)

        pm = holder.get("position_management")
        if isinstance(pm, dict):
            pm.pop("enabled", None)
    return out


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _flatten_overrides(overrides: dict[str, Any], *, prefix: str = "") -> list[tuple[str, Any]]:
    out: list[tuple[str, Any]] = []
    for k, v in overrides.items():
        key = f"{prefix}.{k}" if prefix else str(k)
        if isinstance(v, dict):
            # Some override keys are intended to be objects (maps) as a single value, not a tree of override keys.
            # Example: trading.min_cash_reserve_by_currency is a map of { "USD": 5000, "GBP": 0 }.
            if key in {"trading.min_cash_reserve_by_currency"}:
                out.append((key, v))
            else:
                out.extend(_flatten_overrides(v, prefix=key))
        else:
            out.append((key, v))
    return out


def _validate_markets(v: Any) -> None:
    if not isinstance(v, list) or not v:
        raise ValueError("trading.markets must be a non-empty array")
    allowed = {"US", "UK"}
    norm = []
    for m in v:
        if not isinstance(m, str) or not m.strip():
            raise ValueError("trading.markets entries must be non-empty strings")
        mm = m.strip().upper()
        if mm not in allowed:
            raise ValueError(f"Unsupported market: {mm}")
        norm.append(mm)


def _validate_bool(v: Any, *, name: str) -> None:
    if not isinstance(v, bool):
        raise ValueError(f"{name} must be boolean")


def _validate_float_0_1(v: Any, *, name: str) -> None:
    if not _is_number(v):
        raise ValueError(f"{name} must be a number")
    vv = float(v)
    if vv < 0.0 or vv > 1.0:
        raise ValueError(f"{name} must be between 0 and 1")


def _validate_positive_number(v: Any, *, name: str) -> None:
    if not _is_number(v):
        raise ValueError(f"{name} must be a number")
    if float(v) <= 0:
        raise ValueError(f"{name} must be > 0")


def _validate_non_negative_int(v: Any, *, name: str) -> None:
    if not isinstance(v, int) or isinstance(v, bool):
        raise ValueError(f"{name} must be an integer")
    if v < 0:
        raise ValueError(f"{name} must be >= 0")


def _validate_positive_int(v: Any, *, name: str) -> None:
    _validate_non_negative_int(v, name=name)
    if int(v) <= 0:
        raise ValueError(f"{name} must be > 0")


def _validate_string_list(v: Any, *, name: str, max_items: int = 500) -> None:
    if not isinstance(v, list):
        raise ValueError(f"{name} must be an array")
    if len(v) > max_items:
        raise ValueError(f"{name} must have at most {max_items} entries")
    for item in v:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{name} entries must be non-empty strings")


def _validate_min_cash_reserve(v: Any) -> None:
    if not isinstance(v, dict):
        raise ValueError("trading.min_cash_reserve_by_currency must be an object")
    for k, val in v.items():
        if not isinstance(k, str) or not k.strip():
            raise ValueError("Currency codes must be non-empty strings")
        if not _is_number(val):
            raise ValueError(f"Reserve for {k} must be a number")
        if float(val) < 0:
            raise ValueError(f"Reserve for {k} must be >= 0")


def _raise(msg: str) -> None:
    raise ValueError(msg)


def _validate_prompt_addendum(v: Any, *, name: str) -> None:
    if not isinstance(v, str):
        raise ValueError(f"{name} must be a string")
    # Keep this bounded: overly long prompts are expensive and can break the bot's output contract.
    if len(v) > 8000:
        raise ValueError(f"{name} is too long (max 8000 characters)")

def _validate_prompt_override(v: Any, *, name: str) -> None:
    if not isinstance(v, str):
        raise ValueError(f"{name} must be a string")
    # Full prompts can be longer than addenda, but keep them bounded.
    if len(v) > 20000:
        raise ValueError(f"{name} is too long (max 20000 characters)")


_ALLOWED_OVERRIDE_VALIDATORS: dict[str, Any] = {
    # Trading / risk
    "trading.max_cash_utilisation": lambda v: _validate_float_0_1(v, name="trading.max_cash_utilisation"),
    "trading.risk_per_trade": lambda v: _validate_float_0_1(v, name="trading.risk_per_trade"),
    "trading.max_positions": lambda v: _validate_non_negative_int(v, name="trading.max_positions"),
    "trading.max_new_positions_per_cycle": lambda v: _validate_non_negative_int(v, name="trading.max_new_positions_per_cycle"),
    "trading.cash_budget_tag": lambda v: (isinstance(v, str) and v.strip()) or (_raise("trading.cash_budget_tag must be a non-empty string")),
    "trading.markets": _validate_markets,
    "trading.min_cash_reserve_by_currency": _validate_min_cash_reserve,
    "trading.max_share_price": lambda v: _validate_positive_number(v, name="trading.max_share_price"),
    "trading.min_share_price": lambda v: _validate_positive_number(v, name="trading.min_share_price"),
    "trading.min_avg_volume": lambda v: _validate_non_negative_int(v, name="trading.min_avg_volume"),
    "trading.exclude_microcap": lambda v: _validate_bool(v, name="trading.exclude_microcap"),
    # Screener / universe selection
    "trading.screener.max_candidates": lambda v: _validate_positive_int(v, name="trading.screener.max_candidates"),
    "trading.screener.scan_codes": lambda v: _validate_string_list(v, name="trading.screener.scan_codes", max_items=20),
    "trading.screener.include_reddit_symbols": lambda v: _validate_bool(v, name="trading.screener.include_reddit_symbols"),
    "trading.screener.include_symbols": lambda v: _validate_string_list(v, name="trading.screener.include_symbols", max_items=500),
    "trading.screener.exclude_symbols": lambda v: _validate_string_list(v, name="trading.screener.exclude_symbols", max_items=500),
    "trading.volatility_threshold": lambda v: (_is_number(v) and float(v) >= 0) or (_raise("trading.volatility_threshold must be >= 0")),
    # AI
    "ai.model": lambda v: (isinstance(v, str) and v.strip()) or (_raise("ai.model must be a non-empty string")),
    "ai.shortlist_system_prompt": lambda v: _validate_prompt_override(v, name="ai.shortlist_system_prompt"),
    "ai.buy_selection_system_prompt": lambda v: _validate_prompt_override(v, name="ai.buy_selection_system_prompt"),
    "ai.position_review_system_prompt": lambda v: _validate_prompt_override(v, name="ai.position_review_system_prompt"),
    "ai.order_review_system_prompt": lambda v: _validate_prompt_override(v, name="ai.order_review_system_prompt"),
    # Legacy keys (backward compat - migrated in normalise_runtime_config)
    "ai.trade_decision_system_prompt": lambda v: _validate_prompt_override(v, name="ai.trade_decision_system_prompt"),
    "ai.trade_decision_prompt_addendum": lambda v: _validate_prompt_override(v, name="ai.trade_decision_prompt_addendum"),
    "ai.buy_selection_prompt_addendum": lambda v: _validate_prompt_override(v, name="ai.buy_selection_prompt_addendum"),
    "ai.position_review_prompt_addendum": lambda v: _validate_prompt_override(v, name="ai.position_review_prompt_addendum"),
    "ai.order_review_prompt_addendum": lambda v: _validate_prompt_override(v, name="ai.order_review_prompt_addendum"),
    "ai.sentiment_threshold": lambda v: _validate_float_0_1(v, name="ai.sentiment_threshold"),
    # Intraday
    "intraday.enabled": lambda v: _validate_bool(v, name="intraday.enabled"),
    "intraday.cycle_interval_seconds": lambda v: _validate_non_negative_int(v, name="intraday.cycle_interval_seconds"),
    "intraday.cycle_interval_seconds_closed": lambda v: _validate_non_negative_int(v, name="intraday.cycle_interval_seconds_closed"),
    "intraday.flatten_minutes_before_close": lambda v: _validate_non_negative_int(v, name="intraday.flatten_minutes_before_close"),
    # Features
    "reddit.enabled": lambda v: _validate_bool(v, name="reddit.enabled"),
}


def validate_runtime_config(doc: dict[str, Any]) -> None:
    if not isinstance(doc, dict):
        raise ValueError("runtime config must be an object")

    schema_version = doc.get("schema_version", 1)
    if schema_version != 1:
        raise ValueError(f"Unsupported runtime config schema_version: {schema_version}")

    overrides = doc.get("overrides", {})
    if not isinstance(overrides, dict):
        raise ValueError("runtime.overrides must be an object")

    strategies = doc.get("strategies", [])
    if not isinstance(strategies, list) or not strategies:
        raise ValueError("runtime.strategies must be a non-empty array")
    seen: set[str] = set()
    for s in strategies:
        if not isinstance(s, dict):
            raise ValueError("Each strategy must be an object")
        name = s.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Strategy name must be a non-empty string")
        name = name.strip()
        if name in seen:
            raise ValueError(f"Duplicate strategy name: {name}")
        seen.add(name)
        ov = s.get("overrides", {})
        if not isinstance(ov, dict):
            raise ValueError(f"Strategy overrides for {name} must be an object")
        _validate_override_dict(ov)

    active = doc.get("active_strategy")
    if active is not None:
        if not isinstance(active, str) or not active.strip():
            raise ValueError("active_strategy must be a non-empty string or null")
        if active.strip() not in seen:
            raise ValueError(f"active_strategy not found in strategies: {active.strip()}")

    _validate_override_dict(overrides)


def _validate_override_dict(overrides: dict[str, Any]) -> None:
    # Disallow unknown keys for now; keeps the system predictable.
    for path, value in _flatten_overrides(overrides):
        validator = _ALLOWED_OVERRIDE_VALIDATORS.get(path)
        if validator is None:
            raise ValueError(f"Unsupported override key: {path}")
        validator(value)


def deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = deepcopy(base)
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)  # type: ignore[arg-type]
        else:
            out[k] = deepcopy(v)
    return out


def apply_runtime_config(base_config: dict[str, Any], runtime_doc: dict[str, Any]) -> dict[str, Any]:
    doc = normalise_runtime_config(runtime_doc)
    validate_runtime_config(doc)

    cfg = deep_merge(base_config, doc.get("overrides", {}) or {})

    active = doc.get("active_strategy")
    strategies = doc.get("strategies", []) or []
    if active:
        for s in strategies:
            if s.get("name") == active:
                cfg = deep_merge(cfg, s.get("overrides", {}) or {})
                break

    return cfg


