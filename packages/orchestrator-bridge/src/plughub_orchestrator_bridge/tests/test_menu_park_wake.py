"""
DUR-01 F2 — o bridge ACORDA a conversa estacionada e fecha o segmento UMA vez.

`wake_parked_run` é o único caminho de volta de uma conversa estacionada: se ele deixar
de chamar o executor, a conversa fica parada para sempre; se fechar o segmento duas
vezes, a instância é devolvida duas vezes (contador de ocupação negativo); se tratar um
412 como fim, fecha um atendimento vivo. Cada caso abaixo diz qual desses ele pega.

O Redis é um dicionário (sem `fakeredis` na imagem) com só os comandos usados; o
executor e o fechamento são substituídos, e o teste confere COMO foram chamados.
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

from plughub_orchestrator_bridge import main as bridge_mod

SID, INST = "sess-park", "sac_ia-001"


class FakeRedis:
    def __init__(self) -> None:
        self.kv: dict[str, str] = {}
        self.sets: dict[str, set[str]] = {}

    async def get(self, k):
        return self.kv.get(k)

    async def set(self, k, v, ex=None, nx=False):
        if nx and k in self.kv:
            return None
        self.kv[k] = v
        return True

    async def delete(self, *ks):
        n = 0
        for k in ks:
            n += int(self.kv.pop(k, None) is not None) + int(self.sets.pop(k, None) is not None)
        return n

    async def exists(self, k):
        return int(k in self.kv or k in self.sets)

    async def expire(self, k, s):
        return 1

    async def sadd(self, k, *v):
        self.sets.setdefault(k, set()).update(v)
        return len(v)

    async def srem(self, k, *v):
        s = self.sets.get(k, set())
        n = len(s & set(v))
        s.difference_update(v)
        return n

    async def smembers(self, k):
        return set(self.sets.get(k, set()))


RUN = {
    "session_id": SID, "tenant_id": "tenant_demo", "customer_id": "cus",
    "pool_id": "sac_ia", "agent_type_id": "skill_sac_v1", "conference_id": "",
    "native_instance_id": INST, "native_snapshot": {"pools": ["sac_ia"]},
    "part_role": "primary", "part_seg_id": "seg-1", "part_seq_idx": 0,
    "part_parent_seg": "", "part_joined_iso": "2026-09-28T12:00:00+00:00",
    "is_hook_agent": False,
}


async def _estacionar(r: FakeRedis) -> None:
    await bridge_mod._park_native_run(r, dict(RUN), {"skills": [{"skill_id": "x"}], "webhook_pool": False})


@pytest.fixture
def ambiente(monkeypatch):
    r = FakeRedis()
    monkeypatch.setattr(bridge_mod, "_SHARED_HTTP", object())
    activate = AsyncMock()
    finish   = AsyncMock()
    monkeypatch.setattr(bridge_mod, "activate_native_agent", activate)
    monkeypatch.setattr(bridge_mod, "_finish_native_segment", finish)
    monkeypatch.setattr(bridge_mod, "_stl", lambda: 14400)
    return r, activate, finish


@pytest.mark.asyncio
async def test_sem_estacionamento_nao_chama_o_executor(ambiente):
    r, activate, finish = ambiente
    assert await bridge_mod.wake_parked_run(r, SID, INST, "reply") == "not_parked"
    activate.assert_not_awaited()
    finish.assert_not_awaited()


@pytest.mark.asyncio
async def test_acordar_chama_o_executor_como_ACORDAR_na_mesma_instancia(ambiente):
    r, activate, finish = ambiente
    await _estacionar(r)
    activate.return_value = {"outcome": "awaiting_input"}
    assert await bridge_mod.wake_parked_run(r, SID, INST, "reply") == "parked_again"
    kw = activate.await_args.kwargs
    assert kw["wake_only"] is True and kw["menu_wait"] == "park"
    assert kw["instance_id"] == INST and kw["segment_id"] == "seg-1"
    assert kw["skills"] == [{"skill_id": "x"}]
    finish.assert_not_awaited()                                   # o atendimento segue
    assert await r.exists(bridge_mod._parked_run_key(SID, INST))  # e segue estacionado


@pytest.mark.asyncio
async def test_fluxo_terminou_fecha_o_segmento_uma_vez(ambiente):
    r, activate, finish = ambiente
    await _estacionar(r)
    activate.return_value = {"outcome": "resolved", "pipeline_state": {}}
    assert await bridge_mod.wake_parked_run(r, SID, INST, "reply") == "finished"
    finish.assert_awaited_once()
    run_arg, result_arg = finish.await_args.args[2], finish.await_args.args[3]
    assert run_arg["native_instance_id"] == INST and run_arg["part_seg_id"] == "seg-1"
    assert result_arg["outcome"] == "resolved"
    assert not await r.exists(bridge_mod._parked_run_key(SID, INST))
    assert INST not in await r.smembers(bridge_mod._parked_runs_set(SID))


@pytest.mark.asyncio
async def test_dois_acordares_que_terminam_so_um_fecha(ambiente):
    """Os dois leram o registro antes de qualquer um apagá-lo — só quem apaga fecha."""
    r, activate, finish = ambiente
    await _estacionar(r)
    # O Redis falso não cede a vez: sem esta barreira o primeiro acordar terminaria
    # antes de o segundo ler o registro, e a corrida nunca aconteceria.
    dentro = 0
    ambos  = asyncio.Event()

    async def executor(**_kw):
        nonlocal dentro
        dentro += 1
        if dentro == 2:
            ambos.set()
        await ambos.wait()
        return {"outcome": "resolved", "pipeline_state": {}}

    activate.side_effect = executor
    resultados = await asyncio.gather(
        bridge_mod.wake_parked_run(r, SID, INST, "reply"),
        bridge_mod.wake_parked_run(r, SID, INST, "deadline"),
    )
    assert sorted(resultados) == ["already_finished", "finished"]
    finish.assert_awaited_once()


@pytest.mark.asyncio
async def test_412_nao_fecha_nada(ambiente):
    """Outra execução segura o pipeline: ela lê a caixa. Fechar aqui encerraria um atendimento vivo."""
    r, activate, finish = ambiente
    await _estacionar(r)
    activate.return_value = {}
    assert await bridge_mod.wake_parked_run(r, SID, INST, "reply") == "busy"
    finish.assert_not_awaited()
    assert await r.exists(bridge_mod._parked_run_key(SID, INST))


@pytest.mark.asyncio
async def test_pipeline_sumiu_com_a_conversa_estacionada_fecha_como_falha(ambiente):
    r, activate, finish = ambiente
    await _estacionar(r)
    activate.return_value = {"not_parked": True, "status": "absent"}
    assert await bridge_mod.wake_parked_run(r, SID, INST, "deadline") == "finished"
    assert finish.await_args.args[3] == {"outcome": "failed"}


@pytest.mark.asyncio
async def test_sem_sessao_http_nao_perde_a_conversa(ambiente, monkeypatch):
    r, activate, finish = ambiente
    monkeypatch.setattr(bridge_mod, "_SHARED_HTTP", None)
    await _estacionar(r)
    assert await bridge_mod.wake_parked_run(r, SID, INST, "reply") == "busy"
    activate.assert_not_awaited()
    assert await r.exists(bridge_mod._parked_run_key(SID, INST))


@pytest.mark.asyncio
async def test_fechamento_acorda_toda_conversa_estacionada_da_sessao(ambiente, monkeypatch):
    r, _, _ = ambiente
    await _estacionar(r)
    outro = dict(RUN, native_instance_id="sac_ia-002")
    await bridge_mod._park_native_run(r, outro, {"skills": [], "webhook_pool": False})
    chamados: list[tuple] = []

    async def fake_wake(redis_client, session_id, field, reason):
        chamados.append((session_id, field, reason))
        return "finished"

    monkeypatch.setattr(bridge_mod, "wake_parked_run", fake_wake)
    assert await bridge_mod.wake_all_parked_runs(r, SID, "closed") == 2
    await asyncio.sleep(0)                      # os acordares rodam em tasks
    for t in list(bridge_mod._BG_TASKS):
        await t
    assert sorted(chamados) == [(SID, "sac_ia-001", "closed"), (SID, "sac_ia-002", "closed")]


@pytest.mark.asyncio
async def test_varredura_acorda_so_o_vencido_e_so_uma_vez(monkeypatch):
    """
    D6: o prazo vencido acorda a conversa certa (sessão e instância tiradas da marca do
    engine, com o sufixo de conferência removido); o que não venceu fica; e o claim é o
    ZREM — um membro que outra réplica já removeu não é acordado de novo.
    """
    r = FakeRedis()
    agora = 1_000_000
    r.zsets = {"tenant_demo:menu:deadlines": {"s-vencido": agora - 1, "s-futuro": agora + 60_000,
                                              "s-outra-replica": agora - 5}}

    async def zrangebyscore(k, lo, hi, start=0, num=None):
        return [m for m, s in sorted(r.zsets.get(k, {}).items(), key=lambda x: x[1]) if s <= hi]

    async def zrem(k, m):
        if m == "s-outra-replica":
            return 0                                    # outra réplica levou o claim
        return int(r.zsets.get(k, {}).pop(m, None) is not None)

    r.zrangebyscore, r.zrem = zrangebyscore, zrem      # type: ignore[attr-defined]
    r.kv["tenant_demo:pipeline:s-vencido:parked"] = json.dumps({"instance_id": INST})
    r.kv["tenant_demo:pipeline:s-outra-replica:parked"] = json.dumps({"instance_id": INST})

    acordados: list[tuple] = []

    async def fake_wake(redis_client, session_id, field, reason):
        acordados.append((session_id, field, reason))
        return "finished"

    monkeypatch.setattr(bridge_mod, "wake_parked_run", fake_wake)
    monkeypatch.setattr(bridge_mod.time, "time", lambda: agora / 1000)

    async def para(_s):
        raise asyncio.CancelledError

    monkeypatch.setattr(bridge_mod.asyncio, "sleep", para)
    with pytest.raises(asyncio.CancelledError):
        await bridge_mod._menu_deadline_scanner(r, ["tenant_demo"])
    for t in list(bridge_mod._BG_TASKS):
        await t
    assert acordados == [("s-vencido", INST, "deadline")]
    assert "s-futuro" in r.zsets["tenant_demo:menu:deadlines"]


# ── F3 — agente de fila e `menu.wake` ─────────────────────────────────────────

QRUN = {
    "kind": "queue", "session_id": SID, "tenant_id": "tenant_demo", "customer_id": "cus",
    "pool_id": "retencao_humano", "flow_pool_id": "fila_humano", "agent_type_id": "agente_fila_v1",
    "native_instance_id": "", "q_participant": "queue-agent-x", "q_seg_id": "qseg-1",
    "q_joined_iso": "2026-09-28T12:00:00+00:00",
}


@pytest.mark.asyncio
async def test_fila_estaciona_no_campo_default_e_acorda_como_agente_de_fila(ambiente, monkeypatch):
    """
    O agente de fila roda SEM instância (campo `_default_`) e com parâmetros próprios:
    token com a identidade sintética, `extra_context.pool_id` = DESTINO (o YAML escala
    para lá), e o deploy do pool de FILA. Trocar qualquer um faria o acordar executar
    outra coisa — ou escalar o cliente para a própria fila.
    """
    r, activate, finish = ambiente
    fim_fila = AsyncMock()
    monkeypatch.setattr(bridge_mod, "_finish_queue_segment", fim_fila)
    await bridge_mod._park_native_run(r, dict(QRUN), {"skills": [], "webhook_pool": False})
    assert await r.exists(bridge_mod._parked_run_key(SID, "_default_"))

    activate.return_value = {"outcome": "escalated_human", "pipeline_state": {}}
    assert await bridge_mod.wake_parked_run(r, SID, "_default_", "agent_available") == "finished"
    kw = activate.await_args.kwargs
    assert kw["instance_id"] == "" and kw["token_instance_id"] == "queue-agent-x"
    assert kw["extra_context"] == {"pool_id": "retencao_humano"}
    assert kw["pool_id"] == "fila_humano" and kw["segment_id"] == "qseg-1"
    assert kw["wake_only"] is True
    fim_fila.assert_awaited_once()
    finish.assert_not_awaited()          # o fechamento do agente PRINCIPAL não é o da fila


@pytest.mark.asyncio
async def test_janela_retomada_acorda_sem_resume_context_e_fecha_como_retomada(ambiente, monkeypatch):
    """
    A janela retomada de um delegate/suspend estaciona num menu e acorda depois. O acordar
    NÃO reenvia o `resume_context` (consumido: o pipeline está `in_progress`) e fecha pelo
    fechamento DA RETOMADA — o do agente principal publicaria o segmento errado.
    """
    r, activate, finish = ambiente
    fim_resume = AsyncMock()
    monkeypatch.setattr(bridge_mod, "_finish_resume_segment", fim_resume)
    rrun = {
        "kind": "resume", "session_id": SID, "tenant_id": "tenant_demo", "customer_id": "cus",
        "pool_id": "demo_ia", "agent_type_id": "skill_navegacao_v1", "native_instance_id": INST,
        "native_snapshot": {}, "resume_participant": INST, "resume_seg_id": "rseg-1",
        "resume_seq_idx": 1, "resume_joined_iso": "2026-09-28T12:00:00+00:00",
    }
    await bridge_mod._park_native_run(r, rrun, {"skills": [], "webhook_pool": True})
    activate.return_value = {"outcome": "resolved", "pipeline_state": {}}
    assert await bridge_mod.wake_parked_run(r, SID, INST, "reply") == "finished"
    kw = activate.await_args.kwargs
    assert "resume_context" not in kw
    assert kw["webhook_pool"] is True and kw["wake_only"] is True and kw["instance_id"] == INST
    fim_resume.assert_awaited_once()
    finish.assert_not_awaited()


@pytest.mark.asyncio
async def test_menu_wake_acorda_o_campo_do_aviso(monkeypatch):
    chamados: list[tuple] = []

    async def fake_wake(redis_client, session_id, field, reason):
        chamados.append((session_id, field, reason))
        return "finished"

    monkeypatch.setattr(bridge_mod, "wake_parked_run", fake_wake)
    ev = {"event_type": "menu_wake", "tenant_id": "t", "session_id": SID,
          "field": "_default_", "reason": "agent_available", "timestamp": "2026-09-28T12:00:00Z"}
    assert await bridge_mod.process_menu_wake(ev, FakeRedis()) == "finished"
    assert chamados == [(SID, "_default_", "agent_available")]


@pytest.mark.asyncio
async def test_menu_wake_ilegivel_nao_acorda_nada(monkeypatch):
    chamados: list[tuple] = []

    async def fake_wake(*a):
        chamados.append(a)
        return "finished"

    monkeypatch.setattr(bridge_mod, "wake_parked_run", fake_wake)
    for ev in [{"event_type": "menu_wake", "session_id": SID},            # sem campo
               {"event_type": "outra_coisa", "session_id": SID, "field": INST},
               {"event_type": "menu_wake", "field": INST}]:            # sem sessão
        assert await bridge_mod.process_menu_wake(ev, FakeRedis()) == "invalid"
    assert chamados == []


@pytest.mark.asyncio
async def test_pool_menu_wait_so_estaciona_quando_declarado(monkeypatch):
    cfg = AsyncMock()
    monkeypatch.setattr(bridge_mod, "get_pool_config", cfg)
    for valor, esperado in [({"menu_wait": "park"}, "park"), ({"menu_wait": "block"}, "block"),
                            ({}, "block"), (None, "block")]:
        cfg.return_value = valor
        assert await bridge_mod._pool_menu_wait(object(), "t", "p") == esperado


@pytest.mark.asyncio
async def test_limpeza_da_subida_mantem_conversa_estacionada(monkeypatch):
    """O processo anterior não segurava a conversa estacionada: ela não morreu com ele."""
    r = FakeRedis()
    r.kv[f"session:{SID}:ai_completing:{INST}"] = "1"
    r.kv[f"session:{SID}:ai_completing:outra-001"] = "1"
    r.kv[bridge_mod._parked_run_key(SID, INST)] = json.dumps({"run": RUN, "activation": {}})

    async def keys(pattern):
        return [k for k in r.kv if k.startswith("session:") and ":ai_completing:" in k]

    r.keys = keys  # type: ignore[attr-defined]
    restore = AsyncMock()
    monkeypatch.setattr(bridge_mod, "_restore_instance", restore)
    monkeypatch.setattr(bridge_mod, "_kafka_producer", None)
    await bridge_mod._cleanup_stale_completing_at_startup(r)
    restaurados = [c.args[2] for c in restore.await_args_list]
    assert restaurados == ["outra-001"]         # a morta sim, a estacionada não
    assert f"session:{SID}:ai_completing:{INST}" in r.kv
