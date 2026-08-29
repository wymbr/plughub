"""
resources_query.py — consultas da **Superfície B · Recursos** (F3 do ADR de relatórios).

A superfície da OFERTA. Enquanto `contacts_series.py` responde *"o que estes contatos
custaram"*, aqui a pergunta é *"quanto este RECURSO gastou"* — e a diferença não é de
rótulo, é de POPULAÇÃO.

────────────────────────────────────────────────────────────────────────────
POR QUE NÃO DÁ PARA REUSAR O `query_token_breakdown` DA SUPERFÍCIE A
────────────────────────────────────────────────────────────────────────────
Aquele faz `INNER JOIN` com as sessões SELECIONADAS pelo filtro de contatos, de
propósito: a tabela dele aparece embaixo do gráfico da superfície A, sob a mesma barra,
e responder sobre outra população seria a divergência que a F2 fechou.

Para a conta LLM isso é o predicado errado, e a diferença foi MEDIDA em 2026-08-29
antes de escrever este arquivo:

    todos os eventos de token .................. 20 eventos · 1 991 tokens
    com sessão existente em `sessions` .......... 8 eventos ·   945 tokens

Ou seja: reusar o endpoint da superfície A para perguntar "quanto a conta gastou"
publicaria **47% do consumo real**, e publicaria em silêncio — nenhum erro, nenhuma
linha vermelha, um número plausível. Depois do corte de época sobram 12 eventos
atribuídos, e **4 deles (347 tokens) continuam sem sessão em `sessions`**: consumo real,
de sessões que nunca chegaram à tabela de contatos.

O dinheiro é gasto pela CONTA, não pelo contato. A população é o `usage_events` inteiro
do período — quem tem sessão e quem não tem.

────────────────────────────────────────────────────────────────────────────
DUAS AUSÊNCIAS, DOIS CONTADORES
────────────────────────────────────────────────────────────────────────────
Segue a regra do `usage_attribution`: corta em `USAGE_ATTRIBUTION_EPOCH` e conta o
não-informado do período pós-época como número PRÓPRIO. Aqui isso vira dois campos
distintos no `meta`, porque na superfície da oferta as duas ausências têm donos
diferentes:

  `pre_epoch_events`   — história. Não se conserta, e some da agregação.
  `unidentified_events`— pós-época e ainda **sem conta**: é DEFEITO de propagação, e
                         some se for somado com o anterior.

E há uma terceira, que não é ausência de dado e sim de CADASTRO:
`account_config_id` vazio com `account_key_id` presente. A conta existe e consumiu; ela
só não está no catálogo `llm_accounts`. Medido neste ambiente: **é o caso de 100% dos
eventos**, porque o demo roda pela chave de env legada. Publicá-la como "desconhecida"
esconderia que o consumo É atribuível — à chave, não ao cadastro.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from .usage_attribution import (
    USAGE_ATTRIBUTION_EPOCH,
    USAGE_PRODUCER_EPOCH,
    attribution_where,
)

logger = logging.getLogger("plughub.analytics.resources_query")


def _fetch_account_tokens(
    client: Any, db: str, tenant_id: str, since: str, until: str, limit: int,
) -> tuple[list[dict], dict]:
    """Consumo por CONTA × modelo × origem, sobre todo o `usage_events` do período."""
    params = {"tenant_id": tenant_id, "since": since, "until": until}
    janela = (
        "u.tenant_id = {tenant_id:String}"
        " AND u.dimension IN ('llm_tokens_input', 'llm_tokens_output')"
        " AND u.timestamp >= parseDateTimeBestEffort({since:String})"
        " AND u.timestamp <= parseDateTimeBestEffort({until:String})"
    )

    linhas = client.query(f"""
        SELECT
            u.account_config_id AS account_config_id,
            u.account_key_id    AS account_key_id,
            u.model_id          AS model_id,
            u.model_profile     AS model_profile,
            u.source            AS source,
            sumIf(u.quantity, u.dimension = 'llm_tokens_input')  AS tokens_in,
            sumIf(u.quantity, u.dimension = 'llm_tokens_output') AS tokens_out,
            uniqExact(u.session_id) AS sessions,
            uniqExact(u.event_id)   AS events
        FROM {db}.usage_events AS u
        WHERE {janela} AND {attribution_where('u.timestamp')}
        GROUP BY account_config_id, account_key_id, model_id, model_profile, source
        ORDER BY tokens_in DESC
        LIMIT {int(limit)}
    """, parameters=params)

    cols = linhas.column_names
    dados = [dict(zip(cols, r)) for r in linhas.result_rows]

    # Os dois contadores de ausência, medidos na MESMA janela — e nunca somados.
    # Um `SELECT` só, com `countIf`, para não haver risco de as duas contagens
    # descreverem períodos diferentes.
    aux = client.query(f"""
        SELECT
            countIf(NOT ({attribution_where('u.timestamp')}))                     AS pre_epoch_events,
            countIf({attribution_where('u.timestamp')} AND u.account_key_id = ''
                    AND u.account_config_id = '')                                 AS unidentified_events,
            countIf({attribution_where('u.timestamp')} AND u.account_config_id = ''
                    AND u.account_key_id != '')                                   AS uncatalogued_events
        FROM {db}.usage_events AS u
        WHERE {janela}
    """, parameters=params)
    a = dict(zip(aux.column_names, aux.result_rows[0])) if aux.result_rows else {}
    return dados, a


async def query_account_tokens(
    client:    Any,
    database:  str,
    tenant_id: str,
    since:     str,
    until:     str,
    *,
    limit: int = 100,
) -> dict:
    """
    Consumo de LLM por CONTA — a metade B da lente de token (D2).

    Sem `accessible_pools`: a conta é recurso de TENANT e o gasto dela não se reparte
    por pool. Filtrar por pool aqui devolveria a soma de um subconjunto sob o rótulo do
    todo — que é pior que não filtrar. A lente declara `honors: 'period_only'` e a tela
    diz isso; o contrato existe justamente para essa afirmação não ficar só no código.
    """
    try:
        dados, aux = await asyncio.to_thread(
            _fetch_account_tokens, client, database, tenant_id, since, until, limit,
        )
    except Exception as exc:
        logger.warning("query_account_tokens failed tenant=%s: %s", tenant_id, exc)
        # `error` nomeado, nunca lista vazia: uma consulta que falhou e uma conta que
        # não gastou nada são a mesma tela sem esta distinção.
        return {"data": [], "meta": {"from": since, "to": until}, "error": "data_unavailable"}

    return {
        "data": dados,
        "meta": {
            "from": since,
            "to": until,
            "attribution_epoch": USAGE_ATTRIBUTION_EPOCH,
            "producer_epoch":    USAGE_PRODUCER_EPOCH,
            "pre_epoch_events":     int(aux.get("pre_epoch_events") or 0),
            "unidentified_events":  int(aux.get("unidentified_events") or 0),
            "uncatalogued_events":  int(aux.get("uncatalogued_events") or 0),
            # A população desta lente é o `usage_events` INTEIRO, e o campo existe para
            # que ninguém a compare com a da superfície A sem perceber que são duas.
            "population": "all_usage_events",
        },
    }
