"""
tests/test_speech_metrics.py — speech.metrics na analytics-api (VOZ-22).

Parser: só campos nomeados atravessam, e percentil sem amostra fica ausente (nunca 0).
Relatório: o recorte por pool é do conjunto que o operador ALCANÇA, lido no SQL EXECUTADO.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from plughub_analytics_api import consumer
from plughub_analytics_api.models import parse_speech_metrics_event
from plughub_analytics_api.reports_query import query_speech_quality

RESUMO = {
    "event_id": "e1", "event_type": "stt_stream_summary", "tenant_id": "t", "session_id": "s",
    "pool_id": "p", "channel": "webrtc", "speaker": "customer", "stt_provider": "SpeachesSTTProvider",
    "timestamp": "2026-09-17T12:00:00+00:00", "audio_ms": 60000, "frames": 3000, "voiced_frames": 400,
    "noise_rms_p10": 0.0, "noise_rms_p50": 12.5, "noise_rms_p90": 80.0,
    "utterances_sent": 5, "utterances_transcribed": 4, "discarded_vad": 1, "discarded_short": 2,
    "cut_max_speech": 0, "stt_errors": 0, "confidence_count": 0,
    "confidence_p10": None, "confidence_p50": None, "confidence_p90": None,
    "segmentation": {"energy_threshold": 400.0, "end_silence_ms": 700, "gap_ms": 700,
                     "min_speech_ms": 250, "max_speech_ms": 15000, "vad_filter": True},
    "segmentation_scope": {"end_silence_ms": "tenant"},
}
DESFECHO = {
    "event_id": "e2", "event_type": "collect_outcome", "tenant_id": "t", "session_id": "s", "pool_id": "p",
    "channel": "webrtc", "timestamp": "2026-09-17T12:00:01+00:00", "menu_id": "m", "interaction": "button",
    "inputs": ["dtmf", "voice"], "outcome": "invalid", "via": None, "release_reason": None,
    "speech_inputs": 1, "digit_inputs": 0, "invalid_attempts": 1, "invalid_low_confidence": 1,
    "digit_after_speech": False, "min_confidence": 0.99, "end_silence_ms": None, "max_speech_ms": None,
    "duration_ms": 4200,
}


class TestParser:
    def test_resumo_vai_a_tabela_com_ausencia_preservada(self):
        r = parse_speech_metrics_event(RESUMO)
        assert r["table"] == "speech_stream_summaries"
        assert r["confidence_p50"] is None and r["noise_rms_p10"] == 0.0     # ausente ≠ zero
        assert r["seg_end_silence_ms"] == 700 and r["seg_vad_filter"] is True
        assert r["segmentation_scope"] == '{"end_silence_ms": "tenant"}'

    def test_desfecho_vai_a_tabela(self):
        r = parse_speech_metrics_event(DESFECHO)
        assert (r["table"], r["outcome"], r["invalid_low_confidence"], r["via"]) == \
            ("speech_collect_outcomes", "invalid", 1, None)

    def test_campo_de_texto_nao_atravessa(self):
        r = parse_speech_metrics_event({**DESFECHO, "value": "correio", "transcript": "meu cpf"})
        assert "value" not in r and "transcript" not in r and "correio" not in repr(r)

    def test_sem_ids_ou_tipo_desconhecido_e_ignorado(self):
        assert parse_speech_metrics_event({**RESUMO, "session_id": ""}) is None
        assert parse_speech_metrics_event({**RESUMO, "event_type": "outro"}) is None

    def test_o_consumidor_assina_o_topico_e_escreve_nas_duas_tabelas(self):
        assert "speech.metrics" in consumer._TOPICS and consumer._PARSERS["speech.metrics"] is parse_speech_metrics_event
        store = MagicMock()
        store.insert_speech_stream_summary = AsyncMock()
        store.insert_speech_collect_outcome = AsyncMock()
        asyncio.run(consumer._write_row(store, parse_speech_metrics_event(RESUMO), "speech.metrics", 1))
        asyncio.run(consumer._write_row(store, parse_speech_metrics_event(DESFECHO), "speech.metrics", 2))
        store.insert_speech_stream_summary.assert_awaited_once()
        store.insert_speech_collect_outcome.assert_awaited_once()

    def test_o_store_real_tem_os_dois_inserts(self):
        # o MagicMock acima CRIA os métodos; aqui se confere que o store de verdade os tem
        from plughub_analytics_api.clickhouse import AnalyticsStore
        assert hasattr(AnalyticsStore, "insert_speech_stream_summary")
        assert hasattr(AnalyticsStore, "insert_speech_collect_outcome")


class _Cliente:
    def __init__(self):
        self.sqls: list[str] = []

    def query(self, sql, parameters=None):
        self.sqls.append(sql)
        r = MagicMock()
        if "speech_stream_summaries" in sql:
            r.column_names = ["pool_id", "calls", "noise_rms_p50_median", "confidence_p50_median"]
            r.result_rows = [("p", 3, 12.0, float("nan"))]
        else:
            r.column_names = ["pool_id", "collects", "value"]
            r.result_rows = [("p", 5, 4), ("q", 1, 1)]
        return r


class TestRelatorio:
    def test_recorta_pelos_pools_alcancados_le_final_e_marca_amostra(self):
        cli = _Cliente()
        out = asyncio.run(query_speech_quality(cli, "db", "t", accessible_pools=["p"], min_sample=3))
        assert len(cli.sqls) == 2
        assert all("pool_id IN ('p')" in s and "FINAL" in s for s in cli.sqls)
        linhas = {r["pool_id"]: r for r in out["data"]}
        assert linhas["p"]["calls"] == 3 and linhas["p"]["sample_sufficient"] is True
        assert linhas["p"]["confidence_p50_median"] is None            # NaN do quantile vira ausente
        assert linhas["q"]["calls"] == 0 and linhas["q"]["sample_sufficient"] is False

    def test_nenhum_alias_repete_coluna_da_tabela(self):
        # `sum(x) AS x` derruba a query em ClickHouse (code 184) e o wrapper devolve data_unavailable;
        # o cliente falso acima não executa SQL, então a regra se confere aqui, contra o DDL
        import re
        from plughub_analytics_api import clickhouse as ch
        colunas = {m for ddl in (ch._DDL_SPEECH_STREAM_SUMMARIES, ch._DDL_SPEECH_COLLECT_OUTCOMES)
                   for m in re.findall(r"^\s{4}(\w+)\s+\S", ddl, flags=re.M)}
        assert {"utterances_sent", "invalid_attempts", "audio_ms"} <= colunas
        cli = _Cliente()
        asyncio.run(query_speech_quality(cli, "db", "t"))
        aliases = {a for s in cli.sqls for a in re.findall(r"\bAS\s+(\w+)", s)} - {"pool_id"}
        assert aliases and not (aliases & colunas), aliases & colunas

    def test_escopo_vazio_nao_consulta(self):
        cli = _Cliente()
        out = asyncio.run(query_speech_quality(cli, "db", "t", accessible_pools=[]))
        assert out["data"] == [] and cli.sqls == []

    def test_controle_escopo_irrestrito_nao_filtra_pool(self):
        cli = _Cliente()
        asyncio.run(query_speech_quality(cli, "db", "t", accessible_pools=None))
        assert all("pool_id IN" not in s for s in cli.sqls)
