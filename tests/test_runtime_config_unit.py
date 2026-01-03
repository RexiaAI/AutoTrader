import pytest

from src.utils.runtime_config import (
    apply_runtime_config,
    default_runtime_config,
    normalise_runtime_config,
    validate_runtime_config,
)


def test_default_runtime_config_shape():
    doc = default_runtime_config()
    assert doc["schema_version"] == 1
    assert doc["overrides"] == {}
    assert doc["active_strategy"] == "Default"
    assert isinstance(doc["strategies"], list) and doc["strategies"]
    assert doc["strategies"][0]["name"] == "Default"


def test_normalise_runtime_config_migrates_legacy_prompt_key():
    doc = {
        "schema_version": 1,
        "overrides": {},
        "strategies": [
            {
                "name": "Default",
                "overrides": {
                    "ai": {
                        "trade_decision_system_prompt": "legacy prompt",
                        "trade_decision_prompt_addendum": "legacy addendum",
                    }
                },
            }
        ],
        "active_strategy": "Default",
    }
    out = normalise_runtime_config(doc)
    ai = out["strategies"][0]["overrides"]["ai"]
    assert ai.get("shortlist_system_prompt") == "legacy prompt"
    assert "trade_decision_system_prompt" not in ai
    assert "trade_decision_prompt_addendum" not in ai


def test_validate_runtime_config_rejects_unknown_override_key():
    doc = {
        "schema_version": 1,
        "overrides": {"trading": {"made_up_key": 123}},
        "strategies": [{"name": "Default", "overrides": {}}],
        "active_strategy": "Default",
    }
    with pytest.raises(ValueError, match=r"Unsupported override key"):
        validate_runtime_config(doc)


def test_validate_runtime_config_allows_min_cash_reserve_map_leaf():
    doc = {
        "schema_version": 1,
        "overrides": {"trading": {"min_cash_reserve_by_currency": {"USD": 5000, "GBP": 0}}},
        "strategies": [{"name": "Default", "overrides": {}}],
        "active_strategy": "Default",
    }
    validate_runtime_config(doc)


def test_validate_runtime_config_allows_screener_controls():
    doc = {
        "schema_version": 1,
        "overrides": {
            "trading": {
                "screener": {
                    "max_candidates": 120,
                    "scan_codes": ["MOST_ACTIVE", "TOP_PERC_GAIN"],
                    "include_reddit_symbols": True,
                    "include_symbols": ["AAPL,US", "VOD,UK"],
                    "exclude_symbols": ["GME", "AMC"],
                }
            }
        },
        "strategies": [{"name": "Default", "overrides": {}}],
        "active_strategy": "Default",
    }
    validate_runtime_config(doc)


def test_apply_runtime_config_merges_active_strategy_overrides():
    base = {
        "broker": {"host": "127.0.0.1", "port": 7497, "client_id": 10},
        "trading": {"max_positions": 10, "max_cash_utilisation": 0.3, "risk_per_trade": 0.05, "max_new_positions_per_cycle": 2},
        "ai": {"model": "gpt-4.1-mini"},
    }
    runtime = {
        "schema_version": 1,
        "overrides": {},
        "strategies": [{"name": "Default", "overrides": {"trading": {"max_positions": 5}}}],
        "active_strategy": "Default",
    }
    out = apply_runtime_config(base, runtime)
    assert out["trading"]["max_positions"] == 5


def test_apply_runtime_config_merges_global_then_strategy():
    base = {
        "broker": {"host": "127.0.0.1", "port": 7497, "client_id": 10},
        "trading": {"max_positions": 10, "max_cash_utilisation": 0.3, "risk_per_trade": 0.05, "max_new_positions_per_cycle": 2},
        "ai": {"model": "gpt-4.1-mini"},
    }
    runtime = {
        "schema_version": 1,
        "overrides": {"trading": {"max_positions": 3}},
        "strategies": [{"name": "Default", "overrides": {"trading": {"max_positions": 5}}}],
        "active_strategy": "Default",
    }
    out = apply_runtime_config(base, runtime)
    assert out["trading"]["max_positions"] == 5


