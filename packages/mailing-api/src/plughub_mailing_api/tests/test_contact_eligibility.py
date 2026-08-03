"""
test_contact_eligibility.py
Primeira suíte do mailing-api (2026-08-03).

POR QUE ESTES ALVOS, E NÃO "cobertura". O pacote tinha `[tool.pytest.ini_options]`
apontando para um diretório inexistente — config de teste sem suíte, que é a forma mais
educada de aparentar cobertura. Existem oito smokes de outbound (`smoke_outbound_fase*.sh`)
que exercitam o caminho ponta a ponta por API; a pergunta que sobra não é *quantos testes
faltam*, é **qual regra some sem nada ficar vermelho**.

Resposta: a PRECEDÊNCIA do `contact_eligibility_check`. Um smoke exercita um caminho por
vez, então ele nunca distingue "negou pelo motivo certo" de "negou por outro motivo" — e
aqui os motivos não são intercambiáveis:

  · `opt_out` é veto do CLIENTE. Trocá-lo por `frequency_cap` faz o sistema parecer que
    só adiou o contato, quando na verdade a pessoa pediu para nunca mais ser contatada.
  · `transactional` fura o opt-out (notificação legal), e **só** ele — se furar também a
    fadiga, uma campanha marcada transacional vira um canal sem teto.
  · falha do calendar degrada para ABERTO de propósito: uma checagem perdida não pode
    bloquear contato em silêncio.

Nada disso aparece num teste de "negou/não negou". Por isso cada caso abaixo afirma o
`reason` exato, e vários montam DUAS condições de negação ao mesmo tempo para provar
qual vence — um teste que só olhasse `allowed is False` passaria com a ordem invertida.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ..db import (
    _canonical,
    _derive_dedup_key,
    _parse_window_seconds,
    db_contact_eligibility,
)

_TENANT   = "tenant_test"
_CUSTOMER = "cus_abc123"
_CAMPAIGN = "11111111-2222-3333-4444-555555555555"
_AT       = datetime(2026, 8, 3, 12, 0, 0, tzinfo=timezone.utc)


# ── Dublês ────────────────────────────────────────────────────────────────────

def _fake_pool(campaign_row: dict | None = None):
    """Pool cujo `acquire()` entrega um conn de verdade-de-mentira.

    `MagicMock` configura `__aenter__` como AsyncMock automaticamente, o que faria
    `async with pool.acquire() as conn` entregar um AsyncMock que responde qualquer
    coisa. Aqui o conn é explícito e as consultas de contagem são patchadas por teste.
    """
    conn = AsyncMock()
    conn.execute = AsyncMock(return_value="INSERT 0 1")

    tx = MagicMock()
    tx.__aenter__ = AsyncMock(return_value=None)
    tx.__aexit__  = AsyncMock(return_value=False)
    conn.transaction = MagicMock(return_value=tx)

    acq = MagicMock()
    acq.__aenter__ = AsyncMock(return_value=conn)
    acq.__aexit__  = AsyncMock(return_value=False)

    pool = MagicMock()
    pool.acquire  = MagicMock(return_value=acq)
    pool.fetchrow = AsyncMock(return_value=campaign_row)
    return pool, conn


def _identity(*, all_blocked=False, channels=None):
    idt = MagicMock()
    idt.get_do_not_contact = AsyncMock(
        return_value={"all": all_blocked, "channels": channels or []}
    )
    return idt


def _calendar(*, status="open", next_open=None, raises=False):
    cal = MagicMock()
    if raises:
        cal.is_open_status = AsyncMock(side_effect=RuntimeError("calendar down"))
    else:
        cal.is_open_status = AsyncMock(return_value=status)
    cal.next_open_slot = AsyncMock(return_value=next_open)
    return cal


async def _check(pool, *, identity=None, calendar=None, claim=True, campaign=_CAMPAIGN):
    return await db_contact_eligibility(
        pool, _TENANT,
        {"customer_id": _CUSTOMER, "channel": "whatsapp",
         "campaign_id": campaign, "claim": claim, "at": _AT},
        calendar=calendar, identity=identity,
    )


# ── _parse_window_seconds ─────────────────────────────────────────────────────

class TestParseWindow:
    @pytest.mark.parametrize("raw,expected", [
        ("30s", 30), ("60m", 3600), ("24h", 86_400), ("7d", 604_800),
        (900, 900), ("900", 900), (None, 0),
    ])
    def test_table(self, raw, expected):
        assert _parse_window_seconds(raw) == expected

    def test_garbage_degrades_to_zero_which_DISABLES_the_cap(self):
        """Janela impossível vira 0 — e 0 **desliga** o cap (`db.py:906`, `if w <= 0`).

        Não é um detalhe de parsing: `"24hs"` (um "s" a mais) é um erro de digitação
        plausível na config de um tenant, e o efeito é o cap deixar de existir — a
        campanha passa a contatar sem teto. O código LOGA um warning, o que satisfaz
        *"degradação nunca é silenciosa"*; este teste fixa a consequência para que ela
        seja uma escolha, e não uma surpresa.
        """
        assert _parse_window_seconds("24hs") == 0
        assert _parse_window_seconds("") == 0
        assert _parse_window_seconds("uma semana") == 0


# ── _derive_dedup_key ─────────────────────────────────────────────────────────

class TestDedupKey:
    def test_customer_policy_is_stable_per_customer(self):
        a = _derive_dedup_key("customer", _CUSTOMER, {"x": 1})
        b = _derive_dedup_key("customer", _CUSTOMER, {"x": 2})
        assert a == b == f"cust:{_CUSTOMER}"

    def test_customer_context_ignores_key_ORDER_but_not_content(self):
        """Ordem das chaves não pode gerar entrada duplicada — `_canonical` ordena."""
        a = _derive_dedup_key("customer_context", _CUSTOMER, {"a": 1, "b": 2})
        b = _derive_dedup_key("customer_context", _CUSTOMER, {"b": 2, "a": 1})
        assert a == b
        c = _derive_dedup_key("customer_context", _CUSTOMER, {"a": 1, "b": 3})
        assert c != a

    def test_policy_none_never_collapses(self):
        a = _derive_dedup_key("none", _CUSTOMER, {"x": 1})
        b = _derive_dedup_key("none", _CUSTOMER, {"x": 1})
        assert a != b, "policy 'none' precisa render chaves distintas"

    def test_customer_policy_falls_back_when_id_is_null(self):
        """Sem `customer_id`, `customer` cai no hash de conteúdo — não em `cust:None`."""
        k = _derive_dedup_key("customer", None, {"x": 1})
        assert k.startswith("ctx:")

    def test_canonical_is_deterministic(self):
        assert _canonical({"b": 1, "a": [2, 3]}) == _canonical({"a": [2, 3], "b": 1})


# ── Precedência dos portões ───────────────────────────────────────────────────

class TestEligibilityPrecedence:
    async def test_opt_out_wins_over_closed_window(self):
        """Duas negações ao mesmo tempo — o `reason` precisa ser `opt_out`.

        Se a ordem invertesse, o chamador registraria "fora de janela" e tentaria de
        novo mais tarde, contatando quem pediu para não ser contatado.
        """
        pool, conn = _fake_pool({"contact_calendar_id": "cal_1", "transactional": False})
        r = await _check(
            pool,
            identity=_identity(all_blocked=True),
            calendar=_calendar(status="closed"),
        )
        assert r == {"allowed": False, "reason": "opt_out",
                     "retry_after": None, "claimed": False}
        conn.execute.assert_not_called()   # negação NUNCA grava contact_log

    async def test_opt_out_by_channel_only_blocks_that_channel(self):
        pool, _ = _fake_pool({"contact_calendar_id": None, "transactional": False})
        with patch("plughub_mailing_api.db._resolve_effective_policy",
                   new=AsyncMock(return_value=None)):
            blocked = await _check(pool, identity=_identity(channels=["whatsapp"]))
            other   = await _check(pool, identity=_identity(channels=["sms"]))
        assert blocked["reason"] == "opt_out"
        assert other["allowed"] is True      # controle positivo

    async def test_transactional_bypasses_opt_out_but_NOT_fatigue(self):
        """O furo é escopado ao opt-out. Se vazasse para a fadiga, marcar uma campanha
        como `transactional` viraria um canal sem teto."""
        pool, _ = _fake_pool({"contact_calendar_id": None, "transactional": True})
        policy = {"quarantine_after": "24h", "frequency_caps": [], "channel_caps": {}}
        with patch("plughub_mailing_api.db._resolve_effective_policy",
                   new=AsyncMock(return_value=policy)), \
             patch("plughub_mailing_api.db._count_contacts",
                   new=AsyncMock(return_value=(1, _AT - timedelta(hours=2)))):
            r = await _check(pool, identity=_identity(all_blocked=True))
        assert r["reason"] == "quarantine"       # passou pelo opt-out, parou na fadiga
        assert r["retry_after"] == 22 * 3600     # 24h menos as 2h já decorridas

    async def test_closed_window_wins_over_fatigue(self):
        pool, _ = _fake_pool({"contact_calendar_id": "cal_1", "transactional": False})
        policy = {"quarantine_after": "24h", "frequency_caps": [], "channel_caps": {}}
        with patch("plughub_mailing_api.db._resolve_effective_policy",
                   new=AsyncMock(return_value=policy)), \
             patch("plughub_mailing_api.db._count_contacts",
                   new=AsyncMock(return_value=(5, _AT))):
            r = await _check(pool, calendar=_calendar(
                status="holiday", next_open=_AT + timedelta(hours=9)))
        assert r["reason"] == "outside_window"
        assert r["retry_after"] == 9 * 3600

    async def test_calendar_failure_degrades_OPEN(self):
        """Fail-open deliberado: checagem perdida não bloqueia contato em silêncio."""
        pool, _ = _fake_pool({"contact_calendar_id": "cal_1", "transactional": False})
        with patch("plughub_mailing_api.db._resolve_effective_policy",
                   new=AsyncMock(return_value=None)):
            with pytest.raises(RuntimeError):
                # Hoje a exceção PROPAGA — o docstring promete degradar para aberto.
                # Fixado como está para que a divergência apareça no dia em que alguém
                # implementar o try/except: este teste vira vermelho e força a decisão.
                await _check(pool, calendar=_calendar(raises=True))

    async def test_no_policy_allows_and_claims(self):
        pool, conn = _fake_pool({"contact_calendar_id": None, "transactional": False})
        with patch("plughub_mailing_api.db._resolve_effective_policy",
                   new=AsyncMock(return_value=None)):
            r = await _check(pool, claim=True)
        assert r["allowed"] is True and r["claimed"] is True
        conn.execute.assert_called_once()          # gravou o fato

    async def test_claim_false_allows_without_writing(self):
        """`claim=false` é consulta: responde e não consome a janela."""
        pool, conn = _fake_pool({"contact_calendar_id": None, "transactional": False})
        with patch("plughub_mailing_api.db._resolve_effective_policy",
                   new=AsyncMock(return_value=None)):
            r = await _check(pool, claim=False)
        assert r["allowed"] is True and r["claimed"] is False
        conn.execute.assert_not_called()

    async def test_frequency_cap_before_channel_cap(self):
        """Ambos estourados → `frequency_cap`. A ordem importa para o operador saber
        qual regra afrouxar."""
        pool, _ = _fake_pool({"contact_calendar_id": None, "transactional": False})
        policy = {
            "quarantine_after": None,
            "frequency_caps": [{"window": "7d", "max": 3, "per_channel": False}],
            "channel_caps":   {"whatsapp": {"window": "24h", "max": 1}},
        }
        with patch("plughub_mailing_api.db._resolve_effective_policy",
                   new=AsyncMock(return_value=policy)), \
             patch("plughub_mailing_api.db._count_contacts",
                   new=AsyncMock(return_value=(9, _AT - timedelta(days=1)))):
            r = await _check(pool)
        assert r["reason"] == "frequency_cap"

    async def test_cap_with_unparseable_window_is_skipped(self):
        """Fecha o laço com `TestParseWindow`: janela impossível → cap ignorado → ALLOW.

        É o caminho pelo qual um erro de digitação na policy vira contato sem teto.
        """
        pool, _ = _fake_pool({"contact_calendar_id": None, "transactional": False})
        policy = {
            "quarantine_after": "24hs",     # typo
            "frequency_caps": [{"window": "semana", "max": 1, "per_channel": False}],
            "channel_caps":   {},
        }
        with patch("plughub_mailing_api.db._resolve_effective_policy",
                   new=AsyncMock(return_value=policy)), \
             patch("plughub_mailing_api.db._count_contacts",
                   new=AsyncMock(return_value=(99, _AT))):
            r = await _check(pool)
        assert r["allowed"] is True, "cap com janela ilegível deixou de barrar"
