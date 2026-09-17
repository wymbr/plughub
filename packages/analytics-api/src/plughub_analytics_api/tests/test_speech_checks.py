"""
tests/test_speech_checks.py — verificação ativa do caminho de fala na analytics-api (VOZ-23).

Parser: resultado sem sessão atravessa (falhou antes da chamada); por item só os campos nomeados.
Consulta: recorte pelo escopo, perfil nulo ≠ todos. Comparação: cada ausência de comparação tem o seu
status — nunca um delta zero —, e regressão é item certo na base e errado agora.
"""
from __future__ import annotations

import asyncio
import json
import re
from unittest.mock import AsyncMock, MagicMock

from plughub_analytics_api import consumer
from plughub_analytics_api.models import parse_speech_metrics_event
from plughub_analytics_api.reports_query import (
    build_speech_check_comparison,
    compare_speech_checks,
    fetch_speech_checks,
)

ITEM = {"id": "p01", "kind": "phrase", "transcripts": 1, "confidence": 0.8, "correct": True, "wer": 0.0,
        "hallucinated": None}
RESULTADO = {
    "event_id": "e9", "event_type": "speech_check_result", "tenant_id": "t", "session_id": "s-9",
    "pool_id": "speech_check", "channel": "webrtc", "speech_profile_id": "sip",
    "timestamp": "2026-09-17T12:00:00+00:00", "check_id": "c-1", "requested_by": "ana@x",
    "reference_version": "pt-1", "language": "pt-BR", "status": "completed", "failure_reason": None,
    "started_at": "2026-09-17T11:59:00+00:00", "bot_voice_heard": True, "profile_in_effect": "sip",
    "stt_model": "m", "discarded_vad": 1, "utterances_sent": 8,
    "segmentation": {"energy_threshold": 400.0, "end_silence_ms": 700, "gap_ms": 700, "min_speech_ms": 250,
                     "max_speech_ms": 15000, "vad_filter": True},
    "phrases_total": 7, "phrases_correct": 6, "phrases_transcribed": 7, "accuracy": 0.8571, "wer_mean": 0.03,
    "confidence_p10": 0.5, "confidence_p50": 0.7, "confidence_p90": 0.9, "noise_total": 1, "hallucinations": 0,
    "items": [ITEM],
}


class TestParser:
    def test_resultado_vai_a_tabela_com_itens_nomeados(self):
        r = parse_speech_metrics_event({**RESULTADO, "items": [{**ITEM, "text": "cancelar"}]})
        assert r["table"] == "speech_checks" and r["check_id"] == "c-1" and r["accuracy"] == 0.8571
        assert json.loads(r["items"]) == [ITEM] and "cancelar" not in r["items"]
        assert json.loads(r["segmentation"])["end_silence_ms"] == 700

    def test_falha_antes_da_chamada_sem_sessao_atravessa_com_agregados_nulos(self):
        r = parse_speech_metrics_event({**RESULTADO, "session_id": None, "status": "failed",
                                        "failure_reason": "profile_not_found", "accuracy": None,
                                        "segmentation": None, "items": [], "phrases_total": 0})
        assert r["session_id"] is None and r["accuracy"] is None and r["segmentation"] == ""
        assert r["failure_reason"] == "profile_not_found"

    def test_controle_outros_eventos_sem_sessao_continuam_recusados(self):
        assert parse_speech_metrics_event({"event_id": "e", "tenant_id": "t", "session_id": None,
                                           "event_type": "stt_stream_summary"}) is None
        assert parse_speech_metrics_event({**RESULTADO, "check_id": ""}) is None

    def test_consumidor_escreve_e_o_store_real_tem_o_insert_com_colunas_do_ddl(self):
        store = MagicMock()
        store.insert_speech_check = AsyncMock()
        asyncio.run(consumer._write_row(store, parse_speech_metrics_event(RESULTADO), "speech.metrics", 1))
        store.insert_speech_check.assert_awaited_once()
        from plughub_analytics_api import clickhouse as ch
        assert hasattr(ch.AnalyticsStore, "insert_speech_check") and ch._DDL_SPEECH_CHECKS in ch._ALL_DDL
        colunas = re.findall(r"^\s{4}(\w+)\s+\S", ch._DDL_SPEECH_CHECKS, flags=re.M)
        assert ch.AnalyticsStore._SPEECH_CHECK_COLS == colunas
        linha = parse_speech_metrics_event(RESULTADO)
        assert set(colunas) - {"timestamp", "date"} <= set(linha)


