"""
test_recurrence.py
Primeira suíte do scheduler-api (2026-08-03).

POR QUE A RECORRÊNCIA, E POR QUE EM SÉRIE. O `smoke_scheduled_promote.sh` prova que uma
agenda dispara. Mas `compute_next_fire` calcula **só a próxima** ocorrência e é rearmada a
cada disparo (desenho de estado limitado) — logo o comportamento que importa não é *um*
disparo, é a **série**. Um `interval` errado, um rearme que devolve o mesmo instante, ou um
horário que escorrega no horário de verão só aparecem na 2ª, 3ª, 4ª ocorrência. Nenhum
smoke de disparo único distingue isso de "funcionou".

Os testes abaixo iteram a função realimentando o resultado anterior como `after_dt` — que é
exatamente o que o dispatcher faz. Três invariantes ganham teste próprio porque o modo de
falha de cada um é caro e mudo:

  · **estritamente depois** — se o rearme devolvesse o mesmo instante, a agenda dispararia
    em laço; se devolvesse um instante já passado, o poller reprocessaria sem parar.
  · **o horário é LOCAL, o instante é UTC** — na virada do horário de verão, um relógio de
    parede às 09:00 tem de continuar às 09:00 para o cliente; o que muda é o UTC.
  · **checagem de calendário que falha NÃO cancela disparo** — o docstring do módulo diz
    "degradation is loud: a missed check must not silently drop a fire". Perder contato
    ativo por indisponibilidade de um serviço auxiliar é pior do que disparar em dia
    fechado.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from ..evaluator import compute_next_fire

_UTC = timezone.utc


def _agenda(**over) -> dict:
    base = {
        "timezone": "UTC",
        "validity": {"starts_at": "2026-08-03T00:00:00+00:00"},
        "schedule": {"mode": "recurring", "rule": {
            "frequency": "daily", "interval": 1, "times": ["09:00"],
        }},
    }
    sched = over.pop("schedule", None)
    if sched:
        base["schedule"] = sched
    if "rule" in over:
        base["schedule"]["rule"] = over.pop("rule")
    base.update(over)
    return base


async def _series(agenda: dict, start: datetime, n: int, client=None) -> list[datetime]:
    """Itera o rearme como o dispatcher faz: cada resultado vira o `after_dt` seguinte."""
    out: list[datetime] = []
    cur = start
    for _ in range(n):
        nxt = await compute_next_fire(agenda, cur, calendar_client=client)
        if nxt is None:
            break
        out.append(nxt)
        cur = nxt
    return out


def _cal(status="open"):
    c = MagicMock()
    c.is_open_status = AsyncMock(return_value=status)
    return c


# ── Séries ────────────────────────────────────────────────────────────────────

class TestSeries:
    async def test_daily_every_day(self):
        s = await _series(_agenda(), datetime(2026, 8, 3, 0, 0, tzinfo=_UTC), 3)
        assert s == [
            datetime(2026, 8, 3, 9, 0, tzinfo=_UTC),
            datetime(2026, 8, 4, 9, 0, tzinfo=_UTC),
            datetime(2026, 8, 5, 9, 0, tzinfo=_UTC),
        ]

    async def test_daily_interval_3_skips_two_days(self):
        """O `interval` é o que um disparo único NUNCA revela."""
        a = _agenda(rule={"frequency": "daily", "interval": 3, "times": ["09:00"]})
        s = await _series(a, datetime(2026, 8, 3, 0, 0, tzinfo=_UTC), 3)
        assert [d.day for d in s] == [3, 6, 9]

    async def test_two_times_per_day_are_both_fired_in_order(self):
        a = _agenda(rule={"frequency": "daily", "interval": 1, "times": ["18:00", "09:00"]})
        s = await _series(a, datetime(2026, 8, 3, 0, 0, tzinfo=_UTC), 4)
        assert [(d.day, d.hour) for d in s] == [(3, 9), (3, 18), (4, 9), (4, 18)]

    async def test_weekly_biweekly_on_two_weekdays(self):
        a = _agenda(rule={
            "frequency": "weekly", "interval": 2, "times": ["09:00"],
            "weekdays": ["monday", "thursday"],
        })
        # 2026-08-03 é segunda-feira → âncora na semana dela.
        s = await _series(a, datetime(2026, 8, 3, 0, 0, tzinfo=_UTC), 4)
        assert [(d.month, d.day) for d in s] == [(8, 3), (8, 6), (8, 17), (8, 20)]

    async def test_monthly_by_date_clamps_on_short_month(self):
        """Dia 31 num mês de 30 dias: `clamp` cai no último dia, não pula o mês."""
        a = _agenda(
            validity={"starts_at": "2026-01-31T00:00:00+00:00"},
            rule={"frequency": "monthly", "interval": 1, "times": ["09:00"],
                  "month_by": {"kind": "by_date", "days": ["31"]},
                  "month_overflow": "clamp"},
        )
        s = await _series(a, datetime(2026, 1, 1, 0, 0, tzinfo=_UTC), 4)
        assert [(d.month, d.day) for d in s] == [(1, 31), (2, 28), (3, 31), (4, 30)]

    async def test_monthly_by_position_last_friday(self):
        a = _agenda(
            validity={"starts_at": "2026-08-01T00:00:00+00:00"},
            rule={"frequency": "monthly", "interval": 1, "times": ["09:00"],
                  "month_by": {"kind": "by_position", "weekday": "friday", "nth": "last"}},
        )
        s = await _series(a, datetime(2026, 8, 1, 0, 0, tzinfo=_UTC), 3)
        assert [(d.month, d.day) for d in s] == [(8, 28), (9, 25), (10, 30)]


# ── Invariantes do rearme ─────────────────────────────────────────────────────

class TestRearmInvariants:
    async def test_next_is_STRICTLY_after(self):
        """Rearmar no próprio instante do disparo não pode devolver o mesmo instante.

        Se devolvesse, o dispatcher gravaria o mesmo `AgendaDispatch` em laço — e o
        sintoma seria volume, não erro.
        """
        a = _agenda()
        first = await compute_next_fire(a, datetime(2026, 8, 3, 0, 0, tzinfo=_UTC))
        again = await compute_next_fire(a, first)
        assert again > first
        assert again == first + timedelta(days=1)

    async def test_once_never_repeats(self):
        a = {"timezone": "UTC",
             "schedule": {"mode": "once", "fire_at": "2026-08-03T09:00:00+00:00"}}
        first = await compute_next_fire(a, datetime(2026, 8, 1, tzinfo=_UTC))
        assert first == datetime(2026, 8, 3, 9, 0, tzinfo=_UTC)
        assert await compute_next_fire(a, first) is None      # exausta → completed

    async def test_ends_at_exhausts_the_series(self):
        a = _agenda(validity={"starts_at": "2026-08-03T00:00:00+00:00",
                              "ends_at":   "2026-08-05T12:00:00+00:00"})
        s = await _series(a, datetime(2026, 8, 3, 0, 0, tzinfo=_UTC), 10)
        assert [d.day for d in s] == [3, 4, 5]

    async def test_rule_that_never_matches_returns_none_not_hang(self):
        """Guarda do `_MAX_LOOKAHEAD_DAYS`: regra impossível responde None."""
        a = _agenda(rule={"frequency": "weekly", "interval": 1,
                          "times": ["09:00"], "weekdays": []})
        assert await compute_next_fire(a, datetime(2026, 8, 3, tzinfo=_UTC)) is None


# ── Fuso e horário de verão ───────────────────────────────────────────────────

class TestTimezone:
    async def test_local_time_is_preserved_across_dst(self):
        """09:00 local continua 09:00 local na virada — o que muda é o UTC.

        Nova York entra no horário de verão em 08/03/2026 (2º domingo de março). Se o
        cálculo somasse 24 h no INSTANTE em vez de recalcular o horário local, o disparo
        migraria para as 08:00 do cliente e ninguém veria um erro — só um horário
        estranho.
        """
        a = _agenda(timezone="America/New_York",
                    validity={"starts_at": "2026-03-06T00:00:00+00:00"})
        s = await _series(a, datetime(2026, 3, 6, 0, 0, tzinfo=_UTC), 4)

        from zoneinfo import ZoneInfo
        local = [d.astimezone(ZoneInfo("America/New_York")) for d in s]
        assert {(t.hour, t.minute) for t in local} == {(9, 0)}
        # E o UTC de fato mudou de 14:00 para 13:00 na virada.
        assert {d.hour for d in s} == {14, 13}

    async def test_unknown_timezone_falls_back_to_utc_loudly(self):
        a = _agenda(timezone="Mars/Olympus")
        nxt = await compute_next_fire(a, datetime(2026, 8, 3, 0, 0, tzinfo=_UTC))
        assert nxt == datetime(2026, 8, 3, 9, 0, tzinfo=_UTC)


# ── business_day_policy ───────────────────────────────────────────────────────

class TestBusinessDayPolicy:
    async def test_ignore_NEVER_consults_the_calendar(self):
        """A spec diz "wall-clock, no calendar consulted". Consultar por engano faria o
        scheduler depender de um serviço que ele declarou não precisar."""
        cal = _cal("closed")
        a = _agenda(calendar_id="cal_1",
                    rule={"frequency": "daily", "interval": 1, "times": ["09:00"],
                          "business_day_policy": "ignore"})
        nxt = await compute_next_fire(a, datetime(2026, 8, 3, tzinfo=_UTC), calendar_client=cal)
        assert nxt == datetime(2026, 8, 3, 9, 0, tzinfo=_UTC)
        cal.is_open_status.assert_not_called()

    async def test_only_business_days_skips_closed_days(self):
        cal = MagicMock()
        # fechado nos dias 3 e 4, aberto do 5 em diante
        async def _st(_cid, dt):
            return "closed" if dt.day in (3, 4) else "open"
        cal.is_open_status = AsyncMock(side_effect=_st)

        a = _agenda(calendar_id="cal_1",
                    rule={"frequency": "daily", "interval": 1, "times": ["09:00"],
                          "business_day_policy": "only_business_days"})
        nxt = await compute_next_fire(a, datetime(2026, 8, 3, tzinfo=_UTC), calendar_client=cal)
        assert nxt.day == 5

    async def test_shift_next_moves_whole_days_keeping_time_of_day(self):
        cal = MagicMock()
        async def _st(_cid, dt):
            return "closed" if dt.day == 3 else "open"
        cal.is_open_status = AsyncMock(side_effect=_st)

        a = _agenda(calendar_id="cal_1",
                    rule={"frequency": "daily", "interval": 1, "times": ["09:00"],
                          "business_day_policy": "shift_next"})
        nxt = await compute_next_fire(a, datetime(2026, 8, 3, tzinfo=_UTC), calendar_client=cal)
        assert (nxt.day, nxt.hour, nxt.minute) == (4, 9, 0)   # dia move, hora não

    async def test_calendar_failure_still_fires(self):
        """Degradação barulhenta: `is_open_status` devolvendo None = "não sei" → dispara.

        É a decisão certa e a fácil de reverter por engano — quem lê `None` como
        "fechado" transforma indisponibilidade do calendar em campanha que não sai, sem
        nenhum erro registrado.
        """
        cal = _cal(None)
        a = _agenda(calendar_id="cal_1",
                    rule={"frequency": "daily", "interval": 1, "times": ["09:00"],
                          "business_day_policy": "only_business_days"})
        nxt = await compute_next_fire(a, datetime(2026, 8, 3, tzinfo=_UTC), calendar_client=cal)
        assert nxt == datetime(2026, 8, 3, 9, 0, tzinfo=_UTC)
        cal.is_open_status.assert_called()      # consultou, e seguiu mesmo sem resposta

    async def test_no_calendar_id_skips_the_check(self):
        cal = _cal("closed")
        a = _agenda(rule={"frequency": "daily", "interval": 1, "times": ["09:00"],
                          "business_day_policy": "only_business_days"})
        nxt = await compute_next_fire(a, datetime(2026, 8, 3, tzinfo=_UTC), calendar_client=cal)
        assert nxt == datetime(2026, 8, 3, 9, 0, tzinfo=_UTC)
        cal.is_open_status.assert_not_called()
