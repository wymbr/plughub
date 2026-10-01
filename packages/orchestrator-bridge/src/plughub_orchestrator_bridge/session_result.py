"""
session_result.py — o RESULTADO terminal da sessão (AAS-05; adr-a2a-server-binding D5).

Quando o fluxo do agente principal termina num `complete`, o engine devolve o bloco `terminal`
(passo, outcome, motivo e o resultado declarado em `result_from`). Este módulo o grava, legível,
em `{tenant}:session:{session_id}:result`, com o TTL da sessão — é dali que o status honesto e o
artefato A2A (`tasks/get`, AAS-06) leem.

Por que o bridge, e não o engine: o bridge é quem escreve os fatos de ciclo de vida da sessão
(`session_closed`, status `suspended`), conhece o pool que atende e o TTL da sessão, e já lê o pool
FRESCO do registry. O engine não sabe em que pool roda.

O resultado é conferido contra o `output_schema` do contrato A2A do pool (D5), e o VEREDICTO é
gravado junto — nunca escondido, nunca usado para descartar o resultado:

    contract.checked = false, reason = no_contract           o pool não tem contrato A2A
                                     = result_not_declared   o `complete` não declara `result_from`
                                     = result_missing        declarou e a chave não existia
                                     = registry_unavailable  não deu para ler o contrato (WARNING)
    contract.checked = true,  valid  = true | false, errors  (inválido sai em WARNING, nomeando)

Quem decide o que fazer com um resultado inválido é o leitor (o adapter A2A responde FAILED com o
motivo); aqui só se registra o que aconteceu. Falha de escrita é ERROR e não derruba o fechamento:
a sessão já terminou quando isto roda.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, UnknownType

logger = logging.getLogger("plughub.bridge.session_result")

MAX_ERRORS = 5


def result_key(tenant_id: str, session_id: str) -> str:
    return f"{tenant_id}:session:{session_id}:result"


def check_contract(pool: Optional[dict], result: Optional[dict]) -> dict:
    """O veredicto do resultado contra o `output_schema` do contrato A2A do pool."""
    descriptor = (pool or {}).get("a2a") if pool is not None else None
    if pool is None:
        return {"checked": False, "reason": "registry_unavailable"}
    if not descriptor:
        return {"checked": False, "reason": "no_contract"}
    if result is None:
        return {"checked": False, "reason": "result_not_declared"}
    if result.get("missing"):
        return {"checked": False, "reason": "result_missing"}
    schema = descriptor.get("output_schema") or {}
    try:
        # `check_schema` ANTES de validar: um `type` desconhecido não é SchemaError na construção,
        # é UnknownType no meio da validação — e derrubava a gravação (medido no teste).
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema)
        errors = sorted(validator.iter_errors(result.get("value")), key=lambda e: list(e.path))
    except (SchemaError, UnknownType) as exc:
        # o registry recusa contrato sem `type`, mas um schema malformado de outro jeito chega aqui
        return {"checked": False, "reason": "schema_invalid", "detail": str(getattr(exc, "message", exc))[:200]}
    if not errors:
        return {"checked": True, "valid": True}
    return {
        "checked": True,
        "valid":   False,
        "errors":  [f"{'/'.join(str(p) for p in e.path) or '(raiz)'}: {e.message}"[:300] for e in errors[:MAX_ERRORS]],
    }


async def persist_session_result(
    redis_client: Any,
    *,
    tenant_id:      str,
    session_id:     str,
    pool_id:        str,
    skill_id:       str,
    deploy_version: str,
    terminal:       dict,
    ttl_s:          int,
    fetch_pool:     Callable[[str, str], Awaitable[Optional[dict]]],
) -> Optional[dict]:
    """Grava o resultado e devolve o documento gravado (None se não gravou)."""
    result = terminal.get("result") if isinstance(terminal.get("result"), dict) else None
    pool = await fetch_pool(tenant_id, pool_id) if pool_id else None
    contract = check_contract(pool, result) if pool_id else {"checked": False, "reason": "no_pool"}

    if contract.get("reason") == "registry_unavailable":
        logger.warning("resultado da sessão %s gravado SEM conferir o contrato: registry não respondeu "
                       "para o pool %s", session_id, pool_id)
    elif contract.get("checked") and not contract.get("valid"):
        logger.warning("resultado da sessão %s NÃO cumpre o output_schema do pool %s (skill %s): %s",
                       session_id, pool_id, skill_id, " · ".join(contract.get("errors") or []))
    elif contract.get("reason") == "result_missing":
        logger.warning("sessão %s fechou SEM o resultado que o complete declarou (result_from=%s)",
                       session_id, (result or {}).get("from"))

    doc = {
        "session_id":     session_id,
        "tenant_id":      tenant_id,
        "pool_id":        pool_id or None,
        "skill_id":       skill_id or None,
        "deploy_version": deploy_version or None,
        "step_id":        terminal.get("step_id"),
        "outcome":        terminal.get("outcome"),
        "completed_at":   datetime.now(timezone.utc).isoformat(),
        "contract":       contract,
    }
    if terminal.get("issue_status"):
        doc["issue_status"] = terminal["issue_status"]
    if result is not None:
        doc["result"] = result
    try:
        await redis_client.set(result_key(tenant_id, session_id), json.dumps(doc, default=str), ex=ttl_s)
    except Exception as exc:  # noqa: BLE001 — dito, nunca calado
        logger.error("não gravei o resultado da sessão %s (%s): %s", session_id, result_key(tenant_id, session_id), exc)
        return None
    return doc
