from src.research.prompts import (
    build_buy_selection_system_prompt,
    build_shortlist_system_prompt,
    get_prompt_templates,
)


def test_shortlist_prompt_contains_output_schema_and_decision_values():
    cfg = {"ai": {}}
    p = build_shortlist_system_prompt(cfg)
    assert "=== OUTPUT ===" in p
    assert "decision: SHORTLIST | SKIP" in p


def test_shortlist_prompt_override_is_used_and_output_is_appended():
    cfg = {"ai": {"shortlist_system_prompt": "CUSTOM SHORTLIST PROMPT"}}
    p = build_shortlist_system_prompt(cfg)
    assert p.splitlines()[0] == "CUSTOM SHORTLIST PROMPT"
    assert "=== OUTPUT ===" in p


def test_buy_selection_prompt_contains_output_schema():
    cfg = {"ai": {}}
    p = build_buy_selection_system_prompt(cfg)
    assert "=== OUTPUT ===" in p
    assert "selected_symbols:" in p


def test_get_prompt_templates_excludes_output_schema():
    t = get_prompt_templates()
    assert "shortlist" in t
    assert "buy_selection" in t
    assert "=== OUTPUT ===" not in t["shortlist"]


