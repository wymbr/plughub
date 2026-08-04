"""
test_segment_close_reason_domain.py — Fase E (D8) do ADR
`adr-work-item-requeue-and-agent-affinity.md`: separar os dois domínios de
`close_reason` que um único mapa vinha servindo.

O QUE ESTÁ SENDO AFIRMADO

O `close_reason` responde "por que ISTO terminou", e "isto" são duas coisas:

  · o CONTATO   — enum fechado (`CloseReasonSchema`): agent_hangup,
                  customer_disconnect, session_timeout, max_wait_exceeded,
                  no_resource…
  · o SEGMENTO  — vocabulário livre, onde já vivem task_submitted, acw_expired
                  e acw_supervisor_closed.

`agent_disconnect` e `agent_transfer` são os transportes em que o contato **não**
fecha. Acrescentá-los ao mapa de contato escolheria um domínio em silêncio; antes
da Fase E eles ficavam de fora dos dois e o segmento saía mudo. Medido em
2026-08-04 (`probe_fase_e_drop_footprint.sh`): 14 de 31 segmentos humanos sem
`close_reason`, 12 deles em pools de pull cujo item nunca foi entregue — contra
9/9 COM motivo na fila interna, onde a entrega carimba pelo caminho do submit.

O QUE FARIA CADA CASO REPROVAR — e por que os invariantes valem mais que os mapeamentos

Os testes de mapeamento (1-9) reprovam se alguém trocar um valor. Os
INVARIANTES (10-15) reprovam a regressão que de fato aconteceria: alguém
"consertando" a lacuna 6 com uma linha a mais no mapa compartilhado. Esse é o
conserto que parece óbvio e é o descartado no § 4 do ADR — e nenhum teste de
mapeamento o pegaria, porque com ele todos os valores acima continuam certos.
"""
from __future__ import annotations

import pytest

import plughub_orchestrator_bridge.main as bridge_mod

SEGMENT_TRANSPORTS = ("agent_disconnect", "agent_transfer")

# O domínio fechado do CONTATO, escrito à mão a partir do CloseReasonSchema
# (`CLAUDE.md` § close_reason domain). Escrito, e não derivado do próprio mapa:
# derivá-lo do que o código faz tornaria o teste incapaz de reprovar.
CONTACT_DOMAIN = {
    "no_resource", "max_wait_exceeded", "customer_disconnect", "customer_hangup",
    "customer_abandon", "flow_complete", "agent_transfer", "agent_hangup",
    "session_timeout", "system_error",
}


# ── 1-2 · o domínio de SEGMENTO responde por conta própria ───────────────────

@pytest.mark.parametrize("transport,expected", [
    ("agent_disconnect", "agent_disconnect"),
    ("agent_transfer",   "agent_transfer"),
])
def test_segment_domain_transports_get_their_own_reason(transport, expected):
    """
    Estes dois são a Fase E inteira. Antes devolviam None e o segmento ia ao
    ClickHouse sem motivo — indistinguível, na tela, de um segmento que ninguém
    fechou.
    """
    assert bridge_mod._segment_close_reason_from_transport(transport, "sid") == expected


# ── 3-8 · transporte em que o contato TAMBÉM fecha → cai no mapa de contato ──

@pytest.mark.parametrize("transport,expected", [
    ("agent_closed",      "agent_hangup"),
    ("client_disconnect", "customer_disconnect"),
    ("timeout",           "session_timeout"),
    ("session_timeout",   "session_timeout"),
    ("max_wait_exceeded", "max_wait_exceeded"),
    ("no_resource",       "no_resource"),
])
def test_contact_transports_fall_through_unchanged(transport, expected):
    """
    A queda para o mapa de contato não é fallback de conveniência: quando o
    contato acaba, o motivo dele É o motivo do segmento. Se estes seis mudarem,
    a Fase E terá alterado o comportamento de todo encerramento normal — que é
    a regressão mais cara possível aqui, e a mais silenciosa (o valor continua
    plausível).
    """
    assert bridge_mod._segment_close_reason_from_transport(transport, "sid") == expected


# ── 9-10 · desconhecido continua AUSENTE, nos dois domínios ──────────────────

@pytest.mark.parametrize("transport", ["", "transporte_que_nao_existe"])
def test_unknown_transport_stays_none(transport):
    """
    Ausência visível > valor inventado. Um default aqui (p.ex. "agent_hangup")
    tornaria impossível notar um transporte novo sem mapeamento — é o defeito
    que o docstring de `_close_reason_from_transport` registra ter tido.
    """
    assert bridge_mod._segment_close_reason_from_transport(transport, "sid") is None


# ── 11-12 · INVARIANTE: o mapa de CONTATO não pode absorver o de segmento ────

@pytest.mark.parametrize("transport", SEGMENT_TRANSPORTS)
def test_contact_map_never_absorbs_segment_transports(transport):
    """
    A regressão que este arquivo existe para pegar.

    Acrescentar `agent_disconnect` a `_TRANSPORT_TO_CLOSE_REASON` faria os testes
    1-2 passarem (a queda encontraria o valor lá) e alimentaria com vocabulário
    de segmento um mapa que serve um ENUM FECHADO — e, pior, faria o customer_side
    e o hook de fim de contato passarem a carimbar o mesmo valor, onde o contato
    fechou de verdade. Ver ADR § 4, alternativa descartada.
    """
    assert transport not in bridge_mod._TRANSPORT_TO_CLOSE_REASON, (
        f"{transport!r} entrou no mapa de CONTATO — o contato não fecha nesse "
        f"transporte, e o mapa alimenta um enum fechado"
    )


# ── 13 · INVARIANTE: os dois mapas são disjuntos ─────────────────────────────

def test_maps_are_disjoint():
    """
    Chave nos dois mapas = precedência decidindo em silêncio qual vocabulário
    vale. A precedência existe (segmento primeiro) e é deliberada, mas só é
    legítima enquanto não houver sobreposição para ela arbitrar.
    """
    overlap = set(bridge_mod._TRANSPORT_TO_SEGMENT_CLOSE_REASON) & set(
        bridge_mod._TRANSPORT_TO_CLOSE_REASON
    )
    assert not overlap, f"transportes em AMBOS os mapas: {sorted(overlap)}"


# ── 14 · INVARIANTE: o mapa de contato só emite valores do enum fechado ──────

def test_contact_map_values_stay_inside_the_closed_enum():
    """
    `sessions.close_reason` é validado contra `CloseReasonSchema`. Um valor de
    domínio de segmento vazando para cá não quebra nada na hora: escreve, e só
    aparece como categoria estranha num relatório, meses depois.
    """
    fora = set(bridge_mod._TRANSPORT_TO_CLOSE_REASON.values()) - CONTACT_DOMAIN
    assert not fora, f"valores fora do domínio fechado de contato: {sorted(fora)}"


# ── 15 · INVARIANTE: o domínio de contato NÃO mudou de comportamento ─────────

def test_contact_resolver_still_reports_absence_for_a_drop():
    """
    A função de CONTATO tem de continuar dizendo "não sei" para `agent_disconnect`.
    Se ela passar a responder, é porque o mapa compartilhado foi estendido — e os
    outros dois call sites (customer_side, hook de fim) começariam a carimbar
    queda como se o contato tivesse terminado.
    """
    assert bridge_mod._close_reason_from_transport("agent_disconnect", "sid") is None
