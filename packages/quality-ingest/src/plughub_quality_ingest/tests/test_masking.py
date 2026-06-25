"""Tests for the masking net-pass (mirror of DEFAULT_MASKING_RULES)."""
from plughub_quality_ingest.masking import mask_text


def test_masks_cpf_preserving_last_2():
    masked, cats = mask_text("meu cpf e 123.456.789-01 ok")
    assert "123.456.789-01" not in masked
    assert "01" in masked          # preserve_last_digits=2
    assert masked.endswith("ok")
    assert cats == ["cpf"]


def test_masks_email_preserving_domain():
    masked, cats = mask_text("escreva para joao.silva@example.com por favor")
    assert "joao.silva" not in masked
    assert "****@example.com" in masked   # local masked, domain preserved, single @
    assert masked.count("@") == 1
    assert cats == ["email_addr"]


def test_masks_credit_card_last_4():
    masked, cats = mask_text("cartao 4111 1111 1111 1234")
    assert "4111 1111 1111 1234" not in masked
    assert masked.strip().endswith("1234")
    assert cats == ["credit_card"]


def test_multiple_categories_detected():
    masked, cats = mask_text("cpf 123.456.789-01 email a@b.com")
    assert "cpf" in cats and "email_addr" in cats
    assert "123.456.789-01" not in masked
    assert "a@b.com" not in masked


def test_clean_text_unchanged_no_categories():
    masked, cats = mask_text("obrigado pelo contato, tenha um bom dia")
    assert masked == "obrigado pelo contato, tenha um bom dia"
    assert cats == []


def test_idempotent_on_already_masked():
    once, _ = mask_text("cpf 123.456.789-01")
    twice, cats = mask_text(once)
    assert twice == once
    assert cats == []      # no PII pattern remains


def test_empty_text():
    assert mask_text("") == ("", [])
