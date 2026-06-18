"""
test_reason.py
Tests for output_schema validation in ReasonEngine.
"""

import pytest
from ..reason import _validate_schema, _clean_json, _format_schema, _validate_json_schema
from ..models import OutputFieldSchema


# ── T7b — _validate_json_schema (recursive JSON Schema safety net) ─────────────

_FORM_SCHEMA = {
    "type": "object",
    "required": ["criterion_responses"],
    "properties": {
        "criterion_responses": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["criterion_id", "score"],
                "properties": {
                    "criterion_id": {"type": "string"},
                    "score":        {"type": ["number", "null"], "minimum": 0, "maximum": 10},
                    "na":           {"type": "boolean"},
                    "justification":{"type": "string"},
                },
            },
        },
        "overall_observation": {"type": "string"},
    },
}


def test_json_schema_accepts_valid_nested():
    data = {"criterion_responses": [
        {"criterion_id": "c1", "score": 8, "justification": "ok"},
        {"criterion_id": "c2", "score": None, "na": True},  # nullable score
    ]}
    assert _validate_json_schema(data, _FORM_SCHEMA) == []


def test_json_schema_flags_missing_required_top():
    assert any("criterion_responses" in e for e in _validate_json_schema({}, _FORM_SCHEMA))


def test_json_schema_flags_missing_required_nested():
    data = {"criterion_responses": [{"criterion_id": "c1"}]}  # falta score
    assert any("score" in e and "required" in e for e in _validate_json_schema(data, _FORM_SCHEMA))


def test_json_schema_flags_out_of_range():
    data = {"criterion_responses": [{"criterion_id": "c1", "score": 99}]}
    assert any("maximum" in e for e in _validate_json_schema(data, _FORM_SCHEMA))


def test_json_schema_flags_wrong_type():
    data = {"criterion_responses": [{"criterion_id": 5, "score": 8}]}
    assert any("expected string" in e for e in _validate_json_schema(data, _FORM_SCHEMA))


def test_json_schema_array_must_be_array():
    assert any("expected array" in e for e in _validate_json_schema(
        {"criterion_responses": {"criterion_id": "c1"}}, _FORM_SCHEMA))


# ── _validate_schema ──────────────────────────

def test_accepts_valid_object():
    schema = {
        "intent":     OutputFieldSchema(type="string", enum=["portabilidade", "cancelamento"]),
        "confidence": OutputFieldSchema(type="number", minimum=0, maximum=1),
    }
    # Should not raise
    _validate_schema({"intent": "portabilidade", "confidence": 0.92}, schema)


def test_rejects_invalid_enum():
    schema = {
        "intent": OutputFieldSchema(type="string", enum=["portabilidade", "cancelamento"]),
    }
    with pytest.raises(ValueError, match="enum"):
        _validate_schema({"intent": "INVALID"}, schema)


def test_rejects_missing_required_field():
    schema = {
        "intent":     OutputFieldSchema(type="string"),
        "confidence": OutputFieldSchema(type="number"),
    }
    with pytest.raises(ValueError, match="required"):
        _validate_schema({"intent": "portabilidade"}, schema)


def test_accepts_absent_optional_field():
    schema = {
        "intent":        OutputFieldSchema(type="string"),
        "justification": OutputFieldSchema(type="string", required=False),
    }
    # Should not raise — justification is optional
    _validate_schema({"intent": "portabilidade"}, schema)


def test_rejects_number_below_minimum():
    schema = {"confidence": OutputFieldSchema(type="number", minimum=0, maximum=1)}
    with pytest.raises(ValueError, match="minimum"):
        _validate_schema({"confidence": -0.5}, schema)


def test_rejects_number_above_maximum():
    schema = {"confidence": OutputFieldSchema(type="number", minimum=0, maximum=1)}
    with pytest.raises(ValueError, match="maximum"):
        _validate_schema({"confidence": 1.5}, schema)


def test_rejects_wrong_type():
    schema = {"active": OutputFieldSchema(type="boolean")}
    with pytest.raises(ValueError, match="boolean"):
        _validate_schema({"active": "true"}, schema)


# ── _clean_json ───────────────────────────────

def test_removes_markdown_code_block():
    raw = '```json\n{"key": "value"}\n```'
    assert _clean_json(raw) == '{"key": "value"}'


def test_removes_code_block_without_language():
    raw = '```\n{"key": "value"}\n```'
    assert _clean_json(raw) == '{"key": "value"}'


def test_does_not_alter_plain_json():
    raw = '{"key": "value"}'
    assert _clean_json(raw) == '{"key": "value"}'


# ── _format_schema ────────────────────────────

def test_format_schema_includes_name_and_type():
    schema = {"intent": OutputFieldSchema(type="string", enum=["a", "b"])}
    formatted = _format_schema(schema)
    assert "intent" in formatted
    assert "string" in formatted
    assert "['a', 'b']" in formatted or "a" in formatted
