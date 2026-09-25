"""MSK-05 — `detected_display`, gêmeo de `detectedDisplay` (@plughub/schemas).

Asserções em pares: "segue o catálogo" só significa algo se trocar o catálogo troca a
saída. A paridade com o TS sobre o catálogo VIVO é do
`infra/test/probe_masking_display_parity.sh`; aqui fica o comportamento de cada ramo.
"""
import re

from plughub_contextstore.masking import detected_display

REDE = [
    re.compile(r"\b(?:\d{3}\.\d{3}\.\d{3}-\d{2}|\d{11})\b"),
    re.compile(r"\b(?:\d{4}[\s-]?){3}\d{4}\b"),
    re.compile(r"(?<!\w)(?:\+55\s?)?(?:\(?\d{2}\)?[\s-]?)?9?\d{4}[-\s]?\d{4}\b"),
    re.compile(r"\b[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}\b"),
]


def _cat(**por_id: str) -> dict:
    return {i: {"id": i, "mascara": {"by_role": {"operator": m}}} for i, m in por_id.items()}


SEMEADO = _cat(cpf="last_2", credit_card="last_4", phone="last_4", email_addr="email_domain")


def test_catalogo_semeado_da_a_mascara_do_operator():
    assert detected_display("529.982.247-25", "cpf", SEMEADO, REDE) == "***25"
    assert detected_display("4539 1488 0343 6467", "credit_card", SEMEADO, REDE) == "***6467"
    assert detected_display("(11) 98765-4321", "phone", SEMEADO, REDE) == "***4321"
    assert detected_display("joao.silva@exemplo.com", "email_addr", SEMEADO, REDE) == "j***@exemplo.com"


def test_trocar_o_by_role_troca_a_exibicao():
    assert detected_display("529.982.247-25", "cpf", _cat(cpf="last_4"), REDE) == "***4725"
    assert detected_display("529.982.247-25", "cpf", _cat(cpf="full"), REDE) == "***"


def test_plain_declarado_mostra_o_valor():
    assert detected_display("529.982.247-25", "cpf", _cat(cpf="plain"), REDE) == "529.982.247-25"


def test_hidden_nao_apaga_pedaco_de_frase():
    assert detected_display("529.982.247-25", "cpf", _cat(cpf="hidden"), REDE) == "***"


def test_exibicao_que_casaria_a_rede_e_recusada():
    assert detected_display("joao@exemplo.com", "email_addr", _cat(email_addr="first_word"), REDE) == "***"
    # controle: sem a rede, a mesma máscara passaria — é a rede que recusa
    assert detected_display("joao@exemplo.com", "email_addr", _cat(email_addr="first_word")) == "joao@exemplo.com ***"


def test_catalogo_indisponivel_esconde():
    # `{}` é o que `get_masking_catalog` devolve quando não carrega
    assert detected_display("529.982.247-25", "cpf", {}, REDE) == "***"
