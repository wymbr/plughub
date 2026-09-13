"""
identity/normalize.py — normalização e hashing de âncoras de identidade.

Regra de ouro (LGPD): o índice de resolução NUNCA guarda PII em claro. As âncoras
(telefone/e-mail/cpf/…) são normalizadas por tipo e hasheadas com salt por tenant
antes de virar chave Redis. O salt é SEGREDO → vem de env (PLUGHUB_IDENTITY_SALT),
nunca de config-api.
"""
from __future__ import annotations

import hashlib
import re

import phonenumbers

# Tipos de âncora suportados (Fase A). 'dev' fica para a fase D (device id).
ANCHOR_KINDS = ("phone", "email", "cpf", "princ", "dev")

# Âncoras ENTREGÁVEIS — existe canal para mandar um código a elas. Só estas admitem
# OTP (PID-10, ADR adr-identity-door-evidence D8). `cpf`, `princ` e `dev` identificam
# e nunca recebem código: "provar posse" de um CPF é provar que se sabe um número.
DELIVERABLE_KINDS = ("phone", "email")

# Peso de confiança por tipo — usado na desambiguação do Lookup 1.
# Ordem de autoridade (spec §9/§13.7): princ/ext > cpf/email > phone > dev.
KIND_CONFIDENCE: dict[str, float] = {
    "princ": 0.95,
    "cpf":   0.90,
    "email": 0.80,
    "phone": 0.70,
    "dev":   0.30,
}

_NON_DIGITS = re.compile(r"\D+")


# Telefone só tem dígitos e separadores. Letra é recusada ANTES do parser: o
# `phonenumbers` converte letra em dígito (número "vanity"), e o `contact_identifier`
# do webchat (`cli_52989317358`) virava âncora phone só com os dígitos (IDN-14).
_PHONE_CHARS = re.compile(r"^\+?[\d\s().\-]+$")


def _e164_phone(v: str, region: str | None) -> str:
    """E.164 do telefone (IDN-14, 2026-09-13).

    Até aqui era `"+" + dígitos`, que assume DDI presente: `11 99999-0001` virava
    `+11999990001` e não casava com `+55 11 99999-0001` — o mesmo celular, dois
    hashes. Regras:

      - com `+`: internacional, a região não entra;
      - sem `+`: nacional do país do tenant (`region`); o parser também reconhece o
        DDI do próprio país digitado sem `+` (`5511…` com `BR`);
      - sem `+` e sem região: RECUSA. Adivinhar o país é gravar o hash errado.

    ⚠️ Aceita-se por TAMANHO POSSÍVEL (`is_possible_number`), nunca por validade
    (`is_valid_number`): a validade depende das faixas de numeração, que mudam entre
    versões dos metadados — e o que decide o hash não pode mudar com um upgrade. O
    `phonenumbers` é fixado em versão exata no `pyproject.toml` pelo mesmo motivo.
    """
    if not _PHONE_CHARS.match(v):
        raise ValueError("phone has non-phone characters")
    digits = _NON_DIGITS.sub("", v)
    if not digits:
        raise ValueError("phone has no digits")
    if v.startswith("+"):
        candidatos = [("+" + digits, None)]
    elif region:
        if region not in phonenumbers.SUPPORTED_REGIONS:
            raise ValueError("unknown phone region: %s" % region)
        candidatos = [(digits, region), ("+" + digits, None)]
    else:
        raise ValueError("phone without country code and no default region for the tenant")
    for texto, reg in candidatos:
        try:
            n = phonenumbers.parse(texto, reg)
        except phonenumbers.NumberParseException:
            continue
        if phonenumbers.is_possible_number(n):
            return phonenumbers.format_number(n, phonenumbers.PhoneNumberFormat.E164)
    raise ValueError("phone is not a possible number")


def normalize_anchor(kind: str, value: str, region: str | None) -> str:
    """
    Normaliza o valor de uma âncora conforme o tipo, de forma determinística.

      phone → E.164; sem código do país usa `region` (o país padrão do tenant)
      email → trim + lowercase
      cpf   → só dígitos
      princ → trim (o `sub` do JWT do tenant, já opaco)
      dev   → trim

    `region` é OBRIGATÓRIO na assinatura (pode ser None): esquecê-lo num call site
    tem de quebrar alto, porque o modo de falha contrário é o mesmo cliente com dois
    hashes. Levanta ValueError para kind desconhecido ou valor inválido.
    """
    if kind not in ANCHOR_KINDS:
        raise ValueError(f"unknown anchor kind: {kind}")
    v = (value or "").strip()
    if not v:
        raise ValueError("empty anchor value")

    if kind == "phone":
        return _e164_phone(v, region)
    if kind == "email":
        return v.lower()
    if kind == "cpf":
        digits = _NON_DIGITS.sub("", v)
        if not digits:
            raise ValueError("cpf has no digits")
        return digits
    # princ / dev — opacos, só trim (já feito acima)
    return v


def hash_anchor(salt: str, kind: str, value: str, region: str | None) -> str:
    """
    value_hash = hex(sha256(salt + normalizado)). O salt (segredo, por tenant)
    garante que o índice não seja um dicionário reverso de PII. `region` só vale
    para telefone; quem tem tenant em mãos usa `region.anchor_hash`.
    """
    normalized = normalize_anchor(kind, value, region)
    digest = hashlib.sha256((salt + normalized).encode("utf-8")).hexdigest()
    return digest


def kind_confidence(kind: str) -> float:
    """Peso de confiança do tipo de âncora (0..1). Desconhecido → 0.5."""
    return KIND_CONFIDENCE.get(kind, 0.5)


# ── Verification class — posse de canal (OTP) vs alegação (digitada) ────────────
# claimed  = cliente afirmou/digitou (não verificada) — grau de "origem/fraca".
# possessed = provada por OTP (posse do canal) — confiável para retomada sensível.
VERIFICATION_CLASSES = ("claimed", "possessed")

# Bônus de ranking para âncora verificada. Alto o suficiente para que QUALQUER
# âncora `possessed` supere QUALQUER âncora `claimed` na desambiguação do Lookup 1
# (possessed phone 0.70+1.0=1.70 > claimed cpf 0.90). Só afeta ordenação; a
# CustomerRef.confidence exposta continua sendo o kind_confidence (0..1).
POSSESSED_RANK_BONUS = 1.0


def effective_verification_class(kind: str, verification_class: str) -> str:
    """A classe que VALE para esta âncora: `possessed` só existe em kind entregável.

    ⚠️ IDN-13 (2026-09-13): posse de canal só se prova recebendo um código, e só
    âncora entregável recebe (D8). Até a PID-10 o OTP desafiava CPF e gravava
    `possessed` nele — prova de que se sabia o número digitado —, e o portão de
    retomada lê essa classe: digitar o CPF abria as pendências. A PID-10 fechou o
    produtor; esta função fecha a LEITURA do que ficou gravado (Redis quente, cadastro
    ainda não migrado, instalação que não bootou), e é a mesma regra que a escrita e a
    migração aplicam. Uma regra, lida em todo ponto que lê a classe guardada.
    """
    if verification_class == "possessed" and kind not in DELIVERABLE_KINDS:
        return "claimed"
    return verification_class


def anchor_rank_score(kind: str, verification_class: str) -> float:
    """Score interno de desambiguação = kind_confidence + bônus se possessed."""
    base = kind_confidence(kind)
    return base + (POSSESSED_RANK_BONUS if verification_class == "possessed" else 0.0)
