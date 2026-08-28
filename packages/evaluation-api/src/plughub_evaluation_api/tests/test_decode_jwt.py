"""
test_decode_jwt.py — a porta de AUTENTICAÇÃO da evaluation-api.

POR QUE ESTE ARQUIVO NASCEU (duas mutações sobreviveram)
========================================================
No passo 6 da consolidação, `_decode_jwt` e `_decode_jwt_optional` trocaram o
`pyjwt.decode` local pelo `plughub_authz.verify_user_jwt`. A bateria de mutação plantou
duas reversões no caminho de recusa —

    payload = verify_user_jwt(...) or {"sub": "anon"}     # token inválido ACEITO
    if False: ... "token missing 'sub' claim"             # token sem sujeito ACEITO

— e **220 de 220 continuaram verdes**. Ou seja: a função que decide *"quem é você"*
neste serviço não tinha teste nenhum, e trocar a verificação por nada não ficaria
vermelho. É a terceira vez no mesmo arco: os testes cercam o VEREDICTO de autorização e
deixam a AUTENTICAÇÃO descoberta.

OS DOIS IRMÃOS NÃO SÃO O MESMO GATE
===================================
  · `_decode_jwt`          — Bearer OBRIGATÓRIO. Ausente, inválido ou sem `sub` → 401,
                             cada um com motivo próprio (quem depura precisa distinguir
                             "não mandou" de "mandou torto").
  · `_decode_jwt_optional` — Bearer OPCIONAL. Ausente **e** inválido devolvem `None`,
                             indistinguíveis por decisão: é a postura anônima desta API,
                             que é eixo de DEMO, não de autorização.

Colapsar os dois seria fechar o anônimo (quebra a postura) ou abrir o obrigatório
(quebra a autenticação) — por isso cada um tem os seus casos aqui.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import jwt as pyjwt
import pytest
from fastapi import HTTPException

from plughub_evaluation_api.config import settings
from plughub_evaluation_api.router import _decode_jwt, _decode_jwt_optional


class _Req:
    def __init__(self, authorization: str | None = None):
        self.headers = {"authorization": authorization} if authorization else {}


def _token(secret: str | None = None, **claims) -> str:
    base = {"sub": "u1", "exp": datetime.now(timezone.utc) + timedelta(hours=1)}
    base.update(claims)
    return pyjwt.encode(base, secret or settings.jwt_secret, algorithm="HS256")


# ── _decode_jwt — Bearer obrigatório ─────────────────────────────────────────

def test_token_valido_devolve_os_claims():
    """Testemunha de presença: sem ela, um `raise` incondicional passaria nos demais."""
    p = _decode_jwt(_Req(f"Bearer {_token(email='a@b.c')}"))
    assert p["sub"] == "u1"
    assert p["email"] == "a@b.c"


@pytest.mark.parametrize("header", [None, "", "Basic abc", "Bearer", "Bearer    "])
def test_sem_bearer_e_401_nomeado(header):
    with pytest.raises(HTTPException) as e:
        _decode_jwt(_Req(header))
    assert e.value.status_code == 401
    assert "missing Bearer" in e.value.detail


def test_assinatura_de_OUTRO_segredo_e_401():
    """⚠️ Mutação D5: `verify_user_jwt(...) or {"sub": "anon"}` passava por 220 verdes."""
    with pytest.raises(HTTPException) as e:
        _decode_jwt(_Req(f"Bearer {_token(secret='segredo-alheio')}"))
    assert e.value.status_code == 401


def test_token_expirado_e_401():
    expirado = pyjwt.encode(
        {"sub": "u1", "exp": 1_000_000_000}, settings.jwt_secret, algorithm="HS256"
    )
    with pytest.raises(HTTPException) as e:
        _decode_jwt(_Req(f"Bearer {expirado}"))
    assert e.value.status_code == 401


def test_lixo_no_lugar_do_token_e_401():
    with pytest.raises(HTTPException) as e:
        _decode_jwt(_Req("Bearer nao.e.um.jwt"))
    assert e.value.status_code == 401


def test_token_SEM_sub_e_401_com_motivo_proprio():
    """⚠️ Mutação D6. Identidade sem sujeito não serve: `_compute_available_actions`
    compara `jwt.sub` com `evaluated_user_id` para decidir POSSE, e um `sub` ausente
    tornaria a comparação sempre falsa — o avaliado deixaria de poder contestar, em
    silêncio."""
    sem_sub = pyjwt.encode(
        {"email": "a@b.c", "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
        settings.jwt_secret, algorithm="HS256",
    )
    with pytest.raises(HTTPException) as e:
        _decode_jwt(_Req(f"Bearer {sem_sub}"))
    assert e.value.status_code == 401
    assert "sub" in e.value.detail


def test_o_401_nao_ecoa_o_texto_da_excecao():
    """Mudança declarada no passo 6: o detalhe dizia `invalid token: {exc}`.

    O motivo continua existindo — o canônico o LOGA (`authz: JWT inválido: …`) —, do
    lado de quem opera, não do lado de quem apresentou o token.
    """
    with pytest.raises(HTTPException) as e:
        _decode_jwt(_Req("Bearer nao.e.um.jwt"))
    assert "Signature" not in e.value.detail and "Segment" not in e.value.detail


# ── _decode_jwt_optional — Bearer opcional (postura anônima) ─────────────────

def test_opcional_sem_header_e_None_sem_erro():
    assert _decode_jwt_optional(_Req()) is None


def test_opcional_com_token_valido_devolve_claims():
    assert _decode_jwt_optional(_Req(f"Bearer {_token()}"))["sub"] == "u1"


@pytest.mark.parametrize("header", ["Bearer nao.e.um.jwt", "Basic abc", "Bearer "])
def test_opcional_token_torto_e_None_NUNCA_claims(header):
    """Indistinguível de ausente é a DECISÃO; devolver claims de um token não
    verificado seria outra coisa inteiramente."""
    assert _decode_jwt_optional(_Req(header)) is None


def test_opcional_recusa_assinatura_alheia():
    """O eixo anônimo não é uma porta de entrada: token de outro segredo não vira
    identidade, vira `None` (que os call sites tratam como anônimo)."""
    assert _decode_jwt_optional(_Req(f"Bearer {_token(secret='segredo-alheio')}")) is None
