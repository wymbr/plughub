"""Tests for the masking net-pass (mirror of DEFAULT_MASKING_RULES)."""
from plughub_quality_ingest.masking import mask_text


def test_masks_cpf_preserving_last_2():
    masked, cats = mask_text("meu cpf e 123.456.789-01 ok")
    assert "123.456.789-01" not in masked
    assert "01" in masked          # preserve_last_digits=2
    assert masked.endswith("ok")
    assert cats == ["cpf"]


def test_masks_email_preserving_domain():
    # ⚠️ Asserção mudada em 2026-08-26 com a semântica canônica (buildDisplay do TS):
    # a quantidade de `*` é `ceil(len(prefixo)/4)`, não um literal de `replacement`.
    # "joao.silva" tem 10 chars ⇒ 3 estrelas. O `****` que esta linha exigia era a
    # semântica DIVERGENTE que esta porta usava.
    masked, cats = mask_text("escreva para joao.silva@example.com por favor")
    assert "joao.silva" not in masked
    assert "***@example.com" in masked   # local masked, domain preserved, single @
    assert masked.count("@") == 1
    assert cats == ["email_addr"]


def test_display_is_canonical_not_replacement():
    """A porta Python tem de produzir o MESMO display que o stream vivo.

    Estes valores não são gosto: são a saída medida de `MaskingService.buildDisplay`
    (`infra/test/q_masking_display_parity.sh`). Se alguém reintroduzir a montagem por
    `replacement`, estas três linhas ficam vermelhas — e era exatamente isso que
    NENHUM teste pegava, porque nenhum comparava as portas entre si.
    """
    assert mask_text("123.456.789-00")[0] == "*********00"
    assert mask_text("1234 5678 9012 3456")[0] == "************3456"
    assert mask_text("1234-5678-9012-3456")[0] == "************3456"


def test_phone_in_parens_consumes_the_opening_paren():
    """O `\\(?` do regex de telefone precisa poder casar.

    Com o `\\b` inicial ele era RAMO MORTO — `\\b` exige transição \\W→\\w, que nunca
    ocorre antes de `(`. O match começava no dígito e o parêntese sobrava colado à
    máscara (`((##) ****-4321`). Testemunha ao lado: a forma SEM parêntese continua
    casando, senão o conserto poderia ter trocado um defeito por outro.
    """
    masked, cats = mask_text("(11) 98765-4321")
    assert masked == "*******4321"
    assert "(" not in masked
    assert cats == ["phone"]

    sem_paren, cats2 = mask_text("11 98765-4321")
    assert sem_paren == "*******4321"
    assert cats2 == ["phone"]


def test_paren_fix_changes_the_span_not_the_population():
    """Testemunha NEGATIVA do conserto do regex.

    O conserto amplia o TRECHO casado, nunca o CONJUNTO de telefones detectados —
    os dígitos já eram detectados antes. Um texto sem telefone continua sem categoria.
    """
    _, cats = mask_text("ligue para o (11) e peca o ramal 42")
    assert cats == []


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
