"""
test_agent_event_segment.py
Arc 12 fatia 2 (2026-08-03) — `segment_id` em `agent_business_events`.

O QUE ESTA SUÍTE DEFENDE. A lacuna era de ATRIBUIÇÃO: a marcação ia para a sessão, e
numa sessão orquestrada (primary + especialista, humano + hook de wrap-up) não havia como
saber quem emitiu o KPI. `agent_type_id` agrega todos os agentes daquele tipo.

Três invariantes, e cada um tem um modo de falha silencioso:

  · **precedência** — `segment_id` do payload (o emissor DECLAROU o próprio segmento)
    vence o do enricher (que DEDUZ a partir do `instance_id`, e uma instância pode ter
    mais de um segmento ao longo da sessão). Invertida, a atribuição fica plausível e
    errada, que é pior que ausente.
  · **ausência é NULL, não ""** — string vazia se confundiria com um segmento real de id
    vazio e o relatório perderia a distinção entre "ninguém atribuiu" e "atribuído".
  · **posição na lista de colunas** — `_AGENT_BUSINESS_EVENT_COLS` e
    `_agent_business_event_row` são POSICIONAIS e casadas. Deslocar uma sem a outra não
    levanta erro: grava o valor de uma coluna em outra do mesmo tipo. É a falha que só
    aparece no relatório, semanas depois.
"""
from __future__ import annotations

from ..clickhouse import AnalyticsStore, _agent_business_event_row
from ..models import parse_agent_business_event

_BASE = {
    "event_id":   "evt-1",
    "tenant_id":  "tenant_test",
    "session_id": "sess-1",
    "category":   "retencao_humano.skill_x.offer_accepted",
    "value":      1.0,
    "emitted_at": "2026-08-03T12:00:00+00:00",
}


class TestPrecedence:
    def test_payload_segment_wins_over_enricher(self):
        """Quem declara vence quem deduz."""
        row = parse_agent_business_event(
            {**_BASE, "segment_id": "seg_do_skill"}, segment_id="seg_do_enricher",
        )
        assert row["segment_id"] == "seg_do_skill"

    def test_enricher_fills_when_payload_has_none(self):
        row = parse_agent_business_event({**_BASE}, segment_id="seg_do_enricher")
        assert row["segment_id"] == "seg_do_enricher"

    def test_absence_is_None_not_empty_string(self):
        row = parse_agent_business_event({**_BASE})
        assert row["segment_id"] is None

    def test_empty_string_in_payload_degrades_to_None(self):
        """`""` no payload não pode virar um segmento de id vazio na tabela."""
        row = parse_agent_business_event({**_BASE, "segment_id": ""})
        assert row["segment_id"] is None

    def test_instance_id_travels_but_is_not_a_column(self):
        """`instance_id` é chave de enriquecimento, não dimensão de relatório.

        Ele existe na linha intermediária (para o enricher) e some antes do INSERT —
        o row builder só lê `segment_id`. Se virasse coluna, seria uma segunda
        identidade de participante concorrendo com `segment_id`, e o invariante do
        CLAUDE.md sobre fato de escopo estreito ia junto.
        """
        row = parse_agent_business_event({**_BASE, "instance_id": "inst-9"})
        assert row["instance_id"] == "inst-9"
        assert "instance_id" not in AnalyticsStore._AGENT_BUSINESS_EVENT_COLS


class TestColumnAlignment:
    def test_row_builder_matches_column_list_positionally(self):
        """O contrato que não levanta erro quando quebra.

        Comprimentos iguais NÃO bastam — as duas listas podem ter o mesmo tamanho com a
        ordem trocada, e o INSERT aceita, gravando `segment_id` onde deveria ir
        `emitted_at` se os tipos permitirem. Por isso a asserção é sobre o VALOR na
        posição nomeada, não sobre o tamanho.
        """
        cols = AnalyticsStore._AGENT_BUSINESS_EVENT_COLS
        row  = _agent_business_event_row(
            parse_agent_business_event({**_BASE, "segment_id": "seg_abc"})
        )
        assert len(row) == len(cols), "row builder e lista de colunas divergiram"

        idx = cols.index("segment_id")
        assert row[idx] == "seg_abc"

        # e as vizinhas continuam no lugar (é o deslocamento que se quer pegar)
        assert row[cols.index("value")] == 1.0
        assert row[cols.index("tags")] == {}
        assert row[cols.index("session_id")] == "sess-1"

    def test_null_segment_survives_the_row_builder(self):
        cols = AnalyticsStore._AGENT_BUSINESS_EVENT_COLS
        row  = _agent_business_event_row(parse_agent_business_event({**_BASE}))
        assert row[cols.index("segment_id")] is None
