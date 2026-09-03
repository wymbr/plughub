"""masking_types.py — política de ECO por TIPO, lida do catálogo (ALW-10).

── O que este módulo responde ───────────────────────────────────────────────────

*"Este campo mascarado pode voltar para o operador, e em que forma?"*

A resposta é do TIPO (`masking.types` no config-api), e o tipo do campo já viaja
até aqui: o `menu` step publica `masked_types` no `waitingMeta` (`menu.ts`), o
bridge o coleta em `all_masked_types`, e até agora só o carimbava no evento de
analytics. Faltava consultá-lo.

── A regra: o tipo só APERTA, nunca afrouxa ────────────────────────────────────

Um campo declarado `masked:` na skill é uma declaração do AUTOR do fluxo: *este
input é segredo*. O catálogo é uma declaração do TENANT sobre o tipo de dado. Onde
as duas discordam, vence a mais restritiva — a mesma forma de `resolve_scope` e do
`core.fileMode`, e pelo mesmo motivo: a permissiva degrada mudo.

    ordem de restrição:  none  <  masked  <  plain

Logo, para campo declarado `masked`, o efetivo é `min(masked, política do tipo)`:

    tipo diz `none`    →  `none`    (aperta: nem placeholder aparece)
    tipo diz `masked`  →  `masked`  (é o que as casas já faziam)
    tipo diz `plain`   →  `masked`  (o campo vence — e o rebaixamento é LOGADO)

⚠️ `plain` não é valor morto: ele é a identidade do domínio e vale para o
`echo_to_customer` (o cliente digitou o valor e já o conhece). No lado do operador
ele só não consegue DESFAZER um `masked:` declarado no fluxo.

── O fallback é `masked`, e não o mais restritivo ──────────────────────────────

Com o config-api fora, a tentação é cair em `none` ("o mais seguro"). Errado por
duas razões: (a) `masked` já não vaza nada — é um placeholder; (b) cair em `none`
faria uma INDISPONIBILIDADE mudar o comportamento do produto (o operador perderia
até o `••••••` que hoje enxerga). Fallback tem de ser seguro **e** não ser uma
mudança de comportamento disparada por outage. E ele LOGA, sempre.

── Escopo: ECO é INPUT, nunca ARMAZENAMENTO ────────────────────────────────────

Este módulo governa o que o operador VÊ da entrada fresca. Não governa
persistência: dos cinco destinos de `redact_customer_reply`, três são
Kafka→ClickHouse, log e stream de Analytics, e quem os cobre continua sendo a
redação por `masked`. Foi neles que o vazamento de 2026-08-29 aconteceu, e
confundir os dois eixos reabriria aquele buraco por outro lado.
"""

from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger("plughub.orchestrator-bridge.masking_types")

#: Domínio de `EchoMode` (@plughub/schemas/audit.ts). Espelho — a deduplicação
#: exigiria o bridge importar TS; a paridade é imposta pelo gate.
ECHO_MODES = ("none", "masked", "plain")

#: Restrição DECRESCENTE: índice menor = mais restritivo. É a ordem que `_min`
#: usa, e é o único lugar onde ela vive.
_ORDEM = {"none": 0, "masked": 1, "plain": 2}

#: Fallback — ver o cabeçalho. Nunca `plain`, nunca `none`.
FALLBACK: str = "masked"

_TTL_S = 300
_cache: dict[str, tuple[float, dict[str, dict[str, str]]]] = {}


def _min(a: str, b: str) -> str:
    """O mais restritivo dos dois."""
    return a if _ORDEM.get(a, 0) <= _ORDEM.get(b, 0) else b


def invalidate(tenant_id: str | None = None) -> None:
    """Chamado no `config.changed` do namespace `masking`."""
    if tenant_id is None:
        _cache.clear()
    else:
        _cache.pop(tenant_id, None)


async def _fetch(config_api_url: str, tenant_id: str, http: Any) -> dict[str, dict[str, str]]:
    url = f"{config_api_url}/config/masking/types?tenant_id={tenant_id}"
    async with http.get(url, timeout=5) as resp:
        if resp.status != 200:
            raise RuntimeError(f"HTTP {resp.status}")
        doc = await resp.json()
    tipos = (doc.get("value") or {}).get("types") or []
    fora: dict[str, dict[str, str]] = {}
    for t in tipos:
        tid = t.get("id")
        if not isinstance(tid, str):
            continue
        display = (t.get("mascara") or {}).get("display") or {}
        politica: dict[str, str] = {}
        for campo in ("echo_to_customer", "echo_to_operator"):
            v = display.get(campo)
            if v in ECHO_MODES:
                politica[campo] = v
            elif v is not None:
                # Forma velha (booleano) ou valor fora do domínio: NÃO adivinha.
                # A migração é `infra/scripts/migrate_masking_display_rule.py`, e
                # herdar um booleano aqui em silêncio esconderia store não migrado.
                logger.warning(
                    "masking_types: tipo=%s %s=%r fora do domínio EchoMode — usando "
                    "o fallback %r. Store não migrado? Ver "
                    "infra/scripts/migrate_masking_display_rule.py",
                    tid, campo, v, FALLBACK,
                )
        fora[tid] = politica
    return fora


async def politica_por_tipo(
    config_api_url: str, tenant_id: str, http: Any
) -> dict[str, dict[str, str]]:
    """Catálogo `{type_id: {echo_to_*: modo}}`, cacheado por tenant (TTL 5 min)."""
    agora = time.monotonic()
    em_cache = _cache.get(tenant_id)
    if em_cache and agora - em_cache[0] < _TTL_S:
        return em_cache[1]
    try:
        tipos = await _fetch(config_api_url, tenant_id, http)
    except Exception as exc:
        # Degradação BARULHENTA, e ela nomeia o que deixa de valer.
        logger.warning(
            "masking_types: catálogo indisponível para tenant=%s (%s) — a política "
            "de ECO POR TIPO não se aplica nesta decisão; todo campo mascarado cai "
            "em %r, que é o comportamento anterior à ALW-10 e não vaza. O que se "
            "perde é o APERTO: um tipo que pede `none` vai aparecer como `%s`.",
            tenant_id, exc, FALLBACK, FALLBACK,
        )
        return {}
    _cache[tenant_id] = (agora, tipos)
    return tipos


def resolve_echo_operator(
    tipos: dict[str, dict[str, str]],
    masked_fields: set[str],
    masked_types: dict[str, str],
) -> dict[str, str]:
    """`{field_id: modo efetivo}` para os campos DECLARADOS mascarados.

    Só campos em `masked_fields` entram: a política de tipo não transforma campo
    livre em campo mascarado. Ela só decide o que fazer com o que já é segredo.
    """
    fora: dict[str, str] = {}
    for fid in sorted(masked_fields):
        tid = masked_types.get(fid)
        do_tipo = (tipos.get(tid) or {}).get("echo_to_operator") if tid else None
        if do_tipo is None:
            fora[fid] = FALLBACK
            continue
        efetivo = _min("masked", do_tipo)
        if efetivo != do_tipo:
            logger.info(
                "masking_types: campo=%s tipo=%s pede echo_to_operator=%r, mas o "
                "campo é declarado `masked` no fluxo — rebaixado para %r. O tipo "
                "APERTA, nunca afrouxa.",
                fid, tid, do_tipo, efetivo,
            )
        fora[fid] = efetivo
    return fora
