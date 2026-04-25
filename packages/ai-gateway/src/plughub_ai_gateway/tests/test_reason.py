"""
test_reason.py
Tests for output_schema validation in ReasonEngine.
"""

import pytest
from ..reason import _validate_schema, _clean_json, _format_schema
from ..models import OutputFieldSchema


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
