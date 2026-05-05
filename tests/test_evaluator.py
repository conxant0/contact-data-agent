import pytest
from unittest.mock import patch

import modules.evaluator as evaluator_module
from modules.evaluator import evaluate

GOAL = "Find the partnerships decision-maker and their direct contact email"

ALL_FOUND = [
    {"field": "partnerships decision-maker", "value": "Maria Santos", "found": True},
    {"field": "direct contact email", "value": "maria@habitat.org.ph", "found": True},
]

ONE_MISSING = [
    {"field": "partnerships decision-maker", "value": "Maria Santos", "found": True},
    {"field": "direct contact email", "value": None, "found": False},
]

GROQ_COMPLETE = {
    "decision": "complete",
    "gaps": [],
    "reasoning": "Both requested fields have been provided.",
}

GROQ_FOLLOW_UP = {
    "decision": "follow_up_needed",
    "gaps": [{"field": "direct contact email", "reason": "not mentioned in reply"}],
    "reasoning": "The direct contact email was not provided.",
}


def test_evaluate_returns_complete_when_all_fields_found():
    with patch.object(evaluator_module, "_call_groq", return_value=GROQ_COMPLETE):
        result = evaluate(GOAL, ALL_FOUND, iteration=1, max_iterations=3)
    assert result["decision"] == "complete"
    assert result["gaps"] == []
    assert "reasoning" in result


def test_evaluate_returns_follow_up_needed_with_gap_when_field_missing():
    with patch.object(evaluator_module, "_call_groq", return_value=GROQ_FOLLOW_UP):
        result = evaluate(GOAL, ONE_MISSING, iteration=1, max_iterations=3)
    assert result["decision"] == "follow_up_needed"
    assert len(result["gaps"]) == 1
    assert result["gaps"][0]["field"] == "direct contact email"
    assert "reasoning" in result


def test_evaluate_returns_complete_at_max_iterations_without_calling_groq():
    with patch.object(evaluator_module, "_call_groq") as mock_groq:
        result = evaluate(GOAL, ONE_MISSING, iteration=3, max_iterations=3)
    mock_groq.assert_not_called()
    assert result["decision"] == "complete"
    assert result["gaps"] == []