class _Cli:
    def __init__(self, linhas=()):
        self.sqls, self.params, self.linhas = [], [], list(linhas)

    def query(self, sql, parameters=None):
        self.sqls.append(sql)
        self.params.append(parameters)
        r = MagicMock()
        r.column_names = ["check_id", "items", "segmentation", "bot_voice_heard", "accuracy"]
        r.result_rows = self.linhas
        return r


class TestConsulta:
    def test_recorte_final_e_perfil(self):
        cli = _Cli([("c-1", json.dumps([ITEM]), "", 1, float("nan"))])
        (r,) = fetch_speech_checks(cli, "db", "t", "sip", ["speech_check"])
        s = cli.sqls[0]
        assert "FINAL" in s and "pool_id IN ('speech_check')" in s and "speech_profile_id = {profile:String}" in s
        assert r["items"] == [ITEM] and r["segmentation"] is None and r["bot_voice_heard"] is True
        assert r["accuracy"] is None

    def test_perfil_vazio_e_sem_perfil_e_ausente_e_todas(self):
        cli = _Cli()
        fetch_speech_checks(cli, "db", "t", "", None)
        fetch_speech_checks(cli, "db", "t", None, None)
        assert "speech_profile_id IS NULL" in cli.sqls[0]
        assert "speech_profile_id" not in cli.sqls[1].split("WHERE")[1]


def _check(cid, status="completed", itens=None, ref="pt-1", **kw):
    return {"check_id": cid, "status": status, "reference_version": ref, "accuracy": kw.get("accuracy", 1.0),
            "wer_mean": 0.0, "confidence_p50": kw.get("conf", 0.8), "hallucinations": kw.get("hal", 0),
            "stt_model": kw.get("model", "m"), "segmentation": kw.get("seg", {"end_silence_ms": 700}),
            "items": itens if itens is not None else [
                {"id": "p01", "kind": "phrase", "correct": True, "hallucinated": None},
                {"id": "p02", "kind": "phrase", "correct": False, "hallucinated": None},
                {"id": "n01", "kind": "noise", "correct": None, "hallucinated": False}]}


class TestComparacao:
    def test_regressao_melhora_e_mudanca_de_config_sao_medidas(self):
        base = _check("b")
        novo = _check("n", itens=[{"id": "p01", "kind": "phrase", "correct": False, "hallucinated": None},
                                  {"id": "p02", "kind": "phrase", "correct": True, "hallucinated": None},
                                  {"id": "n01", "kind": "noise", "correct": None, "hallucinated": True}],
                      accuracy=0.5, conf=0.6, hal=1, model="m2", seg={"end_silence_ms": 900})
        c = compare_speech_checks(base, novo)
        assert c["items_regressed"] == ["p01", "n01"] and c["items_improved"] == ["p02"]
        assert (c["accuracy_delta"], c["confidence_p50_delta"], c["hallucinations_delta"]) == (-0.5, -0.2, 1)
        assert c["config_changes"] == {"stt_model": {"baseline": "m", "latest": "m2"},
                                       "segmentation.end_silence_ms": {"baseline": 700, "latest": 900}}

    def test_controle_mesma_execucao_nao_regride(self):
        c = compare_speech_checks(_check("b"), _check("n"))
        assert c["items_regressed"] == [] and c["items_improved"] == [] and c["config_changes"] == {}

    def test_cada_ausencia_de_comparacao_tem_o_seu_status(self):
        n, b = _check("n"), _check("b")
        assert build_speech_check_comparison(None, "config-api: caiu", [n, b])["status"] == "baseline_unavailable"
        assert build_speech_check_comparison(None, None, [n, b])["status"] == "no_baseline"
        assert build_speech_check_comparison({"check_id": "x"}, None, [n, b])["status"] == "baseline_invalid"
        assert build_speech_check_comparison({"check_id": "b"}, None, [n, _check("b", status="failed")])["status"] == "baseline_invalid"
        assert build_speech_check_comparison({"check_id": "b"}, None, [b])["status"] == "latest_is_baseline"
        assert build_speech_check_comparison({"check_id": "b"}, None, [_check("n", ref="pt-2"), b])["status"] == "reference_changed"
        out = build_speech_check_comparison({"check_id": "b"}, None, [_check("f", status="failed"), n, b])
        assert out["status"] == "compared" and out["latest"]["check_id"] == "n"      # a falha não é a "mais recente"
        assert out["comparison"] is not None and out["baseline"]["check_id"] == "b"
