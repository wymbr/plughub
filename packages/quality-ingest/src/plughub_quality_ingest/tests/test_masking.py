"""Tests for the masking net-pass (mirror of DEFAULT_MASKING_RULES)."""
from plughub_quality_ingest.masking import mask_text


def test_masks_cpf_preserving_last_2():
    masked, cats = mask_text("meu cpf e 123.456.789-01 ok")
    assert "123.456.789-01" not in masked
    assert "01" in masked          # preserve_last_digits=2
    assert masked.endswith("ok")
    assert cats == ["cpf"]


def test_masks_email_preserving_domain():
    # MSK-05 (2026-09-25): a exibição é a máscara `email_domain` do `operator` — a
    # primeira letra, `***` e o domínio. Antes era o `ceil(len/4)` do `buildDisplay`.
    masked, cats = mask_text("escreva para joao.silva@example.com por favor")
    assert "joao.silva" not in masked
    assert "j***@example.com" in masked   # 1ª letra + domínio, um único @
    assert masked.count("@") == 1
    assert cats == ["email_addr"]


def test_display_is_the_operator_mask():
    """A porta Python tem de produzir o MESMO display que as outras.

    MSK-05 (2026-09-25): o display é a máscara do `operator` no catálogo (`last_2` no
    CPF, `last_4` no cartão), e não mais o `buildDisplay` (`*********00`) nem o
    `replacement`. Se alguém reintroduzir qualquer um dos dois, estas linhas ficam
    vermelhas; a comparação ENTRE portas é do `probe_masking_display_parity.sh`.
    """
    assert mask_text("123.456.789-00")[0] == "***00"
    assert mask_text("1234 5678 9012 3456")[0] == "***3456"
    assert mask_text("1234-5678-9012-3456")[0] == "***3456"


def test_display_that_still_matches_the_net_is_refused():
    """A exibição que AINDA casaria a rede não escondeu o que foi detectado: sai `***`.

    É a garantia de idempotência que antes dependia de escolher bem os `replacement`.
    Controle positivo ao lado: a máscara normal NÃO é trocada por `***`.
    """
    from plughub_quality_ingest.masking import _display, DEFAULT_MASKING_RULES
    assert _display("joao@exemplo.com", "email_addr", DEFAULT_MASKING_RULES) == "j***@exemplo.com"
    # categoria desconhecida → `full` → `***` (esconder, nunca revelar)
    assert _display("123.456.789-00", "nao_existe", DEFAULT_MASKING_RULES) == "***"


def test_phone_in_parens_consumes_the_opening_paren():
    """O `\\(?` do regex de telefone precisa poder casar.

    Com o `\\b` inicial ele era RAMO MORTO — `\\b` exige transição \\W→\\w, que nunca
    ocorre antes de `(`. O match começava no dígito e o parêntese sobrava colado à
    máscara (`((##) ****-4321`). Testemunha ao lado: a forma SEM parêntese continua
    casando, senão o conserto poderia ter trocado um defeito por outro.
    """
    masked, cats = mask_text("(11) 98765-4321")
    assert masked == "***4321"
    assert "(" not in masked
    assert cats == ["phone"]

    sem_paren, cats2 = mask_text("11 98765-4321")
    assert sem_paren == "***4321"
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


def test_copia_que_reexporia_o_dado_e_recusada(monkeypatch):
    """Se a cópia do `by_role` ganhar uma máscara que não esconde o que a regra detectou
    (`first_word` num e-mail devolve o e-mail), a exibição sai `***` — e a passada
    continua idempotente. Sem este caso a recusa nunca dispara com a cópia de hoje, e um
    `_display` sem ela passaria em todos os outros testes (mutação M11 da MSK-05)."""
    from plughub_quality_ingest import masking
    monkeypatch.setitem(masking._OPERATOR_MASK, "email_addr", "first_word")
    monkeypatch.setattr(masking, "_apply",
                        lambda raw, mask: f"{raw.split()[0]} ***" if mask == "first_word" else "***")
    masked, cats = mask_text("email joao@exemplo.com fim")
    assert masked == "email *** fim"
    assert mask_text(masked) == (masked, [])
