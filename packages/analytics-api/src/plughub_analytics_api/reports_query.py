"""
reports_query.py
ClickHouse query helpers for the /reports/* endpoints.

Four report helpers, all following the same pattern:
  - Accept: client, database, tenant_id, from_dt, to_dt, optional filters, page, page_size
  - Return: {"data": list[dict], "meta": {page, page_size, total, from_dt, to_dt}}

Datetime strings are formatted as 'YYYY-MM-DD HH:MM:SS' for ClickHouse comparisons.
Optional filters are injected as named ClickHouse parameters ({name:Type}) to avoid
SQL injection; only strings read from user input are parameterised.
"""
from __future__ import annotations

import asyncio
import csv
import io
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from . import sla_source, survey_catalog

logger = logging.getLogger("plughub.analytics.reports")

# ─── defaults ─────────────────────────────────────────────────────────────────

_MAX_PAGE_SIZE_JSON = 1_000
_MAX_PAGE_SIZE_CSV  = 10_000


def _default_from() -> str:
    return (datetime.utcnow() - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")


def _default_to() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def _ch_fmt(iso: str | None, upper: bool = False) -> str:
    """Converts an ISO8601 string (or relative '-Nd' offset) to ClickHouse UTC datetime.

    When upper=True and the input is a date-only string (YYYY-MM-DD), the time is
    set to 23:59:59 so the full day is included in upper-bound filters.
    """
    if not iso:
        return _default_to()
    stripped = iso.strip()
    # Relative offset: -Nd (e.g. '-7d' = 7 days ago)
    if stripped.startswith("-") and stripped.endswith("d"):
        try:
            days = int(stripped[1:-1])
            return (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            return _default_from()
    try:
        dt = datetime.fromisoformat(stripped.replace("Z", "+00:00"))
        # Date-only input (no time component): for upper bounds use end-of-day
        if upper and len(stripped) <= 10:
            dt = dt.replace(hour=23, minute=59, second=59)
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return _default_to()


def _rows_to_dicts(result: Any) -> list[dict]:
    """Converts a clickhouse_connect query result to a list of dicts."""
    cols = result.column_names
    rows = []
    for row in result.result_rows:
        d = dict(zip(cols, row))
        # Convert datetime objects to ISO strings for JSON serialisability.
        # ClickHouse returns naive datetimes (no tzinfo) but stores them as UTC.
        # We must append timezone info so JavaScript interprets them correctly
        # (without it, JS treats "2026-05-02T11:48:45" as local time, not UTC).
        for k, v in d.items():
            if isinstance(v, datetime):
                if v.tzinfo is None:
                    v = v.replace(tzinfo=timezone.utc)
                d[k] = v.isoformat()
        rows.append(d)
    return rows


def _to_csv(data: list[dict]) -> str:
    """Converts a list of dicts to a CSV string."""
    if not data:
        return ""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(data[0].keys()), lineterminator="\n")
    writer.writeheader()
    writer.writerows(data)
    return buf.getvalue()


def _clamp_page_size(page_size: int, is_csv: bool) -> int:
    limit = _MAX_PAGE_SIZE_CSV if is_csv else _MAX_PAGE_SIZE_JSON
    return max(1, min(page_size, limit))


# ─── shared count helper ──────────────────────────────────────────────────────

def _count(client: Any, sql_count: str, params: dict) -> int:
    result = client.query(sql_count, parameters=params)
    if result.result_rows:
        return int(result.result_rows[0][0])
    return 0


def _meta(page: int, page_size: int, total: int, from_dt: str, to_dt: str) -> dict:
    return {
        "page":      page,
        "page_size": page_size,
        "total":     total,
        "from_dt":   from_dt,
        "to_dt":     to_dt,
    }


# ─── Journey J3 — resolução canônica (union-find sobre journey_aliases) ────────

def _journey_resolved_map(client: Any, db: str, tenant_id: str) -> dict[str, str]:
    """
    Journey J3 — lê `journey_aliases` active=1 e devolve `{root_local → raiz_canônica}`
    (union-find com path compression + cycle guard). Só raízes que têm aresta entram
    no mapa; as demais resolvem para si mesmas. A ordem novo→antigo da tool torna
    ciclo improvável, mas o guard (`seen`) garante terminação de qualquer forma.
    Tabela pequena; degradação graciosa (erro → mapa vazio = comportamento J2).
    """
    try:
        res = client.query(
            f"SELECT source_root, canonical_root FROM {db}.journey_aliases FINAL "
            "WHERE tenant_id = {tenant_id:String} AND active = 1",
            parameters={"tenant_id": tenant_id},
        )
        edges = list(res.result_rows)
    except Exception:
        return {}

    parent: dict[str, str] = {}
    for src, canon in edges:
        if src and canon and str(src) != str(canon):
            parent[str(src)] = str(canon)

    resolved: dict[str, str] = {}

    def find(x: str) -> str:
        path: list[str] = []
        seen: set[str] = set()
        cur = x
        while cur in parent and cur not in seen:
            seen.add(cur)
            path.append(cur)
            cur = parent[cur]
        for node in path:
            resolved[node] = cur
        return cur

    for node in list(parent.keys()):
        find(node)
    return resolved


def _journey_group_expr(resolved: dict[str, str], col: str = "s.root_session_id") -> str:
    """
    SQL que mapeia `col` (root) → raiz canônica via `transform` (1 salto, pois
    `resolved` já é totalmente resolvido). Sem aliases → identidade (o próprio col).
    Valores são UUIDs da nossa própria tabela (não input do usuário) — inline seguro.
    """
    if not resolved:
        return col
    keys = ", ".join(f"'{k}'" for k in resolved.keys())
    vals = ", ".join(f"'{v}'" for v in resolved.values())
    return f"transform({col}, [{keys}], [{vals}], {col})"


def _journey_member_roots(resolved: dict[str, str], root: str) -> set[str]:
    """Conjunto de raízes locais que resolvem à MESMA journey canônica de `root`
    (para o drill: WHERE root_session_id IN membros). Inclui a própria e a canônica."""
    canonical = resolved.get(root, root)
    members = {r for r, c in resolved.items() if c == canonical}
    members.add(canonical)
    members.add(root)
    return members


def _apply_agent_scope(
    conditions: list[str],
    supervised_agent_types: "list[str] | None",
) -> bool:
    """
    Arc 9 — Mutates *conditions* in-place to add an agent_type_id IN (...) filter.

    supervised_agent_types=None  → no-op (all agent types visible)
    supervised_agent_types=[…]   → append AND agent_type_id IN ('a','b',…)
    supervised_agent_types=[]    → caller has no agent type access → caller must return empty
    """
    if supervised_agent_types is None:
        return True
    if not supervised_agent_types:
        return False
    type_list = ", ".join(f"'{t}'" for t in supervised_agent_types)
    conditions.append(f"agent_type_id IN ({type_list})")
    return True


def _agent_scope_session_join(
    db: str,
    tenant_id: str,
    supervised_agent_types: "list[str] | None",
) -> tuple[str, str]:
    """
    Arc 9 — Returns (join_sql, extra_where) for sessions queries.

    Sessions don't have agent_type_id directly — scope is applied via a
    LEFT JOIN on segments FINAL to find sessions that had at least one
    segment from a supervised agent type.

    Returns ("", "") when no filter is needed.
    Returns (join_sql, "AND _scope.session_id IS NOT NULL") when filtering.
    Returns ("", "AND 1=0") when supervised_agent_types=[] (no access).
    """
    if supervised_agent_types is None:
        return "", ""
    if not supervised_agent_types:
        return "", "AND 1=0"
    type_list = ", ".join(f"'{t}'" for t in supervised_agent_types)
    join_sql = f"""
        LEFT JOIN (
            SELECT DISTINCT session_id
            FROM {db}.segments FINAL
            WHERE tenant_id = {{tenant_id:String}}
              AND agent_type_id IN ({type_list})
        ) AS _scope ON _scope.session_id = s.session_id"""
    return join_sql, "AND _scope.session_id IS NOT NULL"


def _apply_pool_scope(
    conditions: list[str],
    accessible_pools: "list[str] | None",
    column: str = "pool_id",
) -> bool:
    """
    Mutates *conditions* in-place to add a pool_id IN (...) filter when needed.

    *column* existe porque a D14 (iii) passou a escopar pelo pool do SEGMENTO
    de espera (`w.pool_id`), e não pelo da sessão. Default preservado para não
    tocar nenhum dos call sites existentes.

    Returns False if the caller has NO access to any pool (empty whitelist),
    which means the caller should short-circuit and return an empty result
    without hitting ClickHouse.

    accessible_pools=None  → no-op (all pools visible, typical for open-access)
    accessible_pools=[…]   → append AND pool_id IN ('a','b',…)
    accessible_pools=[]    → caller has no pool access → caller must return empty
    """
    if accessible_pools is None:
        return True   # unrestricted
    if not accessible_pools:
        return False  # no pools allowed
    # pool_ids come from a verified JWT — safe to inline as string literals
    pool_list = ", ".join(f"'{p}'" for p in accessible_pools)
    conditions.append(f"{column} IN ({pool_list})")
    return True


def _session_scope_clause(
    db: str,
    accessible_pools: "list[str] | None",
    alias: str = "s",
) -> str:
    """
    Predicado ABAC de escopo para a linha de SESSÃO. Devolve "" quando não há
    restrição (o chamador simplesmente não acrescenta condição).

    ── Por que existe, e por que é UM lugar só (F1b, 2026-08-14) ─────────────────
    A regra estava copiada inline em quatro endpoints, sempre como
        (s.pool_id IN (…) OR s.pool_id = '')
    e as quatro cópias respondiam à pergunta errada. `sessions.pool_id` nunca foi
    "quem atendeu": era o que escrevesse por último e, desde o carimbo
    first-write-wins, é o pool de ENTRADA. Autorizar por ele faz o supervisor
    perder contatos que os agentes DELE atenderam — medido em `tenant_demo`
    2026-08-14: dos 67 contatos cujo pool de entrada difere do de atendimento,
    **52** sairiam do escopo de `admin@plughub.local` e `operator@plughub.local`
    (36 `aprovacao_credito`←`limite_processo` · 14 `retencao_humano`←`sac_ia` ·
    2 `formfill_demo`←`formfill_demo_ia`). Os 14 do meio são contatos de cliente.

    O predicado correto é a UNIÃO de três razões para uma sessão ser minha:
      1. ela ENTROU por um pool meu   → `pool_id IN (…)`
      2. ela ainda não tem pool       → `pool_id = ''` (ver o contato desde a chegada)
      3. um pool meu PARTICIPOU dela  → existe segmento meu (é o "atendido por")
    A (3) é a que faltava, e é estritamente AMPLIADORA: ninguém perde visibilidade
    em relação ao que via antes — o que se recupera são sessões que o acidente do
    "último escritor" às vezes mostrava e às vezes não.

    Não altera `_apply_pool_scope`, que é compartilhada com `_fetch_pools_volume`
    e `_fetch_session_complexity` e cujo `pool_id` é de OUTRA tabela em cada uso —
    o precedente registrado na F2 (§Achado 6) é conserto por ponto, não por mudança
    da função comum.
    """
    if not accessible_pools:
        return ""
    pool_list = ", ".join(f"'{p}'" for p in accessible_pools)
    return (
        f"({alias}.pool_id IN ({pool_list})"
        f" OR {alias}.pool_id = ''"
        f" OR {alias}.session_id IN ("
        f"SELECT session_id FROM {db}.segments FINAL"
        " WHERE tenant_id = {tenant_id:String}"
        f" AND pool_id IN ({pool_list})))"
    )


# Quality substrate isolation (ADR adr-quality-substrate-isolation) — domínio de origin.
_VALID_ORIGINS = {"live", "import", "reeval"}


def _apply_origin_scope(
    conditions: list[str],
    origin: "str | list[str]" = "live",
    alias: str = "",
) -> None:
    """Substrate isolation (ADR) — mutates *conditions* in-place to add an
    `origin IN (...)` filter on the substrate tables (sessions/segments/messages).

    Default `origin='live'` is the correctness guarantee: any caller that does NOT
    explicitly ask for another origin sees ONLY production. Quality/curation reports
    pass `import`/`reeval` explicitly. Values are a controlled enum → safe to inline
    as string literals (same posture as pool/agent scope). Invalid values are dropped;
    an empty/all-invalid selection falls back to ['live'] (never unrestricted).

    alias: column prefix for queries that alias the table (e.g. "s." → "s.origin").
    """
    origins = [origin] if isinstance(origin, str) else list(origin)
    origins = [o for o in origins if o in _VALID_ORIGINS] or ["live"]
    col = f"{alias}origin" if alias else "origin"
    lst = ", ".join(f"'{o}'" for o in origins)
    conditions.append(f"{col} IN ({lst})")


# ─── E2f: o que conta como CONTATO de cliente ────────────────────────────────


def _apply_contact_scope(
    conditions: list[str],
    internal_pools: "frozenset[str] | set[str] | None" = None,
    alias: str = "",
) -> None:
    """Mutates *conditions* in-place com a regra INTEIRA de "isto é um contato".

    Nomeia num lugar só duas exclusões que respondem à mesma pergunta e viviam
    separadas (uma copiada literalmente em 3 queries, a outra inexistente):

    1. **Sessão de hook sem canal físico** (NPS inline, especialista de conferência).
       Não pode ser `channel != ''` sozinho: `parse_routed` escreve `channel=''` e,
       no ReplacingMergeTree, essa linha sobrescreve a do `parse_inbound` — sessões
       ATIVAS sumiriam logo após o roteamento. Por isso ativas passam sempre; só as
       FECHADAS precisam de canal (as de hook fecham com `channel=''`).

    2. **Sessão de pool INTERNO** (E2f) — o wrap-up destacado nasce como sessão
       própria de canal `webhook`, com `customer_id` herdado do contato de origem.
       `channel` não é vazio, então a regra (1) não a pega. O discriminador é o
       POOL (`pools.purpose == "internal"`, resolvido pelo `pools_client`).

    **Segregação, não supressão:** relatórios AGRUPADOS POR POOL não devem chamar
    este helper — lá o pool interno é uma linha legítima e desejada (o TMA do pool
    de wrap-up É o tempo de ACW). O helper serve os totais de ATENDIMENTO e as
    listas de contato do cliente.

    `internal_pools` vem do `pools_client.fetch_internal_pools` (async, cache 60s,
    degradação barulhenta). Vazio/None ⇒ só a regra (1) — o helper nunca inventa
    exclusão, e o cliente é quem loga quando não conseguiu resolver o conjunto.
    """
    ch  = f"{alias}channel" if alias else "channel"
    cl  = f"{alias}closed_at" if alias else "closed_at"
    conditions.append(f"({ch} != '' OR {cl} IS NULL)")
    if internal_pools:
        col = f"{alias}pool_id" if alias else "pool_id"
        # pool_ids vêm do agent-registry (não do request) — seguro inlinear.
        lst = ", ".join(f"'{p}'" for p in sorted(internal_pools))
        conditions.append(f"{col} NOT IN ({lst})")


async def _internal_pools_for(tenant_id: str) -> frozenset[str]:
    """Conjunto de pools internos do tenant (E2f). Ver `pools_client` p/ degradação."""
    from .config import get_settings
    from .pools_client import fetch_internal_pools

    return await fetch_internal_pools(get_settings().agent_registry_url, tenant_id)


def _contact_only_predicate(
    internal_pools: "frozenset[str] | set[str] | None", alias: str = "",
) -> str:
    """Expressão booleana "esta linha é um contato de cliente" (só a regra do POOL).

    É a MESMA regra (2) de `_apply_contact_scope`, na forma de expressão em vez de
    condição de `WHERE` — para poder ser CONTADA numa listagem que a relaxou. Sem
    ela, `scope=all` faria a tela ter um único número misturando contatos e ruído
    operacional, que é exatamente o que a E2f existe para impedir.

    Sem pools internos resolvidos ⇒ `1`: o helper nunca inventa exclusão (mesma
    postura do `_apply_contact_scope`; quem loga a falha de resolução é o cliente).
    """
    if not internal_pools:
        return "1"
    col = f"{alias}pool_id" if alias else "pool_id"
    lst = ", ".join(f"'{p}'" for p in sorted(internal_pools))
    return f"{col} NOT IN ({lst})"


# ── Direção do acesso (ADR histórico-unificado D8) — UMA expressão, dois usos ──
#
# A direção é **derivada, nunca armazenada** (D8): `spawn_reason` foi criado para
# isto, e o canal desempata o caso "ninguém me criou".
#
#   `collect`             → outbound  (a plataforma procurou o cliente)
#   `trigger` / `delegate`→ interno   (maquinaria criou a sessão)
#   ausente/NULL          → canal decide: `webhook` é máquina falando com máquina
#                           (interno); qualquer outro é o cliente chegando (inbound)
#   qualquer outro valor  → `''` (NÃO classificado)
#
# **O último ramo é o que torna esta expressão honesta.** Um `spawn_reason` novo
# não cai num balde plausível: sai vazio, a UI mostra `—`, e o filtro por direção
# não o reivindica. Valor ausente denuncia; valor plausível esconde.
#
# ⚠️ **Por que a expressão é UMA e mora aqui, e não na UI.** Até a F4 esta regra
# vivia em TypeScript (`contactDirection`, `modules/contacts/types.ts`), enquanto o
# filtro que a F3 pedia teria de ser SQL — duas implementações da MESMA pergunta,
# que é o defeito que este arco existe para fechar um nível acima. Agora a coluna
# `direction` e o `WHERE` do filtro são **literalmente a mesma string**: divergir
# entre "o que a linha diz" e "o que o filtro devolve" deixou de ser possível.
# Mesma postura do `_mark_internal_rows` logo abaixo: quem sabe a resposta decide;
# quem exibe apenas exibe.
SESSION_DIRECTIONS = ("inbound", "outbound", "internal")

# Canal EFETIVO da sessão. Não é `s.channel` cru: `parse_routed` escreve `channel=''`
# e, no ReplacingMergeTree, essa linha sobrescreve a do `parse_inbound` — uma sessão
# ATIVA de webhook apareceria com canal vazio e cairia em `inbound`. O JOIN `_ch`
# recupera qualquer linha não-vazia da mesma sessão, e por isso ele é **pré-requisito
# desta expressão** (ver `_CH_JOIN_SQL`).
_CHANNEL_EXPR = "COALESCE(NULLIF(s.channel, ''), _ch.channel_v)"

# `ifNull` em toda condição de propósito: `spawn_reason` é `Nullable(String)` e uma
# condição NULL dentro de `multiIf` não é "falsa" — contamina o resultado inteiro.
_DIRECTION_EXPR = (
    "multiIf("
    "ifNull(s.spawn_reason, '') = 'collect', 'outbound',"
    " ifNull(s.spawn_reason, '') IN ('trigger', 'delegate'), 'internal',"
    " ifNull(s.spawn_reason, '') != '', '',"
    f" ifNull({_CHANNEL_EXPR}, '') = 'webhook', 'internal',"
    " 'inbound')"
)


def _ch_join_sql(db: str) -> str:
    """JOIN de recuperação de canal — o que `_CHANNEL_EXPR` exige para existir.

    Extraído de `_joins` (onde já vivia) porque a query de CONTAGEM também passou a
    precisar dele: sem o join ali, filtrar por direção contaria sobre um canal cru
    que a query de listagem não usa, e as duas responderiam diferente para a mesma
    sessão ativa. O alias é sufixado (`_v`) — ver o aviso de `ILLEGAL_AGGREGATION`.
    """
    return f"""
        LEFT JOIN (
            SELECT session_id, anyIf(channel, channel != '') AS channel_v
            FROM {db}.sessions
            WHERE tenant_id = {{tenant_id:String}} AND channel != ''
            GROUP BY session_id
        ) AS _ch ON _ch.session_id = s.session_id"""


def _mark_internal_rows(
    rows: list[dict], internal_pools: "frozenset[str] | set[str] | None",
) -> list[dict]:
    """Carimba `is_internal` em cada linha (ADR §7, fatia 1b). Muta e devolve.

    **Não é fato novo — é o veredicto que o backend já computou e descartava.** O
    conjunto de pools internos é resolvido aqui a cada request (`_internal_pools_for`)
    para filtrar; até esta fatia, a linha atravessava a fronteira com `pool_id` e sem
    a resposta, e a UI não tinha como distinguir contato de ruído operacional.
    Deixá-la re-derivar poria o discriminador da E2f numa segunda casa — o defeito
    que o "one source per domain" existe para impedir. Quem sabe a resposta decide;
    quem exibe apenas exibe.

    Vale para o CSV de graça: `_to_csv` tira as colunas das chaves da 1ª linha.
    """
    known = set(internal_pools or ())
    for r in rows:
        r["is_internal"] = r.get("pool_id") in known
    return rows


def _sessions_meta(
    page: int, page_size: int, total: int, total_contacts: int,
    from_dt: str, to_dt: str, scope: str = "contacts",
    internal_pools_known: int = 0,
    window_applied: bool = True,
) -> dict:
    """`_meta` + os DOIS domínios de contagem (ADR wrapup-detached-pull §7.2, item 2).

    `total` dimensiona a PAGINAÇÃO (linhas realmente listadas); `total_contacts`
    é o que a tela mostra no cabeçalho, sempre no escopo `contacts` mesmo com a
    tabela expandida. Nunca um número só somando os dois domínios.

    `internal_pools_known` = tamanho do conjunto que classificou estas linhas. É
    um FATO, não um veredicto de saúde — de propósito. Um booleano "resolvido"
    seria mentira nos dois sentidos: `frozenset()` vazio significa tanto "registry
    não respondeu" quanto "este tenant não tem pool interno", e o `pools_client`
    não distingue os casos (ele já grita no log, na camada certa, quando falha).
    Com o número, a UI decide sem afirmar o que não sabe: `0` ⇒ não há como
    distinguir nada, então não prometer o recurso na tela.
    """
    m = _meta(page, page_size, total, from_dt, to_dt)
    m["total_contacts"] = total_contacts
    m["total_internal"] = max(0, total - total_contacts)
    m["scope"]          = scope
    m["internal_pools_known"] = internal_pools_known
    # `window_applied` (2026-08-14) — mesmo marcador da F2 em `/reports/segments`, e
    # pela mesma razão: `from_dt`/`to_dt` são publicados SEMPRE, inclusive nos ramos
    # que não filtraram por eles (drill de processo, filhas de uma sessão). Sem o
    # marcador o cabeçalho afirma um recorte que não houve, e quem o lê conclui que
    # a lista está cortada por período. Aditivo — não quebra leitor existente.
    m["window_applied"] = window_applied
    return m


# ─── /reports/sessions ────────────────────────────────────────────────────────

async def query_sessions_report(
    client:    Any,
    database:  str,
    tenant_id: str,
    from_dt:   str | None = None,
    to_dt:     str | None = None,
    *,
    channel:                str | None       = None,
    outcome:                str | None       = None,
    close_reason:           str | None       = None,
    # `pool_id` é **"atendido por"**: subconsulta em `segments` (qualquer segmento
    # daquele pool). O nome não diz isso e não vai passar a dizer nesta fase — o que
    # muda é que ele deixa de ser o ÚNICO, e a tela nomeia os dois (D12).
    pool_id:                str | None       = None,
    # F3.2 — **"entrou por"**: a PORTA. Igualdade sobre `sessions.pool_id`, que desde
    # a F1b é first-write-wins e significa UMA coisa (o JOIN `_pool` de fallback foi
    # podado junto). É parâmetro NOVO de propósito, não um modo do `pool_id`: as duas
    # perguntas são diferentes, e um enum faria a mesma chave significar duas coisas —
    # exatamente o defeito que este arco existe para fechar, um nível acima.
    entry_pool_id:          str | None       = None,
    # F3 (resíduo) — direção do ACESSO, sobre a mesma expressão que devolve a coluna
    # `direction`. Ver `_DIRECTION_EXPR`. Valor fora de `SESSION_DIRECTIONS` é
    # IGNORADO aqui? Não: é recusado na borda (`pattern` do Query), porque um filtro
    # que não filtra devolve a lista inteira e parece funcionar — foi exatamente o
    # que o seletor «Inbound/Outbound» removido na F3 fazia.
    direction:              str | None       = None,
    session_id:             str | None       = None,
    root_session_id:        str | None       = None,
    # Journey T5: sessões que NASCERAM desta journey mas pertencem a OUTRA — as arestas
    # que atravessam a fronteira (`journey: new`). É o que permite à Vista Processos
    # mostrar "este atendimento originou o processo X" sem expandir a subárvore de X
    # (expandir desfaria o corte que o operador pediu).
    spawned_from_root:      str | None       = None,
    agent_id:               str | None       = None,
    insight_category:       str | None       = None,
    insight_tags:           list[str] | None = None,
    accessible_pools:       list[str] | None = None,
    supervised_agent_types: list[str] | None = None,
    ani:                    str | None       = None,
    dnis:                   str | None       = None,
    status:                 str | None       = None,
    origin:                 "str | list[str]" = "live",
    # ADR wrapup-detached-pull §7 — visibilidade ≠ contagem. `contacts` (default) é
    # bit-a-bit o comportamento que a E2f fechou; `all` acrescenta as sessões de pool
    # interno (wrap-up, dispatch) como LINHAS, sem nunca entrar em `total_contacts`.
    # Nenhum endpoint de AGREGADO aceita este parâmetro (guardrail §7.2 item 1).
    scope:                  str              = "contacts",
    # Timeline do contato (S1): filhas de UM SALTO. Ver `_fetch_sessions`.
    origin_session_id:      str | None       = None,
    page:      int = 1,
    page_size: int = 100,
) -> dict:
    since = _ch_fmt(from_dt) if from_dt else _default_from()
    until = _ch_fmt(to_dt, upper=True) if to_dt else _default_to()
    if accessible_pools is not None and not accessible_pools:
        return {"data": [], "meta": _sessions_meta(page, page_size, 0, 0, since, until, scope)}
    if supervised_agent_types is not None and not supervised_agent_types:
        return {"data": [], "meta": _sessions_meta(page, page_size, 0, 0, since, until, scope)}
    internal_pools = await _internal_pools_for(tenant_id)
    try:
        return await asyncio.to_thread(
            _fetch_sessions, client, database, tenant_id, since, until,
            channel, outcome, close_reason, pool_id, session_id,
            agent_id, insight_category, insight_tags, accessible_pools,
            supervised_agent_types, page, page_size,
            ani, dnis, status, origin, root_session_id, spawned_from_root,
            internal_pools, scope, origin_session_id,
            entry_pool_id=entry_pool_id,
            direction=direction,
        )
    except Exception as exc:
        logger.warning("query_sessions_report failed tenant=%s: %s", tenant_id, exc)
        return {
            "data": [],
            # O marcador vale também no ramo de erro: um cabeçalho que afirma
            # `window_applied=true` numa resposta vazia de drill somaria uma segunda
            # explicação falsa ("está vazio porque o período recortou") ao lado do
            # `data_unavailable`, que é a verdadeira.
            "meta": _sessions_meta(
                page, page_size, 0, 0, since, until, scope,
                window_applied=not (root_session_id or origin_session_id),
            ),
            "error": "data_unavailable",
        }


def _fetch_sessions(
    client: Any, db: str, tenant_id: str,
    since: str, until: str,
    channel: str | None, outcome: str | None, close_reason: str | None, pool_id: str | None,
    session_id: str | None, agent_id: str | None,
    insight_category: str | None, insight_tags: list[str] | None,
    accessible_pools: list[str] | None,
    supervised_agent_types: list[str] | None,
    page: int, page_size: int,
    ani: str | None = None, dnis: str | None = None,
    status: str | None = None,
    origin: "str | list[str]" = "live",
    root_session_id: str | None = None,
    spawned_from_root: str | None = None,
    internal_pools: "frozenset[str] | None" = None,
    scope: str = "contacts",
    origin_session_id: str | None = None,
    entry_pool_id: str | None = None,
    direction: str | None = None,
) -> dict:
    conditions = ["s.tenant_id = {tenant_id:String}"]
    # S1 — o fetch das filhas de UMA sessão IGNORA a janela, como o `_fetch_journeys`
    # já faz no fetch direcionado. A filha nasce depois do pai: um wrap-up que começa
    # às 23:59:58 de `to_dt` cairia fora da janela do pai e a timeline diria "não
    # originou nada" — ausência que se lê como fato, e não como recorte.
    #
    # ── `root_session_id` entrou na isenção em 2026-08-14, e era DEFEITO VIVO ────
    # O CHANGELOG da F2 afirmava que este endpoint "já tinha" a isenção; tinha, mas
    # só para `origin_session_id`. Medido (`probe_journeys_window_applied.sh`): o
    # drill do processo `d62d7121…` devolve **4** sessões com a janela default e
    # **0** com uma janela que o exclui. Não é truncar — é **esvaziar**: abrir na
    # Vista Processos um processo mais velho que o período mostraria um processo sem
    # nenhuma sessão, e a tela não tem como distinguir isso de "processo vazio".
    #
    # A razão é a mesma das outras duas isenções, e está escrita logo abaixo para o
    # escopo de pool interno: **pedir UM processo não é listar**. O operador abriu
    # AQUELE processo; recortá-lo por um período que ele não escolheu para esta
    # leitura responde outra pergunta.
    #
    # `session_id` deliberadamente NÃO entrou: pela mesma lógica ele também deveria
    # ser isento, mas o `probe_segments_journey_window` da F2 usa exatamente
    # "session_id + janela absurda = 0" como TESTEMUNHA de que a janela funciona.
    # Mudar isso junto trocaria um conserto medido por dois, um deles sem medição e
    # derrubando o discriminador de outro gate. Registrado no TODO.md.
    if not origin_session_id and not root_session_id:
        conditions.append(f"s.opened_at >= '{since}'")
        conditions.append(f"s.opened_at < '{until}'")
    # E2f — o que conta como contato: sessão de hook sem canal + sessão de pool
    # interno (wrap-up destacado) ficam de fora. Ver `_apply_contact_scope`.
    #
    # EXCETO no lookup explícito por `session_id`: o escopo filtra LISTAGENS, não
    # uma busca por id. Quem pede uma sessão pelo id (drill, deep-link, suporte)
    # está pedindo AQUELA sessão — devolver vazio seria esconder o que foi pedido.
    #
    # `scope="all"` (ADR §7) relaxa **só a regra do POOL**, passando `None` no lugar
    # do conjunto. A regra do CANAL continua valendo em qualquer escopo, e isso é
    # deliberado: ela não classifica "interno vs contato" — ela protege da linha que
    # o `parse_routed` escreve com `channel=''` e que, no ReplacingMergeTree,
    # sobrescreve a do `parse_inbound`. Relaxá-la faria sessões ATIVAS duplicarem na
    # tela. Consequência a nomear na UI: hook que roda NA CONFERÊNCIA (NPS inline)
    # não tem sessão própria e continua invisível mesmo com `scope=all` — o toggle
    # mostra sessão de pool interno, não "tudo que é interno".
    #
    # Fatia 4 — `root_session_id` (drill de UMA journey) é ISENTO, como o `session_id`
    # e pela mesma razão: escopo filtra LISTAGEM, e isto não é listagem. O operador
    # abriu AQUELE processo; esconder dele a sessão de wrap-up que pertence ao
    # processo seria mentir sobre a composição do que ele pediu para ver. Nenhuma
    # contagem sai daqui — a agregação do processo é do card (`_fetch_journeys`), que
    # segue excluindo pool interno do `session_count` e reporta o interno à parte.
    #
    # S1 (timeline do contato) — `origin_session_id` é a TERCEIRA isenção, pela mesma
    # razão das duas anteriores: pedir as filhas de UMA sessão não é listar. Esconder
    # dali a sessão de wrap-up seria responder "este contato não originou nada" a quem
    # perguntou o que ele originou. É de propósito a aresta de UM SALTO e não a journey:
    # `root_session_id` traria o processo inteiro e, em journey multi-contato, penduraria
    # neste contato as filhas do IRMÃO.
    _scope_pools = internal_pools if scope != "all" else None
    if not session_id and not root_session_id and not origin_session_id:
        _apply_contact_scope(conditions, _scope_pools, alias="s.")
    params: dict = {"tenant_id": tenant_id}

    if origin_session_id:
        conditions.append("s.origin_session_id = {origin_session_id:String}")
        params["origin_session_id"] = origin_session_id

    if session_id:
        conditions.append("s.session_id = {session_id:String}")
        params["session_id"] = session_id
    # Journey J2/J3 drill — sessões-membro de uma journey. Em J3, o root pedido pode
    # ser a raiz canônica de um merge: expande para TODAS as raízes locais que
    # resolvem à mesma journey (union-find sobre journey_aliases). Sem merges → só a
    # própria raiz (comportamento J2).
    if root_session_id:
        _resolved = _journey_resolved_map(client, db, tenant_id)
        _members = _journey_member_roots(_resolved, root_session_id)
        _member_list = ", ".join(f"'{m}'" for m in _members)
        conditions.append(f"s.root_session_id IN ({_member_list})")
    # Journey T5 — as arestas que ATRAVESSAM a fronteira: sessões cujo PAI é desta journey
    # mas que pertencem a OUTRA (nasceram com `journey: new`). Proveniência atravessa,
    # pertença não — e é justamente esse par que a Vista Processos precisa para mostrar
    # "este atendimento originou o processo X" sem expandir a subárvore de X (expandir
    # desfaria o corte que o operador pediu ao usar `journey: new`).
    #
    # A subquery pega os session_id da journey; o `NOT IN` exclui quem continua nela.
    if spawned_from_root:
        _resolved_sp   = _journey_resolved_map(client, db, tenant_id)
        _members_sp    = _journey_member_roots(_resolved_sp, spawned_from_root)
        _member_list_sp = ", ".join(f"'{m}'" for m in _members_sp)
        conditions.append(
            f"""s.origin_session_id IN (
                    SELECT session_id FROM {db}.sessions FINAL
                    WHERE tenant_id = {{tenant_id:String}}
                      AND root_session_id IN ({_member_list_sp})
                )
                AND s.root_session_id NOT IN ({_member_list_sp})"""
        )
    if channel:
        # For active sessions parse_routed may have set channel='' — include them by also
        # checking non-FINAL rows. For closed sessions s.channel is authoritative.
        conditions.append(
            f"(s.channel = {{channel:String}} OR"
            f" (s.closed_at IS NULL AND EXISTS ("
            f"  SELECT 1 FROM {db}.sessions"
            f"  WHERE tenant_id = s.tenant_id AND session_id = s.session_id"
            f"  AND channel = {{channel:String}}"
            f" )))"
        )
        params["channel"] = channel
    if outcome:
        conditions.append("s.outcome = {outcome:String}")
        params["outcome"] = outcome
    if close_reason:
        conditions.append("s.close_reason = {close_reason:String}")
        params["close_reason"] = close_reason
    if pool_id:
        # pool_id changes per segment (routing + specialists + conference).
        # Query via segments to find any session where ANY segment belonged to this pool.
        conditions.append(
            f"s.session_id IN (SELECT session_id FROM {db}.segments FINAL"
            " WHERE tenant_id = {tenant_id:String} AND pool_id = {pool_id:String})"
        )
        params["pool_id"] = pool_id

    # F3.2 — `entrou por` (a PORTA), sobre a coluna da PRÓPRIA sessão.
    #
    # Os dois filtros são compostos por AND e isso é desejado: um contato que entrou
    # no `sac_ia` e terminou no humano casa em `entry_pool_id=sac_ia` E em
    # `pool_id=retencao_humano`, e é justamente essa diferença que a F1b tornou
    # visível (medido: 12 contatos de cliente webchat que o filtro único escondia).
    #
    # ⚠️ NÃO reusar o `_session_scope_clause` aqui: aquele é o predicado de ABAC
    # (união de três razões para a sessão ser minha) e é ampliador; este é o filtro
    # que o operador pediu, e tem de ser estrito.
    if entry_pool_id:
        conditions.append("s.pool_id = {entry_pool_id:String}")
        params["entry_pool_id"] = entry_pool_id

    # F3 (resíduo) — direção do ACESSO (D8). A MESMA expressão que sai na coluna
    # `direction`; ver `_DIRECTION_EXPR`. Note o que ela NÃO faz: sessão de
    # `spawn_reason` desconhecido sai `''` e não é reivindicada por nenhuma das três
    # direções — logo `Σ(inbound, outbound, internal) ≤ total`, e a diferença é
    # exatamente a população não classificada. É assim que o gate a conta.
    if direction:
        conditions.append(f"{_DIRECTION_EXPR} = {{direction:String}}")
        params["direction"] = direction

    # Pool-scope access filter (Arc 7c) — predicado ÚNICO, ver _session_scope_clause.
    # (Era inline aqui e em mais 3 endpoints; virou função na F1b porque o carimbo
    # `entrou por` fez as 4 cópias autorizarem pelo fato errado.)
    _scope = _session_scope_clause(db, accessible_pools)
    if _scope:
        conditions.append(_scope)
    # accessible_pools=None → no restriction; accessible_pools=[] → short-circuit in async wrapper

    # agent_id filter — requires subquery against segments table
    if agent_id:
        conditions.append(
            f"s.session_id IN (SELECT session_id FROM {db}.segments FINAL"
            " WHERE tenant_id = {{tenant_id:String}} AND participant_id = {{agent_id:String}})"
        )
        params["agent_id"] = agent_id

    # insight_category filter — requires subquery against contact_insights table
    if insight_category:
        conditions.append(
            f"s.session_id IN (SELECT session_id FROM {db}.contact_insights FINAL"
            " WHERE tenant_id = {{tenant_id:String}} AND category = {{insight_category:String}})"
        )
        params["insight_category"] = insight_category

    # insight_tags filter — each tag must be present (AND semantics)
    if insight_tags:
        for i, tag in enumerate(insight_tags):
            tag_key = f"insight_tag_{i}"
            conditions.append(
                f"s.session_id IN (SELECT session_id FROM {db}.contact_insights FINAL"
                f" WHERE tenant_id = {{tenant_id:String}} AND has(tags, {{{tag_key}:String}}))"
            )
            params[tag_key] = tag

    # ANI/DNIS filters — partial match (LIKE) for usability
    if ani:
        conditions.append("s.ani LIKE {ani_like:String}")
        params["ani_like"] = f"%{ani}%"
    if dnis:
        conditions.append("s.dnis LIKE {dnis_like:String}")
        params["dnis_like"] = f"%{dnis}%"

    # Arc 19: session status filter (active | suspended | closed).
    # NULL status (pre-Arc-19 closed sessions) are treated as 'closed' for query purposes.
    if status:
        if status == "closed":
            conditions.append("(s.status = {status:String} OR s.status IS NULL)")
        else:
            conditions.append("s.status = {status:String}")
        params["status"] = status

    # Substrate isolation (ADR): default 'live' (produção); UI/quality passam outra origem.
    _apply_origin_scope(conditions, origin, alias="s.")

    where = " AND ".join(conditions)

    # Arc 9 — agent scope: sessions that had at least one segment from a supervised agent type
    _agent_join, _agent_where = _agent_scope_session_join(db, tenant_id, supervised_agent_types)
    if _agent_where:
        where = f"{where} {_agent_where}"

    offset = (page - 1) * page_size

    # Duas contagens numa passada só (ADR §7.2 item 2): `total` pagina o que está
    # LISTADO; `total_contacts` é o cabeçalho, sempre no domínio de contato. Em
    # `scope=contacts` os dois coincidem por construção — o `where` já excluiu o
    # pool interno —, e essa igualdade é o invariante que prova que o default ficou
    # bit-a-bit o de antes.
    _contact_expr = _contact_only_predicate(internal_pools, alias="s.")
    # O JOIN `_ch` entra na CONTAGEM quando (e só quando) o filtro de direção está
    # ativo: a expressão de direção o exige (canal efetivo, não cru). É pré-agregado
    # por `session_id`, então não multiplica linha — a contagem não muda por ele.
    _counts_join = f"{_agent_join}{_ch_join_sql(db) if direction else ''}"
    _counts = client.query(
        f"SELECT count(), countIf({_contact_expr}) "
        f"FROM {db}.sessions AS s FINAL {_counts_join} WHERE {where}",
        parameters=params,
    )
    if _counts.result_rows:
        total          = int(_counts.result_rows[0][0])
        total_contacts = int(_counts.result_rows[0][1])
    else:
        total = total_contacts = 0

    # ClickHouse 23.8 does NOT support correlated subqueries with outer-query aliases
    # (e.g. "WHERE tenant_id = s.tenant_id") in the SELECT clause — it raises:
    #   Code 47: Missing columns 's.session_id' 's.tenant_id'
    #
    # Fix: use pre-aggregated LEFT JOINs instead of correlated subqueries.
    # Each JOIN is scoped to {tenant_id:String} so it remains efficient.
    # handle_time_ms is a plain COALESCE over current-row columns — no subquery needed.
    #
    # Fallback strategy:
    #   Tier 1: full JOINs + s.ani / s.dnis
    #   Tier 2: full JOINs + NULL ani/dnis  (ANI/DNIS columns not yet migrated)
    #   Tier 3: bare sessions query          (segments/agent_events tables absent)

    # Arc 9: prepend agent scope JOIN (empty string when no restriction)
    #
    # ⚠️ NUNCA dê ao alias do agregado o MESMO NOME da coluna que o `WHERE` filtra.
    # O ClickHouse resolve a referência do WHERE para o ALIAS (o agregado) e recusa com
    # `Code 184 ILLEGAL_AGGREGATION`. Escrever
    #     SELECT anyIf(channel, channel != '') AS channel ... WHERE channel != ''
    # derrubava os tiers 1 e 2 **sempre** — e, como o fallback era MUDO, o endpoint
    # respondia 200 pelo tier 3 (bare), com `segment_count: 0` fixo e colunas ausentes.
    # O "Segs: 0" que aparecia em todas as telas nunca foi um zero: era coluna que a
    # query não trazia. Por isso os aliases abaixo são sufixados (`_v`).
    _joins = f"""{_agent_join}
        -- channel recovery: any non-empty channel row for this session.
        -- (extraído para `_ch_join_sql` na F4 — a query de CONTAGEM passou a precisar
        --  do mesmo join para o filtro de direção; duas cópias divergiriam no caso
        --  exato em que o join existe para não mentir: a sessão ATIVA.)
{_ch_join_sql(db)}
        -- (removido 2026-08-14, F1b) O JOIN `_pool`, que recuperava o pool do
        -- primeiro segmento primário quando `s.pool_id` vinha vazio.
        --
        -- Ele existia porque a coluna não tinha significado: era o que escrevesse
        -- por último, e às vezes nada. Com o carimbo first-write-wins no ingest
        -- (`consumer.py::_learn_session_identity`) ela passa a significar UMA coisa
        -- — o pool de ENTRADA — e o fallback passaria a significar OUTRA: o pool de
        -- quem ATENDEU. Duas fontes para a mesma célula, que é o defeito que a fase
        -- existe para fechar, um nível acima.
        --
        -- Medido antes de podar (`probe_entry_pool_base.sh`, 2026-08-14): a coluna
        -- está vazia em **1** de 407 sessões do tenant. É essa 1 que passa a mostrar
        -- ausência em vez de um pool derivado de outro fato — ausência honesta.
        -- O filtro por pool NÃO passa por aqui: ele já é a subconsulta em `segments`
        -- ("atendido por", linha ~586), e continua sendo.
        -- outcome recovery: most-recent closed segment outcome
        LEFT JOIN (
            SELECT session_id, argMax(outcome, ended_at) AS outcome_v
            FROM {db}.segments FINAL
            WHERE tenant_id = {{tenant_id:String}}
              AND outcome IS NOT NULL AND outcome != ''
            GROUP BY session_id
        ) AS _seg_out ON _seg_out.session_id = s.session_id
        -- (removido) 3º nível de fallback de outcome sobre `agent_events`.
        -- A tabela era substrato derivado que duplicava `segments`, e neste
        -- caminho específico era inerte: o `runtime.ts` — único produtor vivo do
        -- agent_done aceito pelo parser — nunca mandou `outcome`, então o JOIN
        -- rendia NULL em toda sessão live. Só produzia valor para sessões vindas
        -- do quality-ingest, que hoje escrevem `segments` de qualquer forma.
        -- segment count per session
        LEFT JOIN (
            SELECT session_id, count() AS cnt
            FROM {db}.segments FINAL
            WHERE tenant_id = {{tenant_id:String}}
            GROUP BY session_id
        ) AS _sc ON _sc.session_id = s.session_id
        -- Fase E.2: início do primeiro segmento da sessão. Usado para o tempo decorrido
        -- total de webhook (closed_at − primeiro segmento), evitando o opened_at que é
        -- re-carimbado a cada resume.
        LEFT JOIN (
            SELECT session_id, min(started_at) AS first_started_at
            FROM {db}.segments FINAL
            WHERE tenant_id = {{tenant_id:String}}
            GROUP BY session_id
        ) AS _segdur ON _segdur.session_id = s.session_id
        -- ── D9: TEMPO-AGENTE (agente × tempo), não duração ────────────────────
        -- Σ do trabalho consumido pela sessão. Os filtros têm precedente vivo no
        -- `busy_ms` da ocupação humana (ver `_agent_availability_sql`):
        --   · agent_type != 'system' → sintético (outage, mute queue) = zero recurso
        --   · role IN (primary, specialist) → exclui 'queue': ESPERA não é trabalho
        --   · duration_ms IS NOT NULL → segmento aberto some silenciosamente no sum()
        -- ⚠️ NUNCA comparar com `elapsed_time_ms`: segmentos se SOBREPÕEM (@mention é
        -- sempre paralelo ao primary e é rotina; especialista de conferência nasce
        -- dentro da janela do pai; hooks posatt são paralelos entre si). Logo
        -- Σ ≥ wall-clock com sobreposição e Σ ≤ com lacunas — as duas nunca são
        -- comparáveis, e a soma NUNCA vira tempo de sessão. Ver ADR §D9.
        LEFT JOIN (
            SELECT session_id, sum(duration_ms) AS agent_ms
            FROM {db}.segments FINAL
            WHERE tenant_id = {{tenant_id:String}}
              AND agent_type != 'system'
              AND role IN ('primary', 'specialist')
              AND duration_ms IS NOT NULL
            GROUP BY session_id
        ) AS _agt ON _agt.session_id = s.session_id"""

    # Use __ANI_DNIS__ placeholder instead of str.format() to avoid conflicts
    # with ClickHouse's own {param:Type} syntax inside _joins.
    # ⚠️ COM JOINs o ClickHouse qualifica o nome da coluna de saída (`s.session_id`, e não
    # `session_id`), e o mapeador de linhas — que casa por NOME — devolve null. No tier 3
    # (sem JOIN) o nome saía limpo, então o problema nunca aparecia ali. Por isso TODA
    # coluna aqui leva alias explícito: o nome de saída passa a ser uma decisão nossa, não
    # um efeito colateral do plano de query.
    _rich_sql = f"""
        SELECT
            s.session_id AS session_id,
            s.tenant_id  AS tenant_id,
            COALESCE(NULLIF(s.channel,  ''), _ch.channel_v)   AS channel,
            -- F1b: `entrou por`, fonte ÚNICA. Ver o comentário do JOIN removido.
            s.pool_id                                         AS pool_id,
            s.customer_id  AS customer_id,
            s.opened_at    AS opened_at,
            s.closed_at    AS closed_at,
            s.close_reason AS close_reason,
            COALESCE(NULLIF(s.outcome, ''), _seg_out.outcome_v) AS outcome,
            s.wait_time_ms AS wait_time_ms,
            -- Fase E.2: webhook duration = tempo decorrido total do processo
            -- (closed_at − início do primeiro segmento). Usa first_started_at porque o
            -- opened_at é re-carimbado a cada resume. Inclui as esperas (suspends) — é a
            -- duração real do caso. Demais canais mantêm handle_time_ms / wall-clock.
            if(
                COALESCE(NULLIF(s.channel, ''), _ch.channel_v) = 'webhook',
                if(s.closed_at IS NOT NULL AND _segdur.first_started_at IS NOT NULL,
                   toInt64(dateDiff('millisecond', _segdur.first_started_at, s.closed_at)),
                   NULL),
                COALESCE(
                    s.handle_time_ms,
                    if(s.closed_at IS NOT NULL AND s.opened_at IS NOT NULL,
                       toInt64(dateDiff('millisecond', s.opened_at, s.closed_at)), NULL)
                )
            ) AS handle_time_ms,
            -- ── D9: os DOIS nomes, e eles estão em UNIDADES diferentes ──────────
            -- `elapsed_time_ms` (tempo) = a mesma expressão de `handle_time_ms` acima,
            --   agora com o nome que diz o que ela é. `handle_time_ms` sobrevive como
            --   ALIAS DE COMPAT (a UI antiga o consome) e não deve ganhar leitor novo.
            -- `agent_time_ms` (agente × tempo) = quanto RECURSO o atendimento consumiu.
            -- Perguntas diferentes: "quanto o caso levou para o cliente" × "quanto de
            -- gente/máquina ele gastou". Antes desta fase o mesmo nome respondia as
            -- duas, com três comportamentos vivos e um quarto nome de saída.
            if(
                COALESCE(NULLIF(s.channel, ''), _ch.channel_v) = 'webhook',
                if(s.closed_at IS NOT NULL AND _segdur.first_started_at IS NOT NULL,
                   toInt64(dateDiff('millisecond', _segdur.first_started_at, s.closed_at)),
                   NULL),
                COALESCE(
                    s.handle_time_ms,
                    if(s.closed_at IS NOT NULL AND s.opened_at IS NOT NULL,
                       toInt64(dateDiff('millisecond', s.opened_at, s.closed_at)), NULL)
                )
            ) AS elapsed_time_ms,
            _agt.agent_ms AS agent_time_ms,
            __ANI_DNIS__,
            COALESCE(_sc.cnt, 0) AS segment_count,
            COALESCE(s.status, 'closed') AS status,
            -- Journey T1/T4: a ARESTA (quem me criou) e o seu RÓTULO (por quê).
            -- É com estes dois que a UI monta a árvore: sem o pai, tudo vira irmão;
            -- sem o rótulo, vê-se a hierarquia mas não por que cada filho existe.
            s.origin_session_id AS origin_session_id,
            s.spawn_reason      AS spawn_reason,
            s.root_session_id   AS root_session_id,
            -- D8 — direção DERIVADA (nunca armazenada), na mesma expressão que o
            -- filtro `direction` usa. `''` = não classificada, e a UI mostra `—`.
            {_DIRECTION_EXPR} AS direction
        FROM {db}.sessions AS s FINAL
        {_joins}
        WHERE {where}
        ORDER BY s.opened_at DESC
        LIMIT {page_size} OFFSET {offset}"""

    # ── Degradação por tiers — que NÃO pode ser muda ──────────────────────────
    # Os `except Exception` silenciosos aqui já custaram caro: o tier 1 falhava, a query
    # caía no tier 3 (bare minimum) e o endpoint respondia 200 com `segment_count: 0` e
    # colunas ausentes — parecendo funcionar. Foi assim que o `spawn_reason` (T4) chegou
    # nulo à UI mesmo estando correto no ClickHouse.
    #
    # Um fallback que esconde o motivo do fallback não é resiliência: é cegueira.
    try:
        # Tier 1: ANI/DNIS columns present
        result = client.query(
            _rich_sql.replace("__ANI_DNIS__", "s.ani AS ani, s.dnis AS dnis"),
            parameters=params,
        )
    except Exception as _t1_exc:
        logger.warning(
            "sessions tier-1 query failed (falling back to tier-2, sem ANI/DNIS): %s",
            _t1_exc,
        )
        try:
            # Tier 2: ANI/DNIS columns not yet migrated
            result = client.query(
                _rich_sql.replace("__ANI_DNIS__", "NULL AS ani, NULL AS dnis"),
                parameters=params,
            )
        except Exception as _t2_exc:
            logger.warning(
                "sessions tier-2 query failed (falling back to tier-3, BARE: sem "
                "segment_count, sem duração de webhook): %s",
                _t2_exc,
            )
            # Tier 3: segments / agent_events tables absent — bare minimum
            result = client.query(f"""
                SELECT
                    s.session_id, s.tenant_id, s.channel, s.pool_id, s.customer_id,
                    s.opened_at, s.closed_at, s.close_reason, s.outcome,
                    s.wait_time_ms, s.handle_time_ms,
                    NULL AS ani, NULL AS dnis, 0 AS segment_count,
                    COALESCE(s.status, 'closed') AS status,
                    -- Journey T1/T4: a árvore tem de sobreviver ao modo degradado — sem
                    -- estes dois a UI perde a hierarquia e o motivo de cada nó existir.
                    s.origin_session_id,
                    s.spawn_reason,
                    s.root_session_id,
                    -- A direção sobrevive ao modo degradado pelo mesmo motivo que a
                    -- aresta: sem ela a Vista Processos não sabe separar acesso do
                    -- cliente de etapa interna, e passa a contar as duas coisas juntas.
                    -- O JOIN `_ch` é sobre `sessions`, tabela que o tier 3 tem por
                    -- definição (o tier existe porque falta `segments`).
                    {_DIRECTION_EXPR} AS direction
                FROM {db}.sessions AS s FINAL
                {_agent_join}{_ch_join_sql(db)}
                WHERE {where}
                ORDER BY s.opened_at DESC
                LIMIT {page_size} OFFSET {offset}
            """, parameters=params)

    rows = _mark_internal_rows(_rows_to_dicts(result), internal_pools)
    # F3.3 — o chip de processo. Pós-passe, bounded à página. Ver o docstring.
    _attach_session_journey_chip(
        client, db, tenant_id, rows, internal_pools, accessible_pools, origin,
    )
    # F3.1 — o outro lado do par `entrou por → atendido por`. Ver o docstring.
    _attach_session_attended_pools(client, db, tenant_id, rows)
    return {
        "data": rows,
        "meta": _sessions_meta(
            page, page_size, total, total_contacts, since, until, scope,
            internal_pools_known=len(internal_pools or ()),
            # Os DOIS ramos isentos, não só o novo — o marcador tem de espelhar o
            # `if` que aplica a janela, ou ele mesmo vira uma segunda fonte.
            window_applied=not (root_session_id or origin_session_id),
        ),
    }


def _attach_session_attended_pools(
    client: Any, db: str, tenant_id: str, rows: list[dict],
) -> None:
    """F3.1 — anexa `attended_pool_ids` (pools que TRABALHARAM no contato) à linha.

    A célula da lista é o PAR `entrou por → atendido por`, e sem este campo ela teria
    só um lado: `s.pool_id` (a porta). Publicar um pool só, sob um cabeçalho que
    promete dois, é a confusão que este arco existe para desfazer — um nível abaixo.

    **Pós-passe, não JOIN.** A query principal já tem 4 JOINs e um histórico de dois
    modos de falha caros (`ILLEGAL_AGGREGATION` por alias sombreando coluna, e a
    degradação por tiers que respondia 200 pelo tier 3 com colunas ausentes). Um
    `groupUniqArray` a mais ali arriscaria os dois; aqui, a falha derruba apenas esta
    célula — e é logada, porque "sem seta" e "não consegui perguntar" não podem ficar
    indistinguíveis na tela.

    A lista vem ORDENADA por primeiro segmento (`argMin`/`min(started_at)` não cabem em
    `groupUniqArray`, então ordenamos pelo par): a UI mostra o ÚLTIMO como destino da
    seta, que é quem de fato atendeu por último. Sessão sem segmento (abandono antes de
    qualquer agente entrar — 5 no ambiente) fica com lista vazia: não há atendimento, e
    a seta não deve aparecer.
    """
    for r in rows:
        r["attended_pool_ids"] = []
    sids = sorted({r["session_id"] for r in rows if r.get("session_id")})
    if not sids:
        return
    id_list = ", ".join(f"'{s}'" for s in sids)
    sql = f"""
        SELECT
            session_id,
            arrayMap(x -> tupleElement(x, 2), arraySort(groupUniqArray((first_at, pool_id)))) AS pools
        FROM (
            SELECT session_id, pool_id, min(started_at) AS first_at
            FROM {db}.segments FINAL
            WHERE tenant_id = {{tenant_id:String}}
              AND session_id IN ({id_list})
              AND pool_id != ''
            GROUP BY session_id, pool_id
        )
        GROUP BY session_id
    """
    try:
        res = client.query(sql, parameters={"tenant_id": tenant_id})
    except Exception as exc:
        logger.warning(
            "attended-pools aggregation failed tenant=%s (a lista mostrará só o pool "
            "de ENTRADA, sem a seta — não é 'ninguém atendeu'): %s", tenant_id, exc,
        )
        return
    by_sid = {row[0]: list(row[1] or []) for row in res.result_rows}
    for r in rows:
        r["attended_pool_ids"] = by_sid.get(r.get("session_id"), [])


def _attach_session_journey_chip(
    client: Any, db: str, tenant_id: str, rows: list[dict],
    internal_pools: "frozenset[str] | None",
    accessible_pools: list[str] | None,
    origin: "str | list[str]" = "live",
) -> None:
    """F3.3 — anexa `journey_id` (raiz CANÔNICA) + `journey_session_count` à linha.

    É o dado do chip `PRC-{root[:4]} · N` da visão 1. Duas decisões estão aqui, e
    nenhuma delas é implementável no front:

    **1. O N é do PROCESSO INTEIRO, não da fatia filtrada.** A página só contém as
    linhas que passaram no período/canal/pool; contar ali daria o tamanho do recorte,
    e o processo não encolhe porque alguém olhou uma semana dele. Por isso esta query
    NÃO herda o `WHERE` da principal: só tenant + escopo de contato + ABAC + origem.
    A consequência (janela que pega 2 de 3 mostra `·3`) é deliberada e a tela carrega
    o rótulo que a nomeia — condicionado a `meta.window_applied`, que já existe.

    **2. O rótulo é a raiz CANÔNICA (union-find), não `root_session_id` cru.** Depois
    de um `journey_merge` os membros absorvidos guardam a raiz antiga; sem resolver,
    duas linhas do MESMO processo exibiriam chips com códigos diferentes — que é
    precisamente o sintoma que o modelo de journey existe para não produzir.

    **Por que o N é "contatos", com o mesmo predicado do card.** `_apply_contact_scope`
    exclui pool interno (wrap-up, dispatch) e é o que `_fetch_journeys.session_count`
    aplica. Ter aqui um segundo predicado faria o chip dizer `·2` e o cabeçalho da
    visão 2, ao pivotar dele, dizer `4` — duas fontes para o mesmo número, no mesmo
    clique. As "etapas internas" por `trigger` (`aprovacao_credito`, `limite_entrega`)
    entram nos dois, e é a visão 2 que as separa em `acessos do cliente × contatos`.

    **Falha ⇒ `journey_session_count: None`, nunca `1`.** Ausência não desenha chip;
    um `1` inventado desenharia a afirmação "este contato não pertence a processo
    nenhum", que é a mentira tranquila que a postura de engenharia proíbe. Por isso
    a falha também é LOGADA — sem log, chip ausente e processo-de-um ficam
    indistinguíveis na tela.
    """
    for r in rows:
        r["journey_id"] = r.get("root_session_id") or None
        r["journey_session_count"] = None
    roots = sorted({r["root_session_id"] for r in rows if r.get("root_session_id")})
    if not roots:
        return

    resolved = _journey_resolved_map(client, db, tenant_id)
    for r in rows:
        _rt = r.get("root_session_id")
        if _rt:
            r["journey_id"] = resolved.get(_rt, _rt)

    jexpr    = _journey_group_expr(resolved)
    id_list  = ", ".join(f"'{j}'" for j in sorted({resolved.get(rt, rt) for rt in roots}))
    conds: list[str] = ["s.tenant_id = {tenant_id:String}"]
    _apply_contact_scope(conds, internal_pools, alias="s.")
    _apply_origin_scope(conds, origin, alias="s.")
    _acc = _session_scope_clause(db, accessible_pools)
    if _acc:
        conds.append(_acc)
    # Alias `jid`, não `journey_id`: `sessions` TEM coluna com esse nome (o cache
    # dormente da raiz canônica) e alias que sombreia coluna real já derrubou query
    # inteira neste projeto (ILLEGAL_AGGREGATION, code 184).
    sql = f"""
        SELECT {jexpr} AS jid, count() AS n
        FROM {db}.sessions AS s FINAL
        WHERE {" AND ".join(conds)}
        GROUP BY jid
        HAVING jid IN ({id_list})
    """
    try:
        res = client.query(sql, parameters={"tenant_id": tenant_id})
    except Exception as exc:
        logger.warning(
            "journey chip aggregation failed tenant=%s (a lista de contatos sairá SEM "
            "chip de processo em todas as linhas — não é 'nenhum contato pertence a "
            "processo'): %s", tenant_id, exc,
        )
        return
    by_id = {row[0]: int(row[1]) for row in res.result_rows}
    for r in rows:
        _jid = r.get("journey_id")
        if _jid:
            r["journey_session_count"] = by_id.get(_jid)


# ─── /reports/journeys (Journey J2 — proveniência-only) ───────────────────────

async def query_journeys_report(
    client:    Any,
    database:  str,
    tenant_id: str,
    from_dt:   str | None = None,
    to_dt:     str | None = None,
    *,
    channel:          str | None       = None,
    pool_id:          str | None       = None,
    customer_id:      str | None       = None,
    root_session_id:  str | None       = None,
    significant_only: bool             = True,
    accessible_pools: list[str] | None = None,
    origin:           "str | list[str]" = "live",
    page:      int = 1,
    page_size: int = 100,
) -> dict:
    """
    Journey list (J2 — proveniência-only): agrupa `analytics.sessions` por
    `root_session_id` (a raiz canônica local). SEM alias/merge (isso é J3) — cada
    grupo é uma árvore de proveniência. Uma journey aparece se ≥1 sessão-membro casa
    os filtros (janela/pool/customer/origin); os agregados refletem os membros na janela.
    `significant_only` (default de UX) esconde journeys de 1 sessão sem workflow:
    mantém `count>1` OU que tenha canal `webhook` (processo N3).
    `customer_id` (Cliente 360 / HJ — ADR §D2): journeys com ≥1 sessão-membro do cliente.
    """
    since = _ch_fmt(from_dt) if from_dt else _default_from()
    until = _ch_fmt(to_dt, upper=True) if to_dt else _default_to()
    if accessible_pools is not None and not accessible_pools:
        return {"data": [], "meta": _meta(page, page_size, 0, since, until)}
    internal_pools = await _internal_pools_for(tenant_id)
    try:
        return await asyncio.to_thread(
            _fetch_journeys, client, database, tenant_id, since, until,
            channel, pool_id, customer_id, root_session_id, significant_only,
            accessible_pools, origin, page, page_size, internal_pools,
        )
    except Exception as exc:
        logger.warning("query_journeys_report failed tenant=%s: %s", tenant_id, exc)
        return {"data": [], "meta": _meta(page, page_size, 0, since, until), "error": "data_unavailable"}


def _fetch_journeys(
    client: Any, db: str, tenant_id: str, since: str, until: str,
    channel: str | None, pool_id: str | None, customer_id: str | None,
    root_session_id: str | None,
    significant_only: bool,
    accessible_pools: list[str] | None, origin: "str | list[str]",
    page: int, page_size: int,
    internal_pools: "frozenset[str] | None" = None,
) -> dict:
    # Journey J3 — mapa union-find (raiz→canônica). Computado CEDO porque a Fatia 2
    # (fetch de UMA journey por deep-link ao L2) resolve o root pedido ao canônico e
    # filtra os membros por ele.
    resolved = _journey_resolved_map(client, db, tenant_id)
    jexpr    = _journey_group_expr(resolved)

    conditions = ["s.tenant_id = {tenant_id:String}"]
    # E2f — mesma regra do _fetch_sessions. O wrap-up destacado herda o
    # `root_session_id` da origem, então sem isto ele entraria na journey como mais
    # uma sessão do processo e inflaria a contagem.
    _apply_contact_scope(conditions, internal_pools, alias="s.")
    params: dict = {"tenant_id": tenant_id}

    # Fatia 2 — fetch DIRECIONADO de uma journey (o L2 abre por URL sem a JourneyRow
    # do L1). Resolve o root ao canônico, restringe aos membros (todos os roots que
    # mapeiam ao canônico) e IGNORA a janela de data + `significant_only` — o operador
    # abriu ESTA journey de propósito, mesmo fora do período/1-sessão.
    canonical: str | None = None
    if root_session_id:
        canonical = resolved.get(root_session_id, root_session_id)
        member_roots = sorted({canonical, root_session_id} | {
            src for src, canon in resolved.items() if canon == canonical
        })
        conditions.append("s.root_session_id IN {jroots:Array(String)}")
        params["jroots"] = member_roots
        params["jcanon"] = canonical
    else:
        conditions.append(f"s.opened_at >= '{since}'")
        conditions.append(f"s.opened_at < '{until}'")

    if channel:
        conditions.append("s.channel = {channel:String}")
        params["channel"] = channel
    if pool_id:
        conditions.append(
            f"s.session_id IN (SELECT session_id FROM {db}.segments FINAL"
            " WHERE tenant_id = {tenant_id:String} AND pool_id = {pool_id:String})"
        )
        params["pool_id"] = pool_id
    # Cliente 360 / HJ (ADR §D2): journeys com ≥1 sessão-membro do cliente. Filtra pelas
    # RAÍZES onde o cliente aparece (não pelo customer_id direto) — assim inclui TODOS os
    # membros dessas journeys (session_count correto), não só as sessões do cliente. A
    # raiz canônica (union-find/merge) é resolvida no GROUP BY externo; sob merge o filtro
    # é levemente conservador (aceitável no v1).
    if customer_id:
        conditions.append(
            f"s.root_session_id IN (SELECT root_session_id FROM {db}.sessions FINAL"
            " WHERE tenant_id = {tenant_id:String} AND customer_id = {customer_id:String})"
        )
        params["customer_id"] = customer_id
    # Pool-scope ABAC (Arc 7c) — predicado único (F1b), ver _session_scope_clause.
    _scope = _session_scope_clause(db, accessible_pools)
    if _scope:
        conditions.append(_scope)

    _apply_origin_scope(conditions, origin, alias="s.")
    where = " AND ".join(conditions)

    # `resolved`/`jexpr` já computados no topo (agrupam pela raiz CANÔNICA via
    # journey_aliases; sem merges → identidade J2; o transform usa o mapa totalmente
    # resolvido, 1 salto basta).

    # Filtros de grupo → HAVING. A "significância" é UX (não se aplica ao fetch
    # direcionado); o filtro por journey canônica é da Fatia 2 (deep-link).
    having_conds: list[str] = []
    if significant_only and not root_session_id:
        having_conds.append("(count() > 1 OR has(groupUniqArray(s.channel), 'webhook'))")
    if root_session_id:
        having_conds.append("journey_id = {jcanon:String}")
    having = ("HAVING " + " AND ".join(having_conds)) if having_conds else ""

    # total = número de journeys (grupos canônicos) que passam o HAVING.
    total = _count(
        client,
        f"""SELECT count() FROM (
            SELECT {jexpr} AS journey_id
            FROM {db}.sessions AS s FINAL
            WHERE {where}
            GROUP BY journey_id
            {having}
        )""",
        params,
    )

    offset = (page - 1) * page_size
    sql = f"""
        SELECT
            {jexpr} AS journey_id,
            count() AS session_count,
            min(s.opened_at) AS started_at,
            max(COALESCE(s.closed_at, s.opened_at)) AS last_activity_at,
            arrayFilter(x -> x != '', groupUniqArray(s.channel)) AS channels,
            arrayFilter(x -> x != '', groupUniqArray(s.pool_id)) AS pool_ids,
            countIf(COALESCE(s.status, 'closed') != 'closed') AS open_count,
            (count() > 1 OR has(groupUniqArray(s.channel), 'webhook')) AS significant,
            -- T2: desfecho do processo = o outcome da RAIZ (a sessão que É o processo).
            --
            -- Era `argMaxIf(outcome, opened_at, outcome != '')` = "a sessão mais
            -- recentemente ABERTA que tenha outcome". Numa journey de survey, quem abre
            -- por último é o CONTATO DE SURVEY — então o "desfecho do processo" exibido
            -- era, na prática, o desfecho da PESQUISA. Um survey que falhasse faria a tela
            -- declarar que o processo de negócio falhou. Um contato auxiliar decidindo o
            -- desfecho do processo é a inversão que o modelo de níveis existe para impedir.
            --
            -- A regra correta cai da estrutura: sessão é NÓ, journey é ÁRVORE ⇒ cada nó
            -- tem seu outcome, e o do PROCESSO é o da RAIZ. Filho nunca sobrescreve pai.
            --
            -- `s.session_id = {jexpr}` isola a raiz CANÔNICA: para a linha dela,
            -- root_session_id = ela mesma → jexpr = session_id. Após um merge, os membros
            -- da árvore absorvida (inclusive a raiz antiga) resolvem para a canônica e não
            -- casam — ou seja, o desfecho passa a ser o da journey sobrevivente, que é
            -- exatamente o que "sobreviver" significa.
            --
            -- Raiz ainda aberta → sem outcome → NULL. É honesto: o processo não concluiu.
            -- (A UI marca como PROVISÓRIO quando open_count > 0.)
            argMaxIf(
                s.outcome, s.opened_at,
                s.session_id = {jexpr} AND s.outcome IS NOT NULL AND s.outcome != ''
            ) AS business_outcome,
            -- ── D9 no grão JOURNEY — os mesmos dois nomes do grão sessão ────────
            -- Consertar só a sessão deixaria os dois níveis discordando, e a §7 do ADR
            -- existe para que não discordem.
            --
            -- `elapsed_time_ms` (tempo) = wall-clock do processo, min→max. É o antigo
            -- `business_duration_ms`, que sobrevive como ALIAS DE COMPAT (a
            -- AnaliseJourneysPage o consome) e não deve ganhar leitor novo.
            --
            -- ⚠️ O "refino adiado" que este comentário prometia — *"exclui suspenso via
            -- SUM(segment.duration_ms)"* — NÃO é o que `agent_time_ms` faz, e a promessa
            -- estava errada nos dois sentidos. Somar segmentos não remove o suspenso de
            -- um wall-clock: produz OUTRA grandeza, em OUTRA unidade (agente × tempo),
            -- que pode ser MAIOR que o wall-clock quando há paralelismo (@mention,
            -- conferência, hooks posatt). O tempo efetivamente suspenso agora tem lugar
            -- próprio — `analytics.session_transitions` (D4/Fase 2) —, e é de lá que
            -- deve sair, por união de intervalos, quando for pedido. Não daqui.
            toInt64(dateDiff('millisecond', min(s.opened_at), max(COALESCE(s.closed_at, s.opened_at)))) AS business_duration_ms,
            toInt64(dateDiff('millisecond', min(s.opened_at), max(COALESCE(s.closed_at, s.opened_at)))) AS elapsed_time_ms,
            -- `agent_time_ms` (agente × tempo) = Σ do trabalho de TODAS as sessões do
            -- processo, com os mesmos filtros do grão sessão. Sessão sem segmento
            -- elegível entra como 0 (COALESCE) — a journey existe mesmo assim.
            toInt64(sum(COALESCE(_jagt.agent_ms, 0))) AS agent_time_ms
        FROM {db}.sessions AS s FINAL
        LEFT JOIN (
            SELECT session_id, sum(duration_ms) AS agent_ms
            FROM {db}.segments FINAL
            WHERE tenant_id = {{tenant_id:String}}
              AND agent_type != 'system'
              AND role IN ('primary', 'specialist')
              AND duration_ms IS NOT NULL
            GROUP BY session_id
        ) AS _jagt ON _jagt.session_id = s.session_id
        WHERE {where}
        GROUP BY journey_id
        {having}
        ORDER BY started_at DESC
        LIMIT {page_size} OFFSET {offset}
    """
    result = client.query(sql, parameters=params)
    rows = _rows_to_dicts(result)

    # J4 — sinal de qualidade N3: agrega session_signal grain=journey pela raiz
    # CANÔNICA (mesmo transform do union-find sobre origin_session_id). Bounded à
    # página (HAVING journey_id IN ...). Degrada gracioso (sem sinais → zeros/None).
    _attach_journey_signals(client, db, tenant_id, resolved, rows)
    # ADR §7 / fatia 4b — o segundo número do card. Pós-passe, não agregado novo.
    _attach_journey_internal_counts(
        client, db, tenant_id, resolved, rows, internal_pools, accessible_pools,
    )

    # `window_applied` — a dívida que a F2 registrou e não fechou (o CHANGELOG dela
    # diz: "/reports/journeys tem a MESMA mentira e segue sem marcador"). O ramo
    # direcionado (fetch de UMA journey) ignora janela E `significant_only`, mas o
    # `_meta` publica `from_dt`/`to_dt` de qualquer jeito. Medido antes de fechar
    # (`probe_journeys_window_applied.sh`): A(janela absurda)=1 == B(default)=1, com
    # a testemunha C=0 provando que a janela de fato exclui quando incide.
    _jmeta = _meta(page, page_size, total, since, until)
    _jmeta["window_applied"] = not bool(root_session_id)
    return {"data": rows, "meta": _jmeta}


def _attach_journey_internal_counts(
    client: Any, db: str, tenant_id: str,
    resolved: dict[str, str], rows: list[dict],
    internal_pools: "frozenset[str] | None",
    accessible_pools: list[str] | None = None,
) -> None:
    """Anexa `internal_session_count` (wrap-up, dispatch) a cada journey da página.

    **Por que pós-passe e não mais um agregado na query principal.** Para contar as
    internas ali, elas teriam de entrar no `WHERE` — e aí contaminariam TODOS os
    outros agregados do mesmo `GROUP BY`: `channels` e `pool_ids` ganhariam o pool
    de wrap-up, `open_count` contaria sessão interna aberta, e o wall-clock
    (`min(opened_at)`→`max(closed_at)`) esticaria até o fim do wrap-up — que é
    exatamente o G1 que a E2f fechou, reaberto num nível acima. Blindar cada
    agregado com `…If(<é contato>)` seria reescrever ~10 expressões numa query com
    histórico de dois bugs sutis (`business_outcome` e as durações D9). O pós-passe
    custa uma query bounded à página e não encosta em nada disso — é o mesmo padrão
    de `_attach_journey_signals`.

    Zero é o default em toda linha: journey sem sessão interna e falha de consulta
    ficam indistinguíveis aqui, e é por isso que a falha é logada.
    """
    for r in rows:
        r["internal_session_count"] = 0
    if not internal_pools:
        return
    ids = [r["journey_id"] for r in rows if r.get("journey_id")]
    if not ids:
        return
    jexpr    = _journey_group_expr(resolved)
    id_list  = ", ".join(f"'{j}'" for j in ids)
    pool_list = ", ".join(f"'{p}'" for p in sorted(internal_pools))
    # Escopo ABAC igual ao da query principal: sem isto o card poderia anunciar
    # "1 interna" para um supervisor cujo drill não mostra nenhuma. É o MESMO
    # predicado do drill por construção (F1b) — antes eram duas cópias que só por
    # sorte diziam a mesma coisa.
    _acc_clause  = _session_scope_clause(db, accessible_pools)
    scope_clause = f" AND {_acc_clause}" if _acc_clause else ""
    # Alias `jid`, não `journey_id`: `sessions` TEM uma coluna com esse nome (o cache
    # dormente da raiz canônica) e alias que sombreia coluna real já derrubou query
    # inteira neste projeto (ILLEGAL_AGGREGATION, code 184).
    sql = f"""
        SELECT {jexpr} AS jid, count() AS internal_count
        FROM {db}.sessions AS s FINAL
        WHERE s.tenant_id = {{tenant_id:String}}
          AND s.pool_id IN ({pool_list}){scope_clause}
        GROUP BY jid
        HAVING jid IN ({id_list})
    """
    try:
        res = client.query(sql, parameters={"tenant_id": tenant_id})
    except Exception as exc:
        logger.warning(
            "journey internal-count aggregation failed tenant=%s (card mostrará 0 "
            "internas mesmo onde houver): %s", tenant_id, exc,
        )
        return
    by_id = {row[0]: int(row[1]) for row in res.result_rows}
    for r in rows:
        r["internal_session_count"] = by_id.get(r["journey_id"], 0)


def _attach_journey_signals(
    client: Any, db: str, tenant_id: str, resolved: dict[str, str], rows: list[dict],
) -> None:
    """J4 — anexa signal_count + médias (nps/csat/ces) por journey às linhas do report.
    Os sinais grain=journey são chaveados por origin_session_id; resolvemos ao canônico
    com o MESMO mapa union-find e agregamos por journey canônica."""
    for r in rows:
        r["signal_count"] = 0
        r["nps_avg"] = r["csat_avg"] = r["ces_avg"] = None
    ids = [r["journey_id"] for r in rows if r.get("journey_id")]
    if not ids:
        return
    sig_expr = _journey_group_expr(resolved, col="ss.origin_session_id")
    id_list = ", ".join(f"'{j}'" for j in ids)
    sql = f"""
        SELECT
            {sig_expr} AS journey_id,
            count() AS signal_count,
            if(countIf(metric = 'nps')  > 0, round(avgIf(value_num, metric = 'nps'),  2), NULL) AS nps_avg,
            if(countIf(metric = 'csat') > 0, round(avgIf(value_num, metric = 'csat'), 2), NULL) AS csat_avg,
            if(countIf(metric = 'ces')  > 0, round(avgIf(value_num, metric = 'ces'),  2), NULL) AS ces_avg
        FROM {db}.session_signal AS ss FINAL
        WHERE ss.tenant_id = {{tenant_id:String}} AND ss.grain = 'journey'
        GROUP BY journey_id
        HAVING journey_id IN ({id_list})
    """
    try:
        res = client.query(sql, parameters={"tenant_id": tenant_id})
    except Exception as exc:
        logger.debug("journey signal aggregation failed tenant=%s: %s", tenant_id, exc)
        return
    by_id = {row[0]: row for row in res.result_rows}
    for r in rows:
        s = by_id.get(r["journey_id"])
        if s:
            r["signal_count"] = int(s[1])
            r["nps_avg"], r["csat_avg"], r["ces_avg"] = s[2], s[3], s[4]


# ─── Journey T6 — rastro forense (proveniência bidirecional) ──────────────────
# "O que aconteceu a partir daqui": a cadeia de proveniência EM VOLTA de uma
# sessão — ancestrais (subindo por origin_session_id até a raiz de topo) +
# descendentes (BFS por origin_session_id), ATRAVESSANDO fronteiras de journey.
#
# Diferente da Vista Processos (§6 da spec: MEDE uma journey, tem fronteira), o
# rastro PERCORRE o grafo inteiro — sem fronteira, ninguém mede. Só descendentes
# do FOCO entram (a cadeia sobe em linha, desce em árvore); irmãos do foco não
# são expandidos, senão a árvore balooneia. Cada nó cuja journey canônica difere
# da do foco é marcado `journey_boundary` (a fronteira que o `journey: new` cria).

_TRACE_COLS = (
    "s.session_id, s.origin_session_id, s.spawn_reason, s.root_session_id, "
    "s.channel, s.pool_id, s.status, s.outcome, s.opened_at, s.closed_at"
)


async def query_session_trace(
    client:    Any,
    database:  str,
    tenant_id: str,
    focus_session_id: str,
    *,
    accessible_pools: list[str] | None = None,
    origin:    "str | list[str]"       = "live",
    max_depth: int = 25,
    max_nodes: int = 200,
) -> dict:
    """Journey T6 — rastro forense bidirecional a partir de `focus_session_id`.
    Degradação graciosa (erro/foco inexistente → foco None + nós vazios)."""
    empty = {
        "focus_session_id": focus_session_id, "focus_journey_id": None,
        "focus": None, "nodes": [],
        "meta": {"node_count": 0, "max_depth": max_depth, "max_nodes": max_nodes, "truncated": False},
    }
    if accessible_pools is not None and not accessible_pools:
        return empty
    try:
        return await asyncio.to_thread(
            _fetch_session_trace, client, database, tenant_id, focus_session_id,
            accessible_pools, origin, max_depth, max_nodes,
        )
    except Exception as exc:
        logger.warning("query_session_trace failed tenant=%s sid=%s: %s", tenant_id, focus_session_id, exc)
        return {**empty, "error": "data_unavailable"}


def _trace_base_conditions(
    db: str, tenant_id: str, accessible_pools: list[str] | None,
    origin: "str | list[str]",
) -> list[str]:
    conditions = ["s.tenant_id = {tenant_id:String}"]
    # ABAC pool-scope (Arc 7c) — predicado único (F1b), ver _session_scope_clause.
    # Nós fora do escopo simplesmente não aparecem no rastro (pode partir a cadeia;
    # aceitável — o mesmo recorte de todos os /reports). `db` entrou na assinatura
    # nesta fase: o predicado passou a consultar `segments`, e um rastro forense é o
    # último lugar onde se quer um recorte diferente do da lista que levou até ele.
    _scope = _session_scope_clause(db, accessible_pools)
    if _scope:
        conditions.append(_scope)
    _apply_origin_scope(conditions, origin, alias="s.")
    return conditions


def _fetch_trace_rows(
    client: Any, db: str, base_conditions: list[str], extra: str, params: dict,
) -> list[dict]:
    where = " AND ".join(base_conditions + [extra])
    sql = f"SELECT {_TRACE_COLS} FROM {db}.sessions AS s FINAL WHERE {where}"
    return _rows_to_dicts(client.query(sql, parameters=params))


def _is_no_parent(v: Any) -> bool:
    """origin_session_id ausente = raiz de topo (NULL, '' ou o zero-uuid legado)."""
    if not v:
        return True
    s = str(v)
    return s in ("", "0", "00000000-0000-0000-0000-000000000000")


def _fetch_session_trace(
    client: Any, db: str, tenant_id: str, focus_id: str,
    accessible_pools: list[str] | None, origin: "str | list[str]",
    max_depth: int, max_nodes: int,
) -> dict:
    base   = _trace_base_conditions(db, tenant_id, accessible_pools, origin)
    params = {"tenant_id": tenant_id}
    resolved = _journey_resolved_map(client, db, tenant_id)

    def canon(root: Any) -> Any:
        return resolved.get(str(root), str(root)) if root else root

    focus_rows = _fetch_trace_rows(client, db, base, f"s.session_id = '{focus_id}'", params)
    if not focus_rows:
        return {
            "focus_session_id": focus_id, "focus_journey_id": None,
            "focus": None, "nodes": [],
            "meta": {"node_count": 0, "max_depth": max_depth, "max_nodes": max_nodes, "truncated": False},
        }
    focus = focus_rows[0]
    focus["depth"] = 0
    focus_journey = canon(focus.get("root_session_id"))

    nodes: dict[str, dict] = {focus_id: focus}
    truncated = False

    # ── sobe: ancestrais em CADEIA (cada nó tem exatamente 1 origem) ──
    cur   = focus.get("origin_session_id")
    depth = 0
    while (not _is_no_parent(cur) and str(cur) not in nodes
           and depth > -max_depth and len(nodes) < max_nodes):
        rows = _fetch_trace_rows(client, db, base, f"s.session_id = '{cur}'", params)
        if not rows:
            break
        depth -= 1
        r = rows[0]
        r["depth"] = depth
        nodes[str(cur)] = r
        cur = r.get("origin_session_id")

    # ── desce: descendentes do FOCO em BFS (árvore) ──
    frontier = [focus_id]
    depth    = 0
    while frontier and depth < max_depth and len(nodes) < max_nodes:
        depth += 1
        in_list = ", ".join(f"'{sid}'" for sid in frontier)
        rows = _fetch_trace_rows(client, db, base, f"s.origin_session_id IN ({in_list})", params)
        next_frontier: list[str] = []
        for r in rows:
            sid = str(r["session_id"])
            if sid in nodes:
                continue
            if len(nodes) >= max_nodes:
                truncated = True
                break
            r["depth"] = depth
            nodes[sid] = r
            next_frontier.append(sid)
        frontier = next_frontier

    out: list[dict] = []
    for sid, r in nodes.items():
        jid = canon(r.get("root_session_id"))
        r["journey_id"]       = jid
        r["is_focus"]         = (sid == focus_id)
        r["journey_boundary"] = (jid != focus_journey)
        out.append(r)
    out.sort(key=lambda x: (x.get("depth", 0), x.get("opened_at") or ""))

    return {
        "focus_session_id": focus_id,
        "focus_journey_id": focus_journey,
        "focus": focus,
        "nodes": out,
        "meta": {"node_count": len(out), "max_depth": max_depth, "max_nodes": max_nodes, "truncated": truncated},
    }


# ─── /reports/contact-insights ────────────────────────────────────────────────

async def query_contact_insights_report(
    client:    Any,
    database:  str,
    tenant_id: str,
    from_dt:   str | None = None,
    to_dt:     str | None = None,
    *,
    session_id:  str | None       = None,
    category:    str | None       = None,
    tags:        list[str] | None = None,
    insight_type: str | None      = None,
    accessible_pools: list[str] | None = None,
    page:      int = 1,
    page_size: int = 100,
) -> dict:
    since = _ch_fmt(from_dt) if from_dt else _default_from()
    until = _ch_fmt(to_dt, upper=True) if to_dt else _default_to()
    # accessible_pools=[] → sem acesso a pool nenhum → lista vazia (não chama ClickHouse).
    if accessible_pools is not None and not accessible_pools:
        return {"data": [], "meta": _meta(page, page_size, 0, since, until)}
    try:
        return await asyncio.to_thread(
            _fetch_contact_insights, client, database, tenant_id, since, until,
            session_id, category, tags, insight_type, page, page_size,
            accessible_pools,
        )
    except Exception as exc:
        logger.warning("query_contact_insights_report failed tenant=%s: %s", tenant_id, exc)
        return {"data": [], "meta": _meta(page, page_size, 0, since, until), "error": "data_unavailable"}


def _fetch_contact_insights(
    client: Any, db: str, tenant_id: str,
    since: str, until: str,
    session_id: str | None, category: str | None,
    tags: list[str] | None, insight_type: str | None,
    page: int, page_size: int,
    accessible_pools: list[str] | None = None,
) -> dict:
    conditions = [
        "tenant_id = {tenant_id:String}",
        f"timestamp >= '{since}'",
        f"timestamp < '{until}'",
    ]
    params: dict = {"tenant_id": tenant_id}

    if session_id:
        conditions.append("session_id = {session_id:String}")
        params["session_id"] = session_id
    if category:
        conditions.append("category = {category:String}")
        params["category"] = category
    if insight_type:
        conditions.append("insight_type = {insight_type:String}")
        params["insight_type"] = insight_type
    if tags:
        for i, tag in enumerate(tags):
            tag_key = f"tag_{i}"
            conditions.append(f"has(tags, {{{tag_key}:String}})")
            params[tag_key] = tag

    # Segurança Fase D — pool-scoping (Arc 7c). contact_insights só tem session_id;
    # restringe às sessões que TOCARAM um pool do domínio (subquery a segments, que
    # carrega pool_id por segmento — mesmo padrão do /reports/sessions). Valores vêm de
    # JWT verificado → seguros inline. accessible_pools=[] é curto-circuitado no wrapper.
    if accessible_pools:
        pool_list = ", ".join(f"'{p}'" for p in accessible_pools)
        conditions.append(
            f"session_id IN (SELECT session_id FROM {db}.segments FINAL "
            f"WHERE tenant_id = {{tenant_id:String}} AND pool_id IN ({pool_list}))"
        )

    where = " AND ".join(conditions)
    offset = (page - 1) * page_size

    total = _count(client, f"SELECT count() FROM {db}.contact_insights FINAL WHERE {where}", params)

    result = client.query(f"""
        SELECT
            insight_id, tenant_id, session_id,
            insight_type, category, value, tags,
            agent_id, timestamp
        FROM {db}.contact_insights FINAL
        WHERE {where}
        ORDER BY timestamp DESC
        LIMIT {page_size} OFFSET {offset}
    """, parameters=params)

    return {"data": _rows_to_dicts(result), "meta": _meta(page, page_size, total, since, until)}


# ─── /reports/agents — REMOVIDO (2026-07-28) ─────────────────────────────────
#
# `query_agents_report` / `_fetch_agents` liam `agent_events` cru e serviam o
# endpoint `GET /reports/agents`, que não tinha NENHUM chamador em todo o repo.
# O que parecia ser seu cliente (`useAgentReport` no platform-ui) aponta para a
# evaluation-api (`/v1/evaluation/reports/agents`) e espera outro shape.
#
# Performance por agente vem de `/reports/agents/performance` e
# `/reports/agent-performance/daily`, ambos sobre `segments` (Arc 5).
# Não confundir com `/reports/agent-events/*`, que é Arc 12 e lê
# `agent_business_events` — outro eixo, mantido.


# ─── /reports/quality ────────────────────────────────────────────────────────

async def query_quality_report(
    client:    Any,
    database:  str,
    tenant_id: str,
    from_dt:   str | None = None,
    to_dt:     str | None = None,
    *,
    pool_id:          str | None       = None,
    category:         str | None       = None,
    accessible_pools: list[str] | None = None,
    page:      int = 1,
    page_size: int = 100,
) -> dict:
    since = _ch_fmt(from_dt) if from_dt else _default_from()
    until = _ch_fmt(to_dt, upper=True) if to_dt else _default_to()
    if accessible_pools is not None and not accessible_pools:
        return {"data": [], "meta": _meta(page, page_size, 0, since, until)}
    try:
        return await asyncio.to_thread(
            _fetch_quality, client, database, tenant_id, since, until,
            pool_id, category, accessible_pools, page, page_size,
        )
    except Exception as exc:
        logger.warning("query_quality_report failed tenant=%s: %s", tenant_id, exc)
        return {"data": [], "meta": _meta(page, page_size, 0, since, until), "error": "data_unavailable"}


def _fetch_quality(
    client: Any, db: str, tenant_id: str,
    since: str, until: str,
    pool_id: str | None, category: str | None,
    accessible_pools: list[str] | None,
    page: int, page_size: int,
) -> dict:
    conditions = [
        "tenant_id = {tenant_id:String}",
        f"timestamp >= '{since}'",
        f"timestamp < '{until}'",
    ]
    params: dict = {"tenant_id": tenant_id}

    if pool_id:
        conditions.append("pool_id = {pool_id:String}")
        params["pool_id"] = pool_id
    if category:
        conditions.append("category = {category:String}")
        params["category"] = category
    _apply_pool_scope(conditions, accessible_pools)

    where = " AND ".join(conditions)
    offset = (page - 1) * page_size

    total = _count(client, f"SELECT count() FROM {db}.sentiment_events FINAL WHERE {where}", params)

    result = client.query(f"""
        SELECT
            event_id, tenant_id, session_id, pool_id,
            score, category, timestamp
        FROM {db}.sentiment_events FINAL
        WHERE {where}
        ORDER BY timestamp DESC
        LIMIT {page_size} OFFSET {offset}
    """, parameters=params)

    return {"data": _rows_to_dicts(result), "meta": _meta(page, page_size, total, since, until)}


# ─── /reports/usage ──────────────────────────────────────────────────────────

async def query_usage_report(
    client:    Any,
    database:  str,
    tenant_id: str,
    from_dt:   str | None = None,
    to_dt:     str | None = None,
    *,
    dimension:        str | None = None,
    source_component: str | None = None,
    page:      int = 1,
    page_size: int = 100,
) -> dict:
    since = _ch_fmt(from_dt) if from_dt else _default_from()
    until = _ch_fmt(to_dt, upper=True) if to_dt else _default_to()
    try:
        return await asyncio.to_thread(
            _fetch_usage, client, database, tenant_id, since, until,
            dimension, source_component, page, page_size,
        )
    except Exception as exc:
        logger.warning("query_usage_report failed tenant=%s: %s", tenant_id, exc)
        return {"data": [], "meta": _meta(page, page_size, 0, since, until), "error": "data_unavailable"}


def _fetch_usage(
    client: Any, db: str, tenant_id: str,
    since: str, until: str,
    dimension: str | None, source_component: str | None,
    page: int, page_size: int,
) -> dict:
    conditions = [
        "tenant_id = {tenant_id:String}",
        f"timestamp >= '{since}'",
        f"timestamp < '{until}'",
    ]
    params: dict = {"tenant_id": tenant_id}

    if dimension:
        conditions.append("dimension = {dimension:String}")
        params["dimension"] = dimension
    if source_component:
        conditions.append("source_component = {source_component:String}")
        params["source_component"] = source_component

    where = " AND ".join(conditions)
    offset = (page - 1) * page_size

    total = _count(client, f"SELECT count() FROM {db}.usage_events FINAL WHERE {where}", params)

    result = client.query(f"""
        SELECT
            event_id, tenant_id, session_id,
            dimension, quantity, source_component, timestamp
        FROM {db}.usage_events FINAL
        WHERE {where}
        ORDER BY timestamp DESC
        LIMIT {page_size} OFFSET {offset}
    """, parameters=params)

    return {"data": _rows_to_dicts(result), "meta": _meta(page, page_size, total, since, until)}


# ─── /reports/workflows ──────────────────────────────────────────────────────

async def query_workflows_report(
    client:    Any,
    database:  str,
    tenant_id: str,
    from_dt:   str | None = None,
    to_dt:     str | None = None,
    *,
    flow_id:     str | None = None,
    status:      str | None = None,
    campaign_id: str | None = None,
    page:      int = 1,
    page_size: int = 100,
) -> dict:
    since = _ch_fmt(from_dt) if from_dt else _default_from()
    until = _ch_fmt(to_dt, upper=True) if to_dt else _default_to()
    try:
        return await asyncio.to_thread(
            _fetch_workflows, client, database, tenant_id, since, until,
            flow_id, status, campaign_id, page, page_size,
        )
    except Exception as exc:
        logger.warning("query_workflows_report failed tenant=%s: %s", tenant_id, exc)
        return {"data": [], "meta": _meta(page, page_size, 0, since, until), "error": "data_unavailable"}


def _fetch_workflows(
    client: Any, db: str, tenant_id: str,
    since: str, until: str,
    flow_id: str | None, status: str | None, campaign_id: str | None,
    page: int, page_size: int,
) -> dict:
    conditions = [
        "tenant_id = {tenant_id:String}",
        f"timestamp >= '{since}'",
        f"timestamp < '{until}'",
    ]
    params: dict = {"tenant_id": tenant_id}

    if flow_id:
        conditions.append("flow_id = {flow_id:String}")
        params["flow_id"] = flow_id
    if status:
        conditions.append("status = {status:String}")
        params["status"] = status
    if campaign_id:
        conditions.append("campaign_id = {campaign_id:String}")
        params["campaign_id"] = campaign_id

    where = " AND ".join(conditions)
    offset = (page - 1) * page_size

    total = _count(client, f"SELECT count() FROM {db}.workflow_events FINAL WHERE {where}", params)

    result = client.query(f"""
        SELECT
            event_id, tenant_id, instance_id, flow_id, campaign_id,
            event_type, status, current_step, suspend_reason, decision,
            outcome, duration_ms, wait_duration_ms, error, timestamp
        FROM {db}.workflow_events FINAL
        WHERE {where}
        ORDER BY timestamp DESC
        LIMIT {page_size} OFFSET {offset}
    """, parameters=params)

    return {"data": _rows_to_dicts(result), "meta": _meta(page, page_size, total, since, until)}


# ─── /reports/workflow-summary — aggregated workflow analytics ────────────────
#
# Aggregates workflow_events per flow_id or campaign_id.
# Uses countDistinctIf to count unique instances per lifecycle stage.
# duration_ms is only populated on completed/failed events (workflow-api sets it).

async def query_workflow_summary(
    client:    Any,
    database:  str,
    tenant_id: str,
    from_dt:   str | None = None,
    to_dt:     str | None = None,
    *,
    group_by:         str        = "pool_id",   # "pool_id" | "flow_id" | "campaign_id"
    flow_id:          str | None = None,
    campaign_id:      str | None = None,
    pool_id:          str | None = None,
    accessible_pools: list[str] | None = None,  # None = unrestricted; non-empty list = scope
) -> dict:
    """
    Summarised workflow metrics grouped by pool_id, flow_id, or campaign_id.

    Returns one row per group with:
      group_key, total_triggered, total_completed, total_failed,
      total_timeout, total_cancelled, total_suspended,
      completion_rate, failure_rate, avg_duration_ms

    accessible_pools: when non-empty list, restricts results to those pool IDs.
    None means unrestricted (admin / open-access mode).
    (mirrors the same scoping used by all other report endpoints).
    """
    since = _ch_fmt(from_dt) if from_dt else _default_from()
    until = _ch_fmt(to_dt, upper=True) if to_dt else _default_to()
    try:
        return await asyncio.to_thread(
            _fetch_workflow_summary, client, database, tenant_id, since, until,
            group_by, flow_id, campaign_id, pool_id, list(accessible_pools) if accessible_pools else [],
        )
    except Exception as exc:
        logger.warning("query_workflow_summary failed tenant=%s: %s", tenant_id, exc)
        return {"data": [], "meta": {"from_dt": since, "to_dt": until}, "error": "data_unavailable"}


def _fetch_workflow_summary(
    client: Any, db: str, tenant_id: str,
    since: str, until: str,
    group_by: str, flow_id: str | None, campaign_id: str | None,
    pool_id: str | None = None,
    accessible_pools: list[str] | None = None,
) -> dict:
    # Validate group_by — only allow known values
    if group_by not in ("pool_id", "flow_id", "campaign_id"):
        group_by = "pool_id"

    conditions = [
        "tenant_id = {tenant_id:String}",
        "timestamp >= {since:String}",
        "timestamp <= {until:String}",
    ]
    params: dict = {"tenant_id": tenant_id, "since": since, "until": until}
    if pool_id:
        # explicit single-pool filter (user picked one pool from the combo)
        conditions.append("pool_id = {pool_id:String}")
        params["pool_id"] = pool_id
    elif accessible_pools:
        # scope to the user's permission set ("All" = all pools they can see)
        placeholders = ", ".join(f"{{ap_{i}:String}}" for i in range(len(accessible_pools)))
        conditions.append(f"pool_id IN ({placeholders})")
        for i, ap in enumerate(accessible_pools):
            params[f"ap_{i}"] = ap
    if flow_id:
        conditions.append("flow_id = {flow_id:String}")
        params["flow_id"] = flow_id
    if campaign_id:
        conditions.append("campaign_id = {campaign_id:String}")
        params["campaign_id"] = campaign_id

    where = " AND ".join(conditions)

    # group_key: NULL/empty pool/campaign maps to a readable label
    if group_by == "campaign_id":
        group_expr = "if(campaign_id IS NOT NULL AND campaign_id != '', campaign_id, '(sem campanha)')"
    elif group_by == "pool_id":
        group_expr = "if(pool_id IS NOT NULL AND pool_id != '', pool_id, '(sem pool)')"
    else:
        group_expr = "flow_id"

    # Use a subquery that computes the LAST event per instance so that
    # an instance which was suspended and later completed/failed is counted
    # only in its terminal state — not in both suspended AND the terminal bucket.
    result = client.query(f"""
        SELECT
            group_key,
            countIf(has_started)                                                           AS total_triggered,
            countIf(last_event = 'workflow.completed')                                     AS total_completed,
            countIf(last_event = 'workflow.failed')                                        AS total_failed,
            countIf(last_event = 'workflow.timed_out')                                     AS total_timeout,
            countIf(last_event = 'workflow.cancelled')                                     AS total_cancelled,
            countIf(last_event = 'workflow.suspended')                                     AS total_suspended,
            avgIf(final_duration_ms,
                  last_event IN ('workflow.completed', 'workflow.failed')
                  AND final_duration_ms > 0)                                               AS avg_duration_ms
        FROM (
            SELECT
                instance_id,
                {group_expr}                                                               AS group_key,
                countIf(event_type = 'workflow.started') > 0                              AS has_started,
                argMax(event_type, timestamp)                                              AS last_event,
                maxIf(duration_ms,
                      event_type IN ('workflow.completed', 'workflow.failed')
                      AND isNotNull(duration_ms) AND duration_ms > 0)                     AS final_duration_ms
            FROM {db}.workflow_events FINAL
            WHERE {where}
            GROUP BY instance_id, group_key
        )
        WHERE has_started = 1
        GROUP BY group_key
        ORDER BY total_triggered DESC
        LIMIT 500
    """, parameters=params)

    rows = _rows_to_dicts(result)

    # Compute derived rates client-side (avoid division-by-zero in SQL)
    for row in rows:
        triggered = row.get("total_triggered") or 0
        completed = row.get("total_completed") or 0
        failed    = row.get("total_failed")    or 0
        row["completion_rate"] = round(completed / triggered, 4) if triggered else 0.0
        row["failure_rate"]    = round(failed    / triggered, 4) if triggered else 0.0
        # avg_duration_ms may be None if no completed/failed events in range
        if row.get("avg_duration_ms") is None:
            row["avg_duration_ms"] = None

    return {
        "data":     rows,
        "group_by": group_by,
        "meta":     {"total": len(rows), "from_dt": since, "to_dt": until},
    }


# ─── /reports/campaigns ──────────────────────────────────────────────────────

async def query_campaigns_report(
    client:    Any,
    database:  str,
    tenant_id: str,
    from_dt:   str | None = None,
    to_dt:     str | None = None,
    *,
    campaign_id: str | None = None,
    channel:     str | None = None,
    status:      str | None = None,
    page:      int = 1,
    page_size: int = 100,
) -> dict:
    since = _ch_fmt(from_dt) if from_dt else _default_from()
    until = _ch_fmt(to_dt, upper=True) if to_dt else _default_to()
    try:
        return await asyncio.to_thread(
            _fetch_campaigns, client, database, tenant_id, since, until,
            campaign_id, channel, status, page, page_size,
        )
    except Exception as exc:
        logger.warning("query_campaigns_report failed tenant=%s: %s", tenant_id, exc)
        return {"data": [], "summary": [], "meta": _meta(page, page_size, 0, since, until), "error": "data_unavailable"}


def _fetch_campaigns(
    client: Any, db: str, tenant_id: str,
    since: str, until: str,
    campaign_id: str | None, channel: str | None, status: str | None,
    page: int, page_size: int,
) -> dict:
    conditions = [
        "tenant_id = {tenant_id:String}",
        f"timestamp >= '{since}'",
        f"timestamp < '{until}'",
    ]
    params: dict = {"tenant_id": tenant_id}

    if campaign_id:
        conditions.append("campaign_id = {campaign_id:String}")
        params["campaign_id"] = campaign_id
    if channel:
        conditions.append("channel = {channel:String}")
        params["channel"] = channel
    if status:
        conditions.append("status = {status:String}")
        params["status"] = status

    where = " AND ".join(conditions)
    offset = (page - 1) * page_size

    total = _count(client, f"SELECT count() FROM {db}.collect_events FINAL WHERE {where}", params)

    result = client.query(f"""
        SELECT
            collect_token, tenant_id, instance_id, flow_id, campaign_id,
            step_id, target_type, channel, interaction, status,
            send_at, responded_at, elapsed_ms, timestamp
        FROM {db}.collect_events FINAL
        WHERE {where}
        ORDER BY timestamp DESC
        LIMIT {page_size} OFFSET {offset}
    """, parameters=params)

    # Aggregate summary: one row per campaign_id
    agg_result = client.query(f"""
        SELECT
            campaign_id,
            count()                                                    AS total,
            countIf(status = 'responded')                              AS responded,
            countIf(status = 'timed_out')                              AS timed_out,
            countIf(status = 'sent')                                   AS sent,
            countIf(status = 'requested')                              AS requested,
            round(countIf(status = 'responded') * 100.0 / count(), 1) AS response_rate_pct,
            avg(if(status = 'responded', elapsed_ms, NULL))            AS avg_elapsed_ms
        FROM {db}.collect_events FINAL
        WHERE {where} AND campaign_id IS NOT NULL
        GROUP BY campaign_id
        ORDER BY total DESC
        LIMIT 100
    """, parameters=params)

    return {
        "data":    _rows_to_dicts(result),
        "summary": _rows_to_dicts(agg_result),
        "meta":    _meta(page, page_size, total, since, until),
    }


# ─── /reports/participation ───────────────────────────────────────────────────

async def query_participation_report(
    client:    Any,
    database:  str,
    tenant_id: str,
    from_dt:   str | None = None,
    to_dt:     str | None = None,
    *,
    session_id:       str | None       = None,
    pool_id:          str | None       = None,
    agent_type_id:    str | None       = None,
    role:             str | None       = None,
    accessible_pools: list[str] | None = None,
    page:      int = 1,
    page_size: int = 100,
) -> dict:
    since = _ch_fmt(from_dt) if from_dt else _default_from()
    until = _ch_fmt(to_dt, upper=True) if to_dt else _default_to()
    if accessible_pools is not None and not accessible_pools:
        return {"data": [], "meta": _meta(page, page_size, 0, since, until)}
    try:
        return await asyncio.to_thread(
            _fetch_participation, client, database, tenant_id, since, until,
            session_id, pool_id, agent_type_id, role, accessible_pools, page, page_size,
        )
    except Exception as exc:
        logger.warning("query_participation_report failed tenant=%s: %s", tenant_id, exc)
        return {"data": [], "meta": _meta(page, page_size, 0, since, until), "error": "data_unavailable"}


def _fetch_participation(
    client: Any, db: str, tenant_id: str,
    since: str, until: str,
    session_id: str | None, pool_id: str | None,
    agent_type_id: str | None, role: str | None,
    accessible_pools: list[str] | None,
    page: int, page_size: int,
) -> dict:
    conditions = [
        "tenant_id = {tenant_id:String}",
        f"timestamp >= '{since}'",
        f"timestamp < '{until}'",
    ]
    params: dict = {"tenant_id": tenant_id}

    if session_id:
        conditions.append("session_id = {session_id:String}")
        params["session_id"] = session_id
    if pool_id:
        conditions.append("pool_id = {pool_id:String}")
        params["pool_id"] = pool_id
    if agent_type_id:
        conditions.append("agent_type_id = {agent_type_id:String}")
        params["agent_type_id"] = agent_type_id
    if role:
        conditions.append("role = {role:String}")
        params["role"] = role
    _apply_pool_scope(conditions, accessible_pools)

    where = " AND ".join(conditions)
    offset = (page - 1) * page_size

    # Use FINAL so ReplacingMergeTree deduplication is applied at query time
    total = _count(
        client,
        f"SELECT count() FROM {db}.participation_intervals FINAL WHERE {where}",
        params,
    )

    result = client.query(f"""
        SELECT
            event_id, session_id, tenant_id,
            participant_id, pool_id, agent_type_id,
            role, agent_type, conference_id,
            joined_at, left_at, duration_ms,
            timestamp
        FROM {db}.participation_intervals FINAL
        WHERE {where}
        ORDER BY timestamp DESC
        LIMIT {page_size} OFFSET {offset}
    """, parameters=params)

    return {"data": _rows_to_dicts(result), "meta": _meta(page, page_size, total, since, until)}


# ─── /reports/segments (Arc 5 — ContactSegment) ──────────────────────────────

async def query_segments_report(
    client:    Any,
    database:  str,
    tenant_id: str,
    from_dt:   str | None = None,
    to_dt:     str | None = None,
    *,
    session_id:             str | None       = None,
    root_session_id:        str | None       = None,
    pool_id:                str | None       = None,
    agent_type_id:          str | None       = None,
    role:                   str | None       = None,
    outcome:                str | None       = None,
    accessible_pools:       list[str] | None = None,
    supervised_agent_types: list[str] | None = None,
    origin:                 "str | list[str]" = "live",
    page:      int = 1,
    page_size: int = 100,
) -> dict:
    since = _ch_fmt(from_dt) if from_dt else _default_from()
    until = _ch_fmt(to_dt, upper=True) if to_dt else _default_to()
    if accessible_pools is not None and not accessible_pools:
        return {"data": [], "meta": _meta(page, page_size, 0, since, until)}
    if supervised_agent_types is not None and not supervised_agent_types:
        return {"data": [], "meta": _meta(page, page_size, 0, since, until)}
    try:
        return await asyncio.to_thread(
            _fetch_segments, client, database, tenant_id, since, until,
            session_id, pool_id, agent_type_id, role, outcome,
            accessible_pools, supervised_agent_types, page, page_size,
            origin, root_session_id,
        )
    except Exception as exc:
        logger.warning("query_segments_report failed tenant=%s: %s", tenant_id, exc)
        return {"data": [], "meta": _meta(page, page_size, 0, since, until), "error": "data_unavailable"}


def _fetch_segments(
    client: Any, db: str, tenant_id: str,
    since: str, until: str,
    session_id: str | None, pool_id: str | None,
    agent_type_id: str | None, role: str | None,
    outcome: str | None,
    accessible_pools: list[str] | None,
    supervised_agent_types: list[str] | None,
    page: int, page_size: int,
    origin: "str | list[str]" = "live",
    root_session_id: str | None = None,
) -> dict:
    conditions = ["tenant_id = {tenant_id:String}"]
    params: dict = {"tenant_id": tenant_id}

    # D10 — a janela é CONDICIONAL, como em `_fetch_journeys`. Sem a isenção, uma
    # journey que atravessa semanas volta truncada em SILÊNCIO: o processo é o
    # escopo, e o escopo do processo não é a semana em que se olha para ele.
    #
    # `root_session_id` não é coluna de `segments` (vive em `sessions`) — daí a
    # subconsulta, e não um `IN {jroots}` direto. Ela carrega o MESMO union-find
    # (proveniência ∪ alias) que `/reports/journeys` usa, senão as duas superfícies
    # sobre o mesmo processo devolveriam conjuntos diferentes de sessões.
    if root_session_id:
        _resolved = _journey_resolved_map(client, db, tenant_id)
        # `sorted(...)`, não o set cru: `_journey_member_roots` devolve `set[str]` e o
        # binding de `Array(String)` precisa de sequência ordenável — é o que
        # `_fetch_journeys` já faz (`:968`). Passar o set daria erro de serialização,
        # que o `except` de `query_segments_report` converteria em `data_unavailable`
        # — indistinguível de "não há segmentos" para quem só olha a tela.
        params["jroots"] = sorted(_journey_member_roots(_resolved, root_session_id))
        conditions.append(
            "session_id IN ("
            f"SELECT session_id FROM {db}.sessions FINAL "
            "WHERE tenant_id = {tenant_id:String} "
            "AND root_session_id IN {jroots:Array(String)})"
        )
    else:
        conditions.append(f"started_at >= '{since}'")
        conditions.append(f"started_at < '{until}'")

    if session_id:
        conditions.append("session_id = {session_id:String}")
        params["session_id"] = session_id
    if pool_id:
        conditions.append("pool_id = {pool_id:String}")
        params["pool_id"] = pool_id
    if agent_type_id:
        conditions.append("agent_type_id = {agent_type_id:String}")
        params["agent_type_id"] = agent_type_id
    if role:
        conditions.append("role = {role:String}")
        params["role"] = role
    if outcome:
        conditions.append("outcome = {outcome:String}")
        params["outcome"] = outcome
    _apply_pool_scope(conditions, accessible_pools)
    _apply_agent_scope(conditions, supervised_agent_types)
    _apply_origin_scope(conditions, origin)   # substrate isolation (ADR)

    where  = " AND ".join(conditions)
    offset = (page - 1) * page_size

    # FINAL applies ReplacingMergeTree dedup so ended rows shadow joined rows
    total = _count(
        client,
        f"SELECT count() FROM {db}.segments FINAL WHERE {where}",
        params,
    )

    result = client.query(f"""
        SELECT
            segment_id, session_id, tenant_id,
            participant_id, pool_id, agent_type_id,
            flow_id, user_id, user_login,
            instance_id, role, agent_type,
            parent_segment_id, sequence_index,
            started_at, ended_at, duration_ms,
            outcome, close_reason, handoff_reason, issue_status,
            -- Prosa do wrap-up: gravada em TODA disposição, inclusive resolvido.
            wrapup_summary, wrapup_next_steps,
            conference_id
        FROM {db}.segments FINAL
        WHERE {where}
        ORDER BY started_at DESC
        LIMIT {page_size} OFFSET {offset}
    """, parameters=params)

    meta = _meta(page, page_size, total, since, until)
    # A janela isenta NÃO pode sair do endpoint parecendo aplicada. `_meta` publica
    # `from_dt`/`to_dt` sempre, e no ramo do processo eles não filtraram nada — quem
    # lê o cabeçalho concluiria que a lista está recortada por período e que os
    # segmentos antigos "sumiram". Marcador explícito, aditivo (não quebra leitor).
    # (2026-08-14: `/reports/journeys` e `/reports/sessions` ganharam o mesmo
    # marcador; a nota anterior dizia que journeys seguia sem ele.)
    meta["window_applied"] = not bool(root_session_id)
    return {"data": _rows_to_dicts(result), "meta": meta}


# ─── /reports/wrapup-summary (I5 / ADR § D7b, fatia 2) ───────────────────────
#
# HISTÓRICO do trabalho author-bound, contraparte retrospectiva do Monitor ›
# Pendências (que só mostra o VIVO). Não há dado novo a produzir: o segmento do
# wrap-up já nasce com `close_reason` do trio, duração e `user_login` — a fatia 2
# é a lente, como a D7b previu.
#
# ESCOPO idêntico ao da fatia 1: pools com sufixo `-int` (trabalho author-bound).
# O trio de `close_reason` SOZINHO não bastaria como filtro — `task_submitted` é
# escrito por qualquer claimante de fila pull, incluindo APROVAÇÃO, que é pooled e
# mora num pool de contato. Filtrar só pelo trio misturaria os dois regimes e o
# número de "wrap-ups vencidos" incluiria aprovações. Escopo = `-int`; trio =
# classificação. Exatamente o par da tela viva.

_WRAPUP_CLOSE_REASONS = ("task_submitted", "acw_expired", "acw_supervisor_closed")

_WRAPUP_GROUP_COLS = {
    # Identidade legível do atendente. `user_login` (e não `user_id`) porque é o que
    # o produtor carimba no segmento — ver `participant_meta`. O `user_id` viaja
    # junto, derivado de `participant_id` (`human-{uid}`), para casar com o
    # `assigned_to` da superfície VIVA, que é chaveada por user_id.
    "agent": "user_login",
    "pool":  "pool_id",
}


async def query_wrapup_summary(
    client:    Any,
    database:  str,
    tenant_id: str,
    from_dt:   str | None = None,
    to_dt:     str | None = None,
    *,
    group_by:         str              = "agent",
    pool_id:          str | None       = None,
    accessible_pools: list[str] | None = None,
    origin:           "str | list[str]" = "live",
) -> dict:
    """
    Agregado do desfecho das pendências author-bound no período.

      submitted         — o humano entregou o formulário (`task_submitted`)
      expired           — o prazo venceu sem entrega (`acw_expired`)
      supervisor_closed — um supervisor encerrou (`acw_supervisor_closed`)
      avg_fill_ms       — duração MÉDIA dos submetidos = o tempo de ACW real

    `avg_fill_ms` cobre só os submetidos de propósito: a duração de um item
    expirado é o intervalo claim→prazo, que mede abandono, não trabalho. Somar os
    dois produziria uma média plausível que não é ACW nem outra coisa.
    """
    since = _ch_fmt(from_dt) if from_dt else _default_from()
    until = _ch_fmt(to_dt, upper=True) if to_dt else _default_to()
    if accessible_pools is not None and not accessible_pools:
        return {"data": [], "totals": {}, "meta": _meta(1, 0, 0, since, until)}
    try:
        return await asyncio.to_thread(
            _fetch_wrapup_summary, client, database, tenant_id, since, until,
            group_by, pool_id, accessible_pools, origin,
        )
    except Exception as exc:
        # Log em WARNING com o texto da exceção: um `except` mudo aqui repetiria o
        # engano `duration_ms × handle_time_ms`, que ficou anos invisível.
        logger.warning("query_wrapup_summary failed tenant=%s: %s", tenant_id, exc)
        return {
            "data": [], "totals": {}, "meta": _meta(1, 0, 0, since, until),
            "error": "data_unavailable",
        }


def _fetch_wrapup_summary(
    client: Any, db: str, tenant_id: str,
    since: str, until: str,
    group_by: str, pool_id: str | None,
    accessible_pools: list[str] | None,
    origin: "str | list[str]" = "live",
) -> dict:
    group_col = _WRAPUP_GROUP_COLS.get(group_by, _WRAPUP_GROUP_COLS["agent"])
    reasons   = ", ".join(f"'{r}'" for r in _WRAPUP_CLOSE_REASONS)

    conditions = [
        "tenant_id = {tenant_id:String}",
        # `segments` usa started_at/duration_ms (`sessions` usa opened_at/
        # handle_time_ms) — trocar dá UNKNOWN_IDENTIFIER ou vazio mudo.
        f"started_at >= '{since}'",
        f"started_at < '{until}'",
        "agent_type = 'human'",
        "endsWith(pool_id, '-int')",
        f"close_reason IN ({reasons})",
    ]
    params: dict = {"tenant_id": tenant_id}
    if pool_id:
        conditions.append("pool_id = {pool_id:String}")
        params["pool_id"] = pool_id
    _apply_pool_scope(conditions, accessible_pools)
    _apply_origin_scope(conditions, origin)

    where = " AND ".join(conditions)

    # ATENÇÃO — alias NUNCA pode repetir nome de coluna real desta tabela.
    # `pool_id` e `user_id` EXISTEM em `segments`; aliasar um agregado com esses
    # nomes faz o alias sombrear a coluna que o `WHERE` usa (`endsWith(pool_id,…)`)
    # e a query falha inteira. É o mesmo defeito já catalogado na lente `deploy`
    # (`any(attr.agent_type)` colidindo com o `WHERE attr.agent_type`). Por isso os
    # agregados saem com sufixo `_ref` e são renomeados em Python, abaixo — o
    # contrato da API segue `pool_id`/`user_id`.
    result = client.query(f"""
        SELECT
            {group_col} AS group_key,
            any(pool_id) AS pool_id_ref,
            -- user_id derivado de participant_id (`human-{{uid}}`): é o que casa
            -- com o `assigned_to` da superfície viva, que é chaveada por user_id.
            any(if(startsWith(participant_id, 'human-'),
                   substring(participant_id, 7), '')) AS user_id_ref,
            count() AS total,
            countIf(close_reason = 'task_submitted')        AS submitted,
            countIf(close_reason = 'acw_expired')           AS expired,
            countIf(close_reason = 'acw_supervisor_closed') AS supervisor_closed,
            round(avgIf(duration_ms, close_reason = 'task_submitted' AND duration_ms > 0)) AS avg_fill_ms,
            max(started_at) AS last_seen
        FROM {db}.segments FINAL
        WHERE {where}
        GROUP BY group_key
        ORDER BY total DESC
    """, parameters=params)

    rows = _rows_to_dicts(result)
    for r in rows:
        r["pool_id"] = r.pop("pool_id_ref", "")
        r["user_id"] = r.pop("user_id_ref", "")
    totals = {
        "total":             sum(int(r.get("total") or 0)             for r in rows),
        "submitted":         sum(int(r.get("submitted") or 0)         for r in rows),
        "expired":           sum(int(r.get("expired") or 0)           for r in rows),
        "supervisor_closed": sum(int(r.get("supervisor_closed") or 0) for r in rows),
    }
    # Taxa de não-preenchimento — a pergunta que a D4 queria responder. Derivada
    # aqui (uma vez) em vez de em cada consumidor, que é onde ela viraria três
    # definições diferentes.
    totals["unfilled_rate"] = (
        round((totals["expired"] + totals["supervisor_closed"]) / totals["total"], 4)
        if totals["total"] else None
    )
    return {"data": rows, "totals": totals, "meta": _meta(1, len(rows), len(rows), since, until)}


# ─── /reports/agents/performance (Arc 5 — aggregate per agent) ───────────────

async def query_agent_performance_report(
    client:    Any,
    database:  str,
    tenant_id: str,
    from_dt:   str | None = None,
    to_dt:     str | None = None,
    *,
    pool_id:                str | None       = None,
    agent_type_id:          str | None       = None,
    role:                   str | None       = None,
    accessible_pools:       list[str] | None = None,
    supervised_agent_types: list[str] | None = None,
    origin:                 "str | list[str]" = "live",
) -> dict:
    """
    Aggregate performance metrics per (agent_type_id, pool_id, role).

    Reads from analytics.segments FINAL (Arc 5 ReplacingMergeTree).
    Returns one row per distinct combination — no pagination needed since
    the cardinality is bounded by the number of registered agent types × pools.

    Metrics:
      total_sessions     — count of participation windows
      avg_duration_ms    — mean handle time (null when all duration_ms are null)
      escalation_rate    — fraction with outcome = 'escalated'
      handoff_rate       — fraction with a non-empty handoff_reason
      resolved_count / escalated_count / transferred_count /
        abandoned_count / timeout_count / handoff_count — raw breakdowns
    """
    since = _ch_fmt(from_dt) if from_dt else _default_from()
    until = _ch_fmt(to_dt, upper=True) if to_dt else _default_to()
    if accessible_pools is not None and not accessible_pools:
        return {"data": [], "meta": {"total": 0, "from_dt": since, "to_dt": until}}
    if supervised_agent_types is not None and not supervised_agent_types:
        return {"data": [], "meta": {"total": 0, "from_dt": since, "to_dt": until}}
    try:
        return await asyncio.to_thread(
            _fetch_agent_performance,
            client, database, tenant_id, since, until,
            pool_id, agent_type_id, role, accessible_pools, supervised_agent_types,
            origin,
        )
    except Exception as exc:
        logger.warning(
            "query_agent_performance_report failed tenant=%s: %s", tenant_id, exc
        )
        return {"data": [], "error": "data_unavailable"}


def _fetch_agent_performance(
    client:          Any,
    db:              str,
    tenant_id:       str,
    since:           str,
    until:           str,
    pool_id:         str | None,
    agent_type_id:   str | None,
    role:            str | None,
    accessible_pools:       list[str] | None = None,
    supervised_agent_types: list[str] | None = None,
    origin:                 "str | list[str]" = "live",
) -> dict:
    conditions = [
        "tenant_id = {tenant_id:String}",
        f"started_at >= '{since}'",
        f"started_at < '{until}'",
        # Fase B (queue-attended-model): synthetic admission segments
        # (outage, duration 0) never count as agent performance.
        "agent_type != 'system'",
    ]
    params: dict = {"tenant_id": tenant_id}

    if pool_id:
        conditions.append("pool_id = {pool_id:String}")
        params["pool_id"] = pool_id
    if agent_type_id:
        conditions.append("agent_type_id = {agent_type_id:String}")
        params["agent_type_id"] = agent_type_id
    if role:
        conditions.append("role = {role:String}")
        params["role"] = role
    _apply_pool_scope(conditions, accessible_pools)
    _apply_agent_scope(conditions, supervised_agent_types)
    _apply_origin_scope(conditions, origin)   # substrate isolation (ADR)

    where = " AND ".join(conditions)

    # C1b — agent identity dimension: humans by user_id (display user_login),
    # AI by flow_id (deployed skill). The legacy agent_type_id collapses every
    # human in a pool into one synthetic human_agent_{pool} row. Fallback to
    # agent_type_id when user_id/flow_id are empty (historical segments).
    result = client.query(f"""
        SELECT
            agent_key,
            any(agent_type_id)                                            AS agent_type_id,
            any(agent_type)                                               AS agent_type,
            anyIf(user_login, user_login != '')                           AS user_login,
            anyIf(flow_id, flow_id != '')                                 AS flow_id,
            anyIf(user_id, user_id != '')                                 AS user_id,
            pool_id,
            role,
            count()                                                       AS total_sessions,
            avgOrNull(duration_ms)                                        AS avg_duration_ms,
            countIf(outcome = 'resolved')                                 AS resolved_count,
            countIf(outcome = 'escalated')                                AS escalated_count,
            countIf(outcome = 'transferred')                              AS transferred_count,
            countIf(outcome = 'abandoned')                                AS abandoned_count,
            countIf(outcome = 'timeout')                                  AS timeout_count,
            countIf(handoff_reason IS NOT NULL AND handoff_reason != '')  AS handoff_count,
            if(count() > 0,
               countIf(outcome = 'escalated') / count(),
               0.0)                                                       AS escalation_rate,
            if(count() > 0,
               countIf(handoff_reason IS NOT NULL AND handoff_reason != '') / count(),
               0.0)                                                       AS handoff_rate
        FROM (
            SELECT
                *,
                if(agent_type = 'human',
                   if(user_id != '', user_id, agent_type_id),
                   if(flow_id != '', flow_id, agent_type_id))             AS agent_key
            FROM {db}.segments FINAL
            WHERE {where}
        )
        GROUP BY agent_key, pool_id, role
        ORDER BY agent_key, pool_id, role
    """, parameters=params)

    rows = _rows_to_dicts(result)
    return {
        "data": rows,
        "meta": {
            "total":   len(rows),
            "from_dt": since,
            "to_dt":   until,
        },
    }


# ─── /reports/evaluations ────────────────────────────────────────────────────

async def query_evaluations_report(
    client:    Any,
    database:  str,
    tenant_id: str,
    from_dt:   str | None = None,
    to_dt:     str | None = None,
    *,
    campaign_id:  str | None = None,
    form_id:      str | None = None,
    evaluator_id: str | None = None,
    eval_status:  str | None = None,
    page:      int = 1,
    page_size: int = 100,
) -> dict:
    """
    Returns individual evaluation results (one row per evaluated session).
    Filters: campaign_id, form_id, evaluator_id, eval_status.
    """
    since = _ch_fmt(from_dt) if from_dt else _default_from()
    until = _ch_fmt(to_dt, upper=True) if to_dt else _default_to()
    try:
        return await asyncio.to_thread(
            _fetch_evaluations, client, database, tenant_id, since, until,
            campaign_id, form_id, evaluator_id, eval_status, page, page_size,
        )
    except Exception as exc:
        logger.warning("query_evaluations_report failed tenant=%s: %s", tenant_id, exc)
        return {"data": [], "meta": _meta(page, page_size, 0, since, until), "error": "data_unavailable"}


def _session_agent_attribution_sql(db: str, origin: "str | list[str]" = "live") -> str:
    """
    F2 (bancada de agentes — analytics-agents-workbench.md §13): atribuição de
    sessão → agente avaliado. Último segmento PRIMARY não-sintético da sessão
    (argMax por sequence_index — consistente com a Fase A: outcome de sessão =
    último primary). agent_key = user_id (humano) / flow_id (IA), fallback
    agent_type_id — mesmo padrão de query_agent_performance_report. Segmentos
    sintéticos excluídos (agent_type='system'; 'queue' já cai no role='primary').
    O parâmetro {tenant_id:String} é fornecido pela query externa.
    """
    # Nota CH (ILLEGAL_AGGREGATION): alias de SELECT é visível no WHERE no
    # ClickHouse — aliasar argMax(...) AS agent_type no MESMO escopo do
    # WHERE agent_type != 'system' faz o filtro resolver para o agregado.
    # Por isso o filtro vive num subquery interno (só colunas reais) e a
    # agregação usa nomes internos não-colidentes (sak/sat/spid/sul).
    # Substrate isolation (ADR): a atribuição lê segments → filtra por origem
    # (default 'live'); centraliza o filtro p/ as lentes quality/deploy/session_nps.
    _oc: list[str] = []
    _apply_origin_scope(_oc, origin)
    origin_clause = _oc[0]
    return f"""
        SELECT
            session_id,
            argMax(sak,  sequence_index) AS agent_key,
            argMax(sat,  sequence_index) AS agent_type,
            argMax(spid, sequence_index) AS pool_id,
            argMax(sul,  sequence_index) AS user_login,
            argMax(ssta, sequence_index) AS session_started_at
        FROM (
            SELECT
                session_id,
                if(agent_type = 'human',
                   if(user_id != '', user_id, agent_type_id),
                   if(flow_id != '', flow_id, agent_type_id)) AS sak,
                agent_type  AS sat,
                pool_id     AS spid,
                user_login  AS sul,
                started_at  AS ssta,
                sequence_index
            FROM {db}.segments FINAL
            WHERE tenant_id = {{tenant_id:String}}
              AND role = 'primary'
              AND agent_type != 'system'
              AND {origin_clause}
        )
        GROUP BY session_id
    """


def _fetch_evaluations(
    client: Any, db: str, tenant_id: str,
    since: str, until: str,
    campaign_id: str | None, form_id: str | None,
    evaluator_id: str | None, eval_status: str | None,
    page: int, page_size: int,
) -> dict:
    conditions = [
        "tenant_id = {tenant_id:String}",
        f"timestamp >= '{since}'",
        f"timestamp < '{until}'",
    ]
    params: dict = {"tenant_id": tenant_id}

    if campaign_id:
        conditions.append("campaign_id = {campaign_id:String}")
        params["campaign_id"] = campaign_id
    if form_id:
        conditions.append("form_id = {form_id:String}")
        params["form_id"] = form_id
    if evaluator_id:
        conditions.append("evaluator_id = {evaluator_id:String}")
        params["evaluator_id"] = evaluator_id
    if eval_status:
        conditions.append("eval_status = {eval_status:String}")
        params["eval_status"] = eval_status

    where = " AND ".join(conditions)
    offset = (page - 1) * page_size

    total = _count(
        client,
        f"SELECT count() FROM {db}.evaluation_results FINAL WHERE {where}",
        params,
    )

    # F2 (bancada de agentes): LEFT JOIN com a atribuição por sessão — cada
    # avaliação ganha o agente AVALIADO (agent_key/agent_type/pool_id/user_login),
    # retroativo a todas as linhas (join em query-time; sem mudança de ingest).
    attr_sql = _session_agent_attribution_sql(db)
    result = client.query(f"""
        SELECT
            er.result_id, er.instance_id, er.session_id, er.tenant_id,
            er.evaluator_id, er.form_id, er.campaign_id,
            er.overall_score, er.eval_status, er.locked,
            er.compliance_flags, er.timestamp,
            attr.agent_key  AS agent_key,
            attr.agent_type AS agent_type,
            attr.pool_id    AS pool_id,
            attr.user_login AS user_login
        FROM (
            SELECT * FROM {db}.evaluation_results FINAL WHERE {where}
        ) AS er
        LEFT JOIN ({attr_sql}) AS attr ON er.session_id = attr.session_id
        ORDER BY er.timestamp DESC
        LIMIT {page_size} OFFSET {offset}
    """, parameters=params)

    return {"data": _rows_to_dicts(result), "meta": _meta(page, page_size, total, since, until)}


# ─── /reports/evaluations/summary ─────────────────────────────────────────────

async def query_evaluations_summary(
    client:    Any,
    database:  str,
    tenant_id: str,
    from_dt:   str | None = None,
    to_dt:     str | None = None,
    *,
    campaign_id: str | None = None,
    form_id:     str | None = None,
    group_by:    str = "campaign_id",   # campaign_id | evaluator_id | form_id | date | agent_key | pool_id
) -> dict:
    """
    Aggregated evaluation summary: avg score, score distribution, count by status.
    group_by controls the breakdown dimension.

    F2 (bancada de agentes): group_by agent_key | pool_id agrupa pelo agente
    AVALIADO (join com segments — último primary não-sintético da sessão).
    """
    since = _ch_fmt(from_dt) if from_dt else _default_from()
    until = _ch_fmt(to_dt, upper=True) if to_dt else _default_to()
    # Whitelist grouping dimensions
    allowed_groups = {"campaign_id", "evaluator_id", "form_id", "date", "agent_key", "pool_id"}
    if group_by not in allowed_groups:
        group_by = "campaign_id"
    try:
        return await asyncio.to_thread(
            _fetch_evaluations_summary, client, database, tenant_id, since, until,
            campaign_id, form_id, group_by,
        )
    except Exception as exc:
        logger.warning("query_evaluations_summary failed tenant=%s: %s", tenant_id, exc)
        return {"data": [], "meta": {"from_dt": since, "to_dt": until}, "error": "data_unavailable"}


def _fetch_evaluations_summary(
    client: Any, db: str, tenant_id: str,
    since: str, until: str,
    campaign_id: str | None, form_id: str | None,
    group_by: str,
) -> dict:
    conditions = [
        "tenant_id = {tenant_id:String}",
        f"timestamp >= '{since}'",
        f"timestamp < '{until}'",
    ]
    params: dict = {"tenant_id": tenant_id}

    if campaign_id:
        conditions.append("campaign_id = {campaign_id:String}")
        params["campaign_id"] = campaign_id
    if form_id:
        conditions.append("form_id = {form_id:String}")
        params["form_id"] = form_id

    where = " AND ".join(conditions)

    # Resolve FROM + GROUP BY.
    # F2 (bancada de agentes): agrupamentos por agente AVALIADO (agent_key /
    # pool_id) exigem o join com a atribuição por sessão (segments). Os filtros
    # de evaluation_results são aplicados no subquery interno; avaliações sem
    # segmento primary correspondente caem em group_key '' (não-atribuídas).
    if group_by in ("agent_key", "pool_id"):
        attr_sql    = _session_agent_attribution_sql(db)
        group_col   = f"attr.{group_by}"
        from_clause = (
            f"(SELECT * FROM {db}.evaluation_results FINAL WHERE {where}) AS er "
            f"LEFT JOIN ({attr_sql}) AS attr ON er.session_id = attr.session_id"
        )
        extra_select = (
            """
            any(attr.agent_type)                         AS agent_type,
            any(attr.pool_id)                            AS pool_id,
            any(attr.user_login)                         AS user_login,"""
            if group_by == "agent_key" else ""
        )
    else:
        group_col    = "toDate(timestamp)" if group_by == "date" else group_by
        from_clause  = f"{db}.evaluation_results FINAL"
        extra_select = ""

    # No caminho com join os filtros já foram aplicados no subquery interno.
    where_clause = "1 = 1" if group_by in ("agent_key", "pool_id") else where

    result = client.query(f"""
        SELECT
            {group_col}                                  AS group_key,{extra_select}
            count()                                      AS total_evaluated,
            countIf(eval_status = 'submitted')           AS count_submitted,
            countIf(eval_status = 'approved')            AS count_approved,
            countIf(eval_status = 'rejected')            AS count_rejected,
            countIf(eval_status = 'contested')           AS count_contested,
            countIf(eval_status = 'locked')              AS count_locked,
            countIf(locked = 1)                          AS count_locked_flag,
            round(avg(overall_score), 4)                 AS avg_score,
            round(min(overall_score), 4)                 AS min_score,
            round(max(overall_score), 4)                 AS max_score,
            countIf(overall_score >= 0.9)                AS score_excellent,
            countIf(overall_score >= 0.7 AND overall_score < 0.9) AS score_good,
            countIf(overall_score >= 0.5 AND overall_score < 0.7) AS score_fair,
            countIf(overall_score < 0.5)                 AS score_poor,
            countIf(length(compliance_flags) > 0)        AS with_compliance_flags
        FROM {from_clause}
        WHERE {where_clause}
        GROUP BY {group_col}
        ORDER BY group_key ASC
    """, parameters=params)

    rows = _rows_to_dicts(result)
    return {
        "data":     rows,
        "group_by": group_by,
        "meta":     {"total": len(rows), "from_dt": since, "to_dt": until},
    }


# ─── /reports/customers/{id}/360 — Cliente 360 (C1b, ADR §D4) ─────────────────

async def query_customer_360(
    client:    Any,
    database:  str,
    tenant_id: str,
    customer_id: str,
    *,
    origin: "str | list[str]" = "live",
) -> dict:
    """Cliente 360 agregado por `customer_id` (ADR `adr-customer-360-two-surfaces.md` §D4).

    Junta três leituras sobre o MESMO cliente, cada uma fail-soft e independente:
      - contacts: resumo de `sessions` (total/resolvidos/abertos/canais/último contato);
      - quality:  `evaluation_finalized` (modo Oficial — o invariante de qualidade),
                  join às sessões do cliente por `session_id`;
      - surveys:  `session_signal` (voz do cliente — NPS/CSAT/…), por métrica.

    Nem `evaluation_finalized` nem `session_signal` carregam `customer_id` — ambos têm
    `session_id`, então o vínculo é sempre via subquery no conjunto de sessões do cliente.
    `origin='live'` por default (isolamento de substrato) — a superfície é operacional.
    """
    internal_pools = await _internal_pools_for(tenant_id)
    try:
        return await asyncio.to_thread(
            _fetch_customer_360, client, database, tenant_id, customer_id, origin,
            internal_pools,
        )
    except Exception as exc:
        logger.warning("query_customer_360 failed tenant=%s customer=%s: %s",
                       tenant_id, customer_id, exc)
        return {"customer_id": customer_id, "contacts": None, "quality": None,
                "surveys": [], "error": "data_unavailable"}


def _fetch_customer_360(
    client: Any, db: str, tenant_id: str, customer_id: str,
    origin: "str | list[str]",
    internal_pools: "frozenset[str] | None" = None,
) -> dict:
    params = {"tenant_id": tenant_id, "customer_id": customer_id}

    # Conjunto de sessões do cliente (origin-scoped) — reusado como subquery nos joins.
    sess_conds = [
        "tenant_id = {tenant_id:String}",
        "customer_id = {customer_id:String}",
    ]
    _apply_origin_scope(sess_conds, origin)
    sessions_subquery = (
        f"SELECT session_id FROM {db}.sessions FINAL WHERE {' AND '.join(sess_conds)}"
    )

    # ── contacts ──────────────────────────────────────────────────────────────
    # E2f: o bloco de contatos usa o escopo de CONTATO (o subquery acima, usado
    # pelos joins de qualidade/sinal, segue com todas as sessões do cliente — uma
    # avaliação do segmento de wrap-up continua sendo do cliente).
    contact_conds = list(sess_conds)
    _apply_contact_scope(contact_conds, internal_pools)
    contacts: dict | None = None
    try:
        r = client.query(f"""
            SELECT
                count()                                          AS total,
                countIf(outcome = 'resolved')                    AS resolved,
                countIf(COALESCE(status, 'closed') != 'closed')  AS open_count,
                arrayFilter(x -> x != '', groupUniqArray(channel)) AS channels,
                max(opened_at)                                   AS last_contact_at
            FROM {db}.sessions FINAL
            WHERE {' AND '.join(contact_conds)}
        """, parameters=params)
        rows = _rows_to_dicts(r)
        contacts = rows[0] if rows else None
    except Exception as exc:
        logger.warning("customer_360 contacts failed: %s", exc)

    # ── quality (evaluation_finalized — Oficial) ────────────────────────────────
    quality: dict | None = None
    try:
        r = client.query(f"""
            SELECT
                count()                                AS count,
                round(avg(final_score), 4)             AS avg_score,
                round(min(final_score), 4)             AS min_score,
                round(max(final_score), 4)             AS max_score,
                argMax(final_score, timestamp)         AS latest_score,
                max(timestamp)                         AS latest_at
            FROM {db}.evaluation_finalized FINAL
            WHERE tenant_id = {{tenant_id:String}}
              AND session_id IN ({sessions_subquery})
        """, parameters=params)
        rows = _rows_to_dicts(r)
        # count=0 ⇒ sem avaliações finalizadas p/ o cliente (mantém None, não "0 vazio").
        quality = rows[0] if rows and rows[0].get("count") else None
    except Exception as exc:
        logger.warning("customer_360 quality failed: %s", exc)

    # ── surveys (session_signal — voz do cliente, por métrica) ──────────────────
    surveys: list[dict] = []
    try:
        r = client.query(f"""
            SELECT
                metric,
                count()                                AS count,
                round(avg(value_num), 4)               AS avg_value,
                argMax(value_num, captured_at)         AS latest_value,
                argMax(value_label, captured_at)       AS latest_label,
                max(captured_at)                       AS latest_at
            FROM {db}.session_signal FINAL
            WHERE tenant_id = {{tenant_id:String}}
              AND session_id IN ({sessions_subquery})
            GROUP BY metric
            ORDER BY metric
        """, parameters=params)
        surveys = _rows_to_dicts(r)
    except Exception as exc:
        logger.warning("customer_360 surveys failed: %s", exc)

    return {
        "customer_id": customer_id,
        "contacts":    contacts,
        "quality":     quality,
        "surveys":     surveys,
    }


# ─── /reports/quality — T11: Oficial × Operacional (§17.3) ────────────────────

_QUALITY_GROUPS = {"campaign_id", "finalize_reason", "segment_id", "form_version",
                   "evaluated_agent_type", "date"}


async def query_quality_report(
    client:    Any,
    database:  str,
    tenant_id: str,
    from_dt:   str | None = None,
    to_dt:     str | None = None,
    *,
    mode:            str = "oficial",   # "oficial" (só finalized) | "operacional" (+ provisório)
    group_by:        str = "campaign_id",
    campaign_id:     str | None = None,
    finalize_reason: str | None = None,
    segment_id:      str | None = None,
    form_version:    int | None = None,
) -> dict:
    """T11 — relatório de qualidade em DOIS modos, nunca blendados (spec §17.3):
      - oficial: só `result_state='finalized'` (tabela `evaluation_finalized`) — o invariante.
      - operacional: finalized ∪ provisório (evaluation_results ainda não finalizados), rotulado.
    Fatiável por finalize_reason/segment_id/form_version/evaluated_agent_type."""
    since = _ch_fmt(from_dt) if from_dt else _default_from()
    until = _ch_fmt(to_dt, upper=True) if to_dt else _default_to()
    mode  = "operacional" if mode == "operacional" else "oficial"
    if group_by not in _QUALITY_GROUPS:
        group_by = "campaign_id"
    try:
        return await asyncio.to_thread(
            _fetch_quality, client, database, tenant_id, since, until,
            mode, group_by, campaign_id, finalize_reason, segment_id, form_version,
        )
    except Exception as exc:
        logger.warning("query_quality_report failed tenant=%s: %s", tenant_id, exc)
        return {"data": [], "mode": mode, "group_by": group_by,
                "meta": {"from_dt": since, "to_dt": until}, "error": "data_unavailable"}


def _fetch_quality(
    client: Any, db: str, tenant_id: str, since: str, until: str,
    mode: str, group_by: str, campaign_id: str | None,
    finalize_reason: str | None, segment_id: str | None, form_version: int | None,
) -> dict:
    params: dict = {"tenant_id": tenant_id}

    # filtros aplicáveis ao slice finalized
    fin_conds = [
        "tenant_id = {tenant_id:String}",
        f"timestamp >= '{since}'", f"timestamp < '{until}'",
    ]
    if campaign_id:
        fin_conds.append("campaign_id = {campaign_id:String}"); params["campaign_id"] = campaign_id
    if finalize_reason:
        fin_conds.append("finalize_reason = {finalize_reason:String}"); params["finalize_reason"] = finalize_reason
    if segment_id:
        fin_conds.append("segment_id = {segment_id:String}"); params["segment_id"] = segment_id
    if form_version is not None:
        fin_conds.append("form_version = {form_version:Int32}"); params["form_version"] = int(form_version)
    fin_where = " AND ".join(fin_conds)

    fin_src = f"""
        SELECT campaign_id, final_score AS score, finalize_reason, segment_id,
               form_version, evaluated_agent_type, toDate(timestamp) AS date, 0 AS provisional
        FROM {db}.evaluation_finalized FINAL
        WHERE {fin_where}
    """

    if mode == "operacional":
        prov_conds = [
            "tenant_id = {tenant_id:String}",
            f"timestamp >= '{since}'", f"timestamp < '{until}'",
            "eval_status NOT IN ('skipped','error','error_rejected')",
        ]
        if campaign_id:
            prov_conds.append("campaign_id = {campaign_id:String}")
        prov_where = " AND ".join(prov_conds)
        # provisório = evaluation_results cujo instance_id ainda NÃO finalizou
        source = f"""
            {fin_src}
            UNION ALL
            SELECT campaign_id, toFloat32(overall_score) AS score, '' AS finalize_reason,
                   '' AS segment_id, toInt32(0) AS form_version, '' AS evaluated_agent_type,
                   toDate(timestamp) AS date, 1 AS provisional
            FROM {db}.evaluation_results FINAL
            WHERE {prov_where}
              AND instance_id NOT IN (
                  SELECT instance_id FROM {db}.evaluation_finalized
                  WHERE tenant_id = {{tenant_id:String}}
              )
        """
    else:
        source = fin_src

    # date → toString p/ JSON-serializável (Date do ClickHouse não serializa direto → 500)
    group_col = "toString(date)" if group_by == "date" else group_by
    result = client.query(f"""
        SELECT
            {group_col}                              AS group_key,
            count()                                  AS n,
            countIf(provisional = 0)                 AS finalized_n,
            countIf(provisional = 1)                 AS provisional_n,
            round(avg(score), 4)                     AS avg_score,
            countIf(score >= 0.8)                    AS score_high,
            countIf(score >= 0.6 AND score < 0.8)    AS score_mid,
            countIf(score < 0.6)                     AS score_low
        FROM ( {source} )
        GROUP BY {group_col}
        ORDER BY n DESC
    """, parameters=params)
    rows = _rows_to_dicts(result)

    # distribuição por finalize_reason (qualidade do ciclo) — sempre sobre o slice finalized
    reason_res = client.query(f"""
        SELECT finalize_reason AS reason, count() AS n
        FROM {db}.evaluation_finalized FINAL
        WHERE {fin_where}
        GROUP BY finalize_reason ORDER BY n DESC
    """, parameters=params)
    by_reason = {r[0] or "unknown": r[1] for r in reason_res.result_rows}

    total_fin  = sum(int(r.get("finalized_n", 0)) for r in rows)
    total_prov = sum(int(r.get("provisional_n", 0)) for r in rows)
    return {
        "data":             rows,
        "mode":             mode,
        "group_by":         group_by,
        "finalize_reasons": by_reason,
        "meta": {
            "total":           len(rows),
            "total_finalized": total_fin,
            "total_provisional": total_prov,
            "from_dt": since, "to_dt": until,
        },
    }


# ─── /reports/agents/compare — F3 bancada de agentes ─────────────────────────
# Spec: docs/arcos/analytics-agents-workbench.md §11 (contrato) + §10 (regras) +
# §13 (decisões). Multi-entidade × lente; bucket diário por session_at; média =
# ARITMÉTICA dos agentes por bucket ("média dos agentes", N visível; agente sem
# dado no bucket = gap, fora do denominador — nunca zero). Filtros sintéticos
# (agent_type != 'system', role='primary') herdados da atribuição.

_COMPARE_LENSES = {"resolution", "sessions_aht", "availability", "pause_reason",
                   "quality", "nps", "session_nps", "wrapup", "quality_criteria",
                   "escalation_reason", "deploy"}
# deploy (Arc 6 Fase 2 / P2-B): qualidade OFICIAL (evaluation_finalized.final_score)
# por entidade IA (agent_key = flow_id = skill_id), série diária por data da SESSÃO +
# deploy_markers (linhas verticais de deploy, lidas em query-time do agent-registry —
# D1 da spec). Domain `ai`: humanos não têm deploy de skill (gating no front, P2-C).
# 1º corte = série diária + markers (spec §6); agregação por epoch é refinamento.
# session_nps (F10.3a): lê de session_signal (grain=session, metric=nps) via atribuição
# por session_id — voz do cliente no grão sessão, cruzada ao agente (contexto/§8).
# nps (F10.3b): lê de session_signal (grain=segment, metric=nps). wrapup (F5): lê de
# segments (outcome+issue_status) — grão segmento. segments.nps_score dropada (item 5).
# quality_criteria (F8): lê de evaluation_dimension_scores (nota por dimensão) via
# atribuição por session_id; comparável só dentro do mesmo form (guard na UI).
_COMPARE_LENSES_PENDING: set[str] = set()

# Folding da família escalate (§13.2): o alvo humano-vs-IA é recuperável pela
# topologia do segmento seguinte; a bancada compara o CONCEITO escalação.
_ESCALATE_FAMILY_SQL = "('escalated', 'escalated_human', 'escalated_ai', 'transferred')"

# deploy lens (Arc 6 Fase 2): N mínimo p/ significância por bucket (spec §5).
# Constante por ora; alvo = config-api namespace `quality_comparison_min_sample`.
_DEPLOY_MIN_SAMPLE = 30


async def _fetch_deploy_markers(
    tenant_id: str, pool_ids: list[str], since: str, until: str,
) -> list[dict]:
    """deploy_markers da lente ancorada no POOL (Arc 6 Fase 2, spec §11): para cada
    pool selecionado, a timeline de deploys que o atingiram (agent-registry
    `GET /v1/pools/:id/deployments`, D1). Cada marker carrega `pool_id` (a qual curva
    pertence) + `skill_id`+`version_label` (o que foi deployado). Um deploy que atinge
    N pools aparece como marker em cada um deles. Filtra ao window [since, until] (só
    markers desenháveis no eixo). Degradação: registry fora → []."""
    from .config import get_settings
    from .deployments_client import fetch_pool_deployments

    base_url = get_settings().agent_registry_url
    lo, hi = since[:10], until[:10]   # YYYY-MM-DD; markers desenháveis no eixo
    markers: list[dict] = []
    for pool_id in pool_ids:
        deploys = await fetch_pool_deployments(base_url, tenant_id, pool_id)
        for d in deploys:
            at = d.get("deployed_at") or ""
            if at[:10] and lo <= at[:10] <= hi:
                markers.append({
                    "deploy_id":     d.get("deploy_id"),
                    "pool_id":       pool_id,
                    "skill_id":      d.get("skill_id"),
                    "version_label": d.get("version_label"),
                    "deployed_at":   at,
                    "deployed_by":   d.get("deployed_by"),
                })
    markers.sort(key=lambda m: m.get("deployed_at") or "")
    return markers


async def query_agents_compare(
    client:    Any,
    database:  str,
    tenant_id: str,
    from_dt:   str | None = None,
    to_dt:     str | None = None,
    *,
    lens:            str = "resolution",
    mode:            str = "daily",
    pool_id:         str | None = None,
    entities:        list[str] | None = None,
    include_average: bool = True,
    accessible_pools:       list[str] | None = None,
    supervised_agent_types: list[str] | None = None,
    origin:                 "str | list[str]" = "live",
) -> dict:
    """
    Bancada de comparação (F3): devolve séries por entidade (agent_key) + a
    "média dos agentes" de referência, para a lente pedida, numa chamada só.

    entities vazio → só a média (default da bancada: média do(s) pool(s)).
    A média é SEMPRE computada sobre todos os agentes do escopo (pool/ABAC),
    independentemente da seleção — é a referência de comparação.
    """
    since = _ch_fmt(from_dt) if from_dt else _default_from()
    until = _ch_fmt(to_dt, upper=True) if to_dt else _default_to()

    # epoch (R15a) só faz sentido na lente deploy; demais lentes ignoram `mode`.
    epoch = lens == "deploy" and mode == "epoch"
    if epoch:
        include_average = False   # epoch é single-(pool,skill); sem média da frota

    if lens not in _COMPARE_LENSES:
        pending = lens in _COMPARE_LENSES_PENDING
        return {
            "data":  {"average": None, "entities": []},
            "meta":  {"lens": lens, "bucket": "day", "from_dt": since, "to_dt": until},
            "error": "lens_not_available" if pending else "invalid_lens",
            "allowed_lenses": sorted(_COMPARE_LENSES),
            **({"pending_lenses": sorted(_COMPARE_LENSES_PENDING)} if pending else {}),
        }

    try:
        result = await asyncio.to_thread(
            _fetch_agents_compare, client, database, tenant_id, since, until,
            lens, mode, pool_id, entities or [], include_average,
            accessible_pools, supervised_agent_types, origin,
        )
    except Exception as exc:
        logger.warning("query_agents_compare failed tenant=%s lens=%s: %s", tenant_id, lens, exc)
        return {
            "data": {"average": None, "entities": []},
            "meta": {"lens": lens, "bucket": "day", "from_dt": since, "to_dt": until},
            "error": "data_unavailable",
        }

    # ── deploy lens (ancorada no POOL, §11) ─────────────────────────────────────
    # As entidades desta lente SÃO pool_ids (o front seleciona pools). Degradação
    # graciosa: registry fora → markers/ordem vazios, série intacta (nunca 500).
    if epoch:
        # epoch (R15a): eixo X = versões; anexa deployed_at por (skill, versão) e
        # reordena a série por deployed_at (fallback first_seen). Sem markers.
        await _attach_epoch_deploy_order(result, tenant_id)
        # micro-fatia 1b (Opção II): overlay de nota provisória + pendentes por
        # versão, da evaluation-api. Degrada graciosamente (API fora → sem overlay).
        await _attach_epoch_coverage(result, tenant_id, since, until)
        result["meta"]["mode"] = "epoch"
        result["meta"]["min_sample"] = _DEPLOY_MIN_SAMPLE
    elif lens == "deploy":
        pool_ids = [e for e in (entities or [])
                    if e and not e.startswith(_POOL_ENTITY_PREFIX)]
        result["deploy_markers"] = await _fetch_deploy_markers(
            tenant_id, pool_ids, since, until,
        )
        result["meta"]["mode"] = "daily"
        result["meta"]["min_sample"] = _DEPLOY_MIN_SAMPLE

    return result


def _norm_ts(s: str) -> str:
    """Normaliza um timestamp ISO p/ casar set_at × deployed_at (precisão segundos)."""
    return (s or "")[:19].replace(" ", "T")


async def _attach_epoch_deploy_order(result: dict, tenant_id: str) -> dict:
    """Epoch (R15a/Fase C): anexa `deployed_at` + `version_label` (rótulo de display)
    a cada ponto-versão e **reordena por deployed_at** (fallback `first_seen`).

    Dois esquemas de chave convivem (transição):
      - **Fase C** (atual): `deploy_version` carimbado = `set_at` do slot (timestamp) =
        `SkillDeployment.deployed_at`. Casa por timestamp → `deployed_at` = a própria
        versão; `version_label` = `SkillDeployment.version` (rótulo skill.version).
      - **Legado**: `deploy_version` = `skill.version` ("2.0"); casa por
        (`skill_id`, `version_label`) → `deployed_at` do registry; rótulo = a própria versão.
    Degradação: registry fora → ordena por `first_seen`, rótulo = a versão crua."""
    from .config import get_settings
    from .deployments_client import fetch_pool_deployments

    base_url = get_settings().agent_registry_url
    entities = result.get("data", {}).get("entities", [])

    by_label:    dict[tuple[str, str], str] = {}   # legado: (skill, label) -> deployed_at
    by_deployed: dict[str, str] = {}               # Fase C: norm(deployed_at) -> version_label
    for ent in entities:
        pool_id = ent.get("agent_key") or ""
        if not pool_id or pool_id.startswith(_POOL_ENTITY_PREFIX):
            continue
        for d in await fetch_pool_deployments(base_url, tenant_id, pool_id):
            ver = d.get("version_label") or ""
            at  = d.get("deployed_at") or ""
            if at:
                by_deployed.setdefault(_norm_ts(at), ver)
            if ver and at:
                by_label.setdefault((d.get("skill_id") or "", ver), at)

    for ent in entities:
        series = ent.get("series", [])
        for pt in series:
            version = pt.get("version", "") or ""
            # Fase C: a versão é o set_at (timestamp) e É o deployed_at.
            label = by_deployed.get(_norm_ts(version))
            if label is not None:
                pt["deployed_at"]   = version
                pt["version_label"] = label or version
            else:
                # legado: casa por rótulo; o rótulo é a própria versão.
                pt["deployed_at"]   = by_label.get((pt.get("skill_id", ""), version))
                pt["version_label"] = version
        series.sort(key=lambda p: (
            p.get("deployed_at") or p.get("first_seen") or "",
            p.get("version") or ""))
        ent["series"] = series
    return result


async def _attach_epoch_coverage(
    result: dict, tenant_id: str, since: str, until: str,
) -> dict:
    """Epoch overlay (Arc 6 Fase 2, micro-fatia 1b — Opção II): para cada ponto-versão,
    anexa `provisional_avg`/`provisional_n` (nota provisória, só pontuadas) e `pending_n`
    (instâncias amostradas não finalizadas) buscados da evaluation-api por `(pool, versão)`.
    A curva finalizada permanece do ClickHouse (exata); este overlay é o dado que o
    ClickHouse não tem por versão. Degradação: evaluation-api fora → sem overlay (campos
    ausentes; a UI cai p/ só-finalizada)."""
    from .config import get_settings
    from .coverage_client import fetch_deploy_coverage

    base_url = get_settings().evaluation_api_url
    if not base_url:
        return result   # não configurado → epoch só com a curva finalizada
    entities = result.get("data", {}).get("entities", [])
    for ent in entities:
        pool_id = ent.get("agent_key") or ""
        if not pool_id or pool_id.startswith(_POOL_ENTITY_PREFIX):
            continue
        cov = await fetch_deploy_coverage(base_url, tenant_id, pool_id, since, until)
        by_ver = {c.get("deploy_version"): c for c in cov}
        for pt in ent.get("series", []):
            c = by_ver.get(pt.get("version"))
            if not c:
                continue
            pt["provisional_avg"] = c.get("provisional_avg")
            pt["provisional_n"]   = c.get("provisional_n")
            pt["pending_n"]       = c.get("pending_n")
    return result


# Prefixo de pseudo-entidade "média do pool" (F9). entity = "pool:<pool_id>".
_POOL_ENTITY_PREFIX = "pool:"


def _per_agent_for_lens(
    client: Any, db: str, tenant_id: str, since: str, until: str,
    lens: str, pool_id: str | None,
    accessible_pools: list[str] | None, supervised_agent_types: list[str] | None,
    mode: str = "daily",
    origin: "str | list[str]" = "live",
) -> tuple[dict, list[str]]:
    """Computa per_agent + metric_keys para a lente/escopo dados.

    per_agent[agent_key] = {"agent_type", "label", "buckets": {date: point}, "summary"}.
    Fatorado da F3 para ser reusado pela pseudo-entidade de média do pool (F9).

    Substrate isolation (ADR): `origin` (default 'live') é repassado a cada lens helper
    e aplicado aos blocos que leem segments/sessions — a bancada viva exclui import/reeval.
    """
    if lens in ("resolution", "sessions_aht"):
        per_agent = _compare_segments_lens(
            client, db, tenant_id, since, until, lens, pool_id,
            accessible_pools, supervised_agent_types, origin,
        )
        metric_keys = (
            ["resolution_rate", "escalation_rate"] if lens == "resolution"
            else ["sessions", "aht_ms"]
        )
    elif lens in ("availability", "pause_reason"):
        per_agent = _compare_availability_lens(
            client, db, tenant_id, since, until, lens, pool_id,
            accessible_pools, supervised_agent_types, origin,
        )
        metric_keys = (
            ["logged_ms", "available_ms", "paused_ms", "busy_ms",
             "occupancy_pct", "pause_pct"] if lens == "availability" else []
        )
    elif lens == "nps":
        per_agent = _compare_nps_lens(
            client, db, tenant_id, since, until, pool_id,
            accessible_pools, supervised_agent_types, origin,
        )
        metric_keys = ["avg_nps", "nps"]
    elif lens == "session_nps":
        per_agent = _compare_session_nps_lens(
            client, db, tenant_id, since, until, pool_id,
            accessible_pools, supervised_agent_types, origin,
        )
        metric_keys = ["avg_nps", "nps"]
    elif lens == "wrapup":
        per_agent = _compare_wrapup_lens(
            client, db, tenant_id, since, until, pool_id,
            accessible_pools, supervised_agent_types, origin,
        )
        metric_keys = []  # distribuição por disposição vive no summary (como pause_reason)
    elif lens == "quality_criteria":
        per_agent = _compare_quality_criteria_lens(
            client, db, tenant_id, since, until, pool_id,
            accessible_pools, supervised_agent_types, origin,
        )
        metric_keys = []  # nota por dimensão vive no summary.dimensions[] (como wrapup)
    elif lens == "escalation_reason":
        per_agent = _compare_escalation_reason_lens(
            client, db, tenant_id, since, until, pool_id,
            accessible_pools, supervised_agent_types, origin,
        )
        metric_keys = []  # distribuição por motivo vive no summary.reasons[] (como pause_reason)
    elif lens == "deploy":
        if mode == "epoch":
            # epoch (R15a): série por deploy_version (não por dia), via JOIN
            # evaluation_finalized→segments (deploy_version carimbado, R9).
            per_agent = _compare_deploy_epoch_lens(
                client, db, tenant_id, since, until, pool_id,
                accessible_pools, supervised_agent_types, origin,
            )
        else:
            per_agent = _compare_deploy_lens(
                client, db, tenant_id, since, until, pool_id,
                accessible_pools, supervised_agent_types, origin,
            )
        metric_keys = ["avg_score"]  # qualidade Oficial; markers/ordem vêm no envelope (async)
    else:  # quality
        per_agent = _compare_quality_lens(
            client, db, tenant_id, since, until, pool_id,
            accessible_pools, supervised_agent_types, origin,
        )
        metric_keys = ["avg_score"]
    return per_agent, metric_keys


def _mean_series(per_agent: dict, metric_keys: list[str]) -> list[dict]:
    """Média aritmética por bucket sobre os agentes (gap ≠ zero: agente ausente
    no bucket sai do denominador, não conta como 0). Decisão fechada da F3."""
    buckets = sorted({b for a in per_agent.values() for b in a["buckets"]})
    series: list[dict] = []
    for b in buckets:
        present = [a["buckets"][b] for a in per_agent.values() if b in a["buckets"]]
        if not present:
            continue
        point: dict = {"date": b, "n": len(present)}
        for mk in metric_keys:
            vals = [p[mk] for p in present if p.get(mk) is not None]
            point[mk] = round(sum(vals) / len(vals), 4) if vals else None
        series.append(point)
    return series


def _aggregate_pool_summary(per_agent: dict) -> dict:
    """Summary agregado de um pool (F9): escalares numéricos → média aritmética;
    arrays aninhados (reasons/dispositions) → soma por id; `total` → soma.
    Espelha o que cada viz lê (GroupedBars=escalar, Stacked*=array)."""
    summary: dict = {}
    scalar: dict[str, list[float]] = {}
    reasons: dict[str, dict] = {}
    disps: dict[tuple, dict] = {}
    total = 0
    has_total = False
    for a in per_agent.values():
        s = a.get("summary", {})
        for k, v in s.items():
            if k == "total":
                total += int(v or 0); has_total = True
            elif k == "reasons" and isinstance(v, list):
                for r in v:
                    rid = r.get("reason_id", "")
                    agg = reasons.setdefault(rid, {"reason_id": rid,
                        "reason_label": r.get("reason_label", rid), "total_ms": 0, "count": 0})
                    agg["total_ms"] += int(r.get("total_ms", 0) or 0)
                    agg["count"]    += int(r.get("count", 0) or 0)
            elif k == "dispositions" and isinstance(v, list):
                for d in v:
                    key = (d.get("outcome", ""), d.get("issue_status", ""))
                    agg = disps.setdefault(key, {"outcome": key[0], "issue_status": key[1], "count": 0})
                    agg["count"] += int(d.get("count", 0) or 0)
            elif isinstance(v, (int, float)):
                scalar.setdefault(k, []).append(float(v))
    for k, vals in scalar.items():
        summary[k] = round(sum(vals) / len(vals), 4) if vals else None
    if reasons:
        summary["reasons"] = list(reasons.values())
    if disps:
        summary["dispositions"] = list(disps.values())
    if has_total:
        summary["total"] = total
    return summary


def _fetch_agents_compare(
    client: Any, db: str, tenant_id: str,
    since: str, until: str,
    lens: str, mode: str, pool_id: str | None, entities: list[str], include_average: bool,
    accessible_pools: list[str] | None, supervised_agent_types: list[str] | None,
    origin: "str | list[str]" = "live",
) -> dict:
    per_agent, metric_keys = _per_agent_for_lens(
        client, db, tenant_id, since, until, lens, pool_id,
        accessible_pools, supervised_agent_types, mode, origin,
    )

    # ── Média dos agentes (aritmética por bucket; gap ≠ zero) ────────────────
    average = None
    if include_average and per_agent:
        average = {
            "label":  "média dos agentes",
            "n":      len(per_agent),
            "series": _mean_series(per_agent, metric_keys),
        }

    # ── Entidades selecionadas — agentes reais + pseudo-pools `pool:<id>` (F9) ─
    out_entities: list[dict] = []
    pool_cache: dict[str, dict] = {}
    for ak in entities:
        # Pseudo-entidade: média do pool como série agregada selecionável.
        if ak.startswith(_POOL_ENTITY_PREFIX):
            pid = ak[len(_POOL_ENTITY_PREFIX):]
            if pid not in pool_cache:
                pool_cache[pid], _ = _per_agent_for_lens(
                    client, db, tenant_id, since, until, lens, pid,
                    accessible_pools, supervised_agent_types, mode, origin,
                )
            pa = pool_cache[pid]
            out_entities.append({
                "agent_key":  ak,
                "label":      f"média · {pid}",
                "agent_type": "__pool__",
                "pool_id":    pid,
                "n":          len(pa),
                "series":     _mean_series(pa, metric_keys),
                "summary":    _aggregate_pool_summary(pa),
                **({"missing": True} if not pa else {}),
            })
            continue
        a = per_agent.get(ak)
        if a is None:
            out_entities.append({
                "agent_key": ak, "label": ak, "agent_type": None,
                "series": [], "summary": {}, "missing": True,
            })
            continue
        out_entities.append({
            "agent_key":  ak,
            "label":      a["label"],
            "agent_type": a["agent_type"],
            "series":     [a["buckets"][b] for b in sorted(a["buckets"])],
            "summary":    a["summary"],
        })

    return {
        "data": {"average": average, "entities": out_entities},
        "meta": {
            "lens": lens, "bucket": "day",
            "from_dt": since, "to_dt": until,
            "agents_in_scope": len(per_agent),
        },
    }


def _compare_segments_lens(
    client: Any, db: str, tenant_id: str, since: str, until: str,
    lens: str, pool_id: str | None,
    accessible_pools: list[str] | None, supervised_agent_types: list[str] | None,
    origin: "str | list[str]" = "live",
) -> dict:
    """resolution + sessions_aht — fonte: segments (primary, não-sintético)."""
    conditions = [
        "tenant_id = {tenant_id:String}",
        f"started_at >= '{since}'",
        f"started_at <  '{until}'",
        "role = 'primary'",
        "agent_type != 'system'",
    ]
    params: dict = {"tenant_id": tenant_id}
    if pool_id:
        conditions.append("pool_id = {pool_id:String}")
        params["pool_id"] = pool_id
    _apply_pool_scope(conditions, accessible_pools)
    _apply_agent_scope(conditions, supervised_agent_types)
    _apply_origin_scope(conditions, origin)   # substrate isolation (ADR)

    rows = _rows_to_dicts(client.query(f"""
        SELECT
            ak                              AS agent_key,
            any(at)                         AS agent_type,
            any(lbl)                        AS label,
            toString(bucket)                AS bucket,
            count()                         AS sessions,
            countIf(outc = 'resolved')      AS resolved,
            countIf(outc IN {_ESCALATE_FAMILY_SQL}) AS escalated,
            avg(dur)                        AS aht_ms
        FROM (
            SELECT
                if(agent_type = 'human',
                   if(user_id != '', user_id, agent_type_id),
                   if(flow_id != '', flow_id, agent_type_id))      AS ak,
                agent_type                                          AS at,
                if(agent_type = 'human',
                   if(user_login != '', user_login, user_id),
                   if(flow_id != '', flow_id, agent_type_id))       AS lbl,
                toDate(started_at)                                  AS bucket,
                outcome                                             AS outc,
                duration_ms                                         AS dur
            FROM {db}.segments FINAL
            WHERE {" AND ".join(conditions)}
        )
        GROUP BY ak, bucket
        ORDER BY ak, bucket
    """, parameters=params))

    per_agent: dict = {}
    for r in rows:
        a = per_agent.setdefault(r["agent_key"], {
            "agent_type": r["agent_type"], "label": r["label"],
            "buckets": {}, "_tot": {"sessions": 0, "resolved": 0, "escalated": 0, "dur_sum": 0.0},
        })
        sessions = int(r["sessions"] or 0)
        resolved = int(r["resolved"] or 0)
        escalated = int(r["escalated"] or 0)
        aht = float(r["aht_ms"]) if r["aht_ms"] is not None else None
        point: dict = {"date": r["bucket"], "sessions": sessions}
        if lens == "resolution":
            point["resolution_rate"] = round(resolved / sessions, 4) if sessions else None
            point["escalation_rate"] = round(escalated / sessions, 4) if sessions else None
        else:
            point["aht_ms"] = round(aht, 1) if aht is not None else None
        a["buckets"][r["bucket"]] = point
        t = a["_tot"]
        t["sessions"] += sessions
        t["resolved"] += resolved
        t["escalated"] += escalated
        if aht is not None:
            t["dur_sum"] += aht * sessions

    for a in per_agent.values():
        t = a.pop("_tot")
        s = t["sessions"]
        a["summary"] = {
            "sessions":        s,
            "resolution_rate": round(t["resolved"] / s, 4) if s else None,
            "escalation_rate": round(t["escalated"] / s, 4) if s else None,
            "aht_ms":          round(t["dur_sum"] / s, 1) if s else None,
        }
    return per_agent


# ── Customer Voice (Fatia 1) — lente genérica (grain × metric) + overlay SLA ──────
# O session_signal já é uniforme (grain, metric, value_num, scale carimbada). Aqui a
# leitura GENÉRICA: escolhe grão + instrumento, aplica o roll-up do catálogo, e sobrepõe
# o KPI operacional SLA (aderência %) no mesmo eixo diário. `source` por métrica no
# catálogo (survey | operational). Só instrumentos SEM interpretação primeiro (NPS índice,
# avg, SLA % dentro do alvo); top-box/%alvo entram com a escala carimbada / polaridade.
#
# S1: os instrumentos de survey são DERIVADOS de `survey_catalog.SURVEY_INSTRUMENTS`
# (fonte única: escala, direção, bandas, roll-up). Antes esta tabela era uma segunda
# definição e já havia divergido da spec (CES com `higher_is_better: False` contra
# "nota alta = bom"). `sla` continua declarado aqui: é KPI operacional (sessions),
# não instrumento de pesquisa (session_signal).
CV_INSTRUMENTS: dict[str, dict] = {
    metric: {
        "source":           "survey",
        "rollup":           inst["rollup"],
        "rollup_cond":      inst.get("rollup_cond"),
        "label":            inst["label"],
        "higher_is_better": inst["higher_is_better"],
        "grains":           inst["grains"],
    }
    for metric, inst in survey_catalog.SURVEY_INSTRUMENTS.items()
}
CV_INSTRUMENTS["sla"] = {
    "source": "operational", "rollup": "sla_pct", "rollup_cond": None,
    "label": "SLA (aderência)", "higher_is_better": True,
    "grains": ["segment", "session", "journey"],
}
_CV_GRAINS = ("segment", "session", "journey")


def _cv_sla_series(
    client: Any, db: str, tenant_id: str, since: str, until: str,
    pool_id: str | None, accessible_pools: list[str] | None,
) -> list[dict]:
    """Aderência de SLA diária (% de esperas elegíveis dentro do alvo). KPI objetivo,
    sem interpretação: duração da espera ≤ alvo daquela espera. Overlay no mesmo
    eixo do survey.

    ⚠️ **D14 (iii), 2026-08-25 — fonte migrada de `sessions` para `segments`.**
    A unidade passa a ser a PASSAGEM pela fila e o alvo vem do segmento; ver
    `sla_source.py`. Duas escolhas explícitas:

      · **o eixo continua sendo `sessions.opened_at`**, não a data da espera. O
        overlay existe para ficar lado a lado com os sinais de survey, que são
        datados pela sessão — mudar o eixo desalinharia as duas curvas por até
        uma virada de meia-noite. O JOIN paga por isso.
      · **`pool_id` e escopo ABAC passam a ser lidos do SEGMENTO** (onde se
        esperou), não da sessão (pool de ENTRADA, D10). Filtrar por pool na
        sessão devolvia esperas de outro pool.

    ⚠️ **O `coalesce(wait_time_ms, 0)` SAIU, e não por limpeza.** Ele
    transformava "não esperou" em "esperou zero, logo cumpriu" — o mesmo buraco
    que a D14-i fechou no Fila/SLA, onde produziu pools com 100% de aderência e
    ZERO esperas. Aqui só entra quem tem segmento de fila concluído."""
    conds = [
        "w.tenant_id = {tenant_id:String}",
        "w.role = 'queue'",
        "w.duration_ms IS NOT NULL",
        sla_source.segment_sla_epoch_clause("w.started_at"),
        f"s.opened_at >= '{since}'",
        f"s.opened_at <  '{until}'",
    ]
    params: dict = {"tenant_id": tenant_id}
    if pool_id:
        conds.append("w.pool_id = {pool_id:String}"); params["pool_id"] = pool_id
    _apply_pool_scope(conds, accessible_pools, column="w.pool_id")
    rows = _rows_to_dicts(client.query(f"""
        SELECT toString(toDate(s.opened_at)) AS date,
               countIf(coalesce(w.sla_target_ms, 0) > 0)               AS eligible,
               countIf(coalesce(w.sla_target_ms, 0) > 0
                       AND w.duration_ms <= w.sla_target_ms)           AS within_sla
        FROM {db}.segments AS w FINAL
        INNER JOIN (SELECT session_id, opened_at
                    FROM {db}.sessions FINAL
                    WHERE tenant_id = {{tenant_id:String}}) AS s
               ON w.session_id = s.session_id
        WHERE {" AND ".join(conds)}
        GROUP BY toDate(s.opened_at) ORDER BY date
    """, parameters=params))
    out: list[dict] = []
    for r in rows:
        elig = int(r.get("eligible") or 0)
        out.append({"date": r["date"], "n": elig,
                    "value": round(int(r.get("within_sla") or 0) / elig * 100, 1) if elig else None})
    return out


def query_customer_voice(
    client: Any, db: str, tenant_id: str, grain: str, metric: str, since: str, until: str,
    pool_id: str | None = None, accessible_pools: list[str] | None = None,
) -> dict:
    """Lente genérica: série diária do instrumento (roll-up do catálogo) no grão pedido +
    overlay de SLA. grain=journey: cada sinal já é uma journey (1 survey por processo) →
    agrupar por dia basta (sem union-find para a série)."""
    inst = CV_INSTRUMENTS.get(metric)
    if inst is None:
        raise ValueError(f"unknown metric '{metric}'")
    if grain not in _CV_GRAINS:
        raise ValueError(f"unknown grain '{grain}'")

    series: list[dict] = []
    summary: dict = {"value": None, "n": 0}

    if inst["source"] == "survey":
        conds = [
            "tenant_id = {tenant_id:String}",
            f"session_at >= '{since}'",
            f"session_at <  '{until}'",
            "grain = {grain:String}",
            "metric = {metric:String}",
            "value_num IS NOT NULL",
        ]
        params = {"tenant_id": tenant_id, "grain": grain, "metric": metric}
        if pool_id:
            conds.append("pool_id = {pool_id:String}"); params["pool_id"] = pool_id
        _apply_pool_scope(conds, accessible_pools)
        # S1 — roll-up POR INSTRUMENTO (antes era `avg` para tudo que não fosse NPS):
        #   nps_index → %promotores − %detratores
        #   pct       → % das respostas que satisfazem `rollup_cond` do catálogo
        #               (PMF = % "very_disappointed", alvo Sean Ellis ≥40%;
        #                FCR = % resolvido). `avg` sobre escala categórica 1–3 ou
        #               binária 0/1 é numericamente sem sentido — PMF ainda tinha a
        #               direção invertida no número cru (1 = melhor).
        #   avg       → média (CSAT, CES)
        # `rollup_cond` vem do catálogo (constante de código), nunca do request.
        hits_expr = inst.get("rollup_cond") or "1 = 0"
        rows = _rows_to_dicts(client.query(f"""
            SELECT toString(toDate(session_at)) AS date,
                   count()                 AS n,
                   avg(value_num)          AS avg_value,
                   countIf(value_num >= 9) AS promoters,
                   countIf(value_num <= 6) AS detractors,
                   countIf({hits_expr})    AS hits
            FROM {db}.session_signal FINAL
            WHERE {" AND ".join(conds)}
            GROUP BY toDate(session_at) ORDER BY date
        """, parameters=params))
        tot_n = tot_prom = tot_det = tot_hits = 0
        tot_sum = 0.0
        for r in rows:
            n = int(r["n"] or 0)
            avg_v = float(r["avg_value"]) if r["avg_value"] is not None else None
            prom = int(r["promoters"] or 0)
            det = int(r["detractors"] or 0)
            hits = int(r["hits"] or 0)
            if inst["rollup"] == "nps_index":
                val = round((prom - det) / n * 100, 1) if n else None
            elif inst["rollup"] == "pct":
                val = round(hits / n * 100, 1) if n else None
            else:  # avg
                val = round(avg_v, 2) if avg_v is not None else None
            series.append({"date": r["date"], "n": n, "value": val})
            tot_n += n; tot_prom += prom; tot_det += det; tot_hits += hits
            tot_sum += (avg_v or 0) * n
        if tot_n:
            summary["n"] = tot_n
            if inst["rollup"] == "nps_index":
                summary["value"] = round((tot_prom - tot_det) / tot_n * 100, 1)
            elif inst["rollup"] == "pct":
                summary["value"] = round(tot_hits / tot_n * 100, 1)
            else:
                summary["value"] = round(tot_sum / tot_n, 2)
    elif metric == "sla":
        series = _cv_sla_series(client, db, tenant_id, since, until, pool_id, accessible_pools)
        wsum = sum((s["value"] or 0) * s["n"] for s in series)
        n = sum(s["n"] for s in series if s["value"] is not None)
        summary = {"n": n, "value": round(wsum / n, 1) if n else None}

    # Overlay operacional (descritivo) no mesmo eixo — omitido quando a própria métrica é SLA.
    overlay: dict = {}
    if metric != "sla":
        overlay["sla"] = _cv_sla_series(client, db, tenant_id, since, until, pool_id, accessible_pools)

    return {
        "metric": metric, "grain": grain,
        "instrument": {"label": inst["label"], "rollup": inst["rollup"],
                       "source": inst["source"], "higher_is_better": inst["higher_is_better"]},
        "series": series, "overlay": overlay, "summary": summary,
        "meta": {"from_dt": since, "to_dt": until},
    }


def _compare_nps_lens(
    client: Any, db: str, tenant_id: str, since: str, until: str,
    pool_id: str | None,
    accessible_pools: list[str] | None, supervised_agent_types: list[str] | None,
    origin: "str | list[str]" = "live",
) -> dict:
    """
    nps (F10.3b cutover) — fonte: session_signal (grain='segment', metric='nps'),
    gravado via survey_record pelo hook de NPS de segmento. agent_key/segment_id já
    na linha; agent_type/label vêm de segments por segment_id (ledger do segmento).
    Bucketiza por session_at (regra de ouro §7). NPS = (%promotores − %detratores),
    promotor ≥9, detrator ≤6. session_signal é a fonte ÚNICA — segments.nps_score
    foi dropada (item 5); nem escrita nem lida.
    """
    ss_conditions = [
        "tenant_id = {tenant_id:String}",
        f"session_at >= '{since}'",
        f"session_at <  '{until}'",
        "grain = 'segment'",
        "metric = 'nps'",
        "value_num IS NOT NULL",
    ]
    seg_conditions = [
        "tenant_id = {tenant_id:String}",
        "role = 'primary'",
        "agent_type != 'system'",
    ]
    params: dict = {"tenant_id": tenant_id}
    if pool_id:
        seg_conditions.append("pool_id = {pool_id:String}")
        params["pool_id"] = pool_id
    _apply_pool_scope(seg_conditions, accessible_pools)
    _apply_agent_scope(seg_conditions, supervised_agent_types)
    _apply_origin_scope(seg_conditions, origin)   # substrate isolation (ADR): junção só de segmentos live

    rows = _rows_to_dicts(client.query(f"""
        SELECT
            seg.agent_key               AS agent_key,
            any(seg.agent_type)         AS agent_type,
            any(seg.label)              AS label,
            toString(toDate(ss.session_at)) AS bucket,
            count()                     AS n,
            avg(ss.nps)                 AS avg_nps,
            countIf(ss.nps >= 9)        AS promoters,
            countIf(ss.nps <= 6)        AS detractors
        FROM (
            SELECT segment_id, value_num AS nps, session_at
            FROM {db}.session_signal FINAL
            WHERE {" AND ".join(ss_conditions)}
        ) AS ss
        INNER JOIN (
            SELECT
                segment_id,
                if(agent_type = 'human',
                   if(user_id != '', user_id, agent_type_id),
                   if(flow_id != '', flow_id, agent_type_id))  AS agent_key,
                agent_type,
                if(agent_type = 'human',
                   if(user_login != '', user_login, user_id),
                   if(flow_id != '', flow_id, agent_type_id))   AS label
            FROM {db}.segments FINAL
            WHERE {" AND ".join(seg_conditions)}
        ) AS seg ON ss.segment_id = seg.segment_id
        GROUP BY seg.agent_key, toDate(ss.session_at)
        ORDER BY agent_key, bucket
    """, parameters=params))

    per_agent: dict = {}
    for r in rows:
        a = per_agent.setdefault(r["agent_key"], {
            "agent_type": r["agent_type"], "label": r["label"],
            "buckets": {}, "_n": 0, "_sum": 0.0, "_prom": 0, "_det": 0,
        })
        n = int(r["n"] or 0)
        avg_nps = float(r["avg_nps"]) if r["avg_nps"] is not None else None
        prom = int(r["promoters"] or 0)
        det = int(r["detractors"] or 0)
        a["buckets"][r["bucket"]] = {
            "date": r["bucket"], "n": n,
            "avg_nps": round(avg_nps, 2) if avg_nps is not None else None,
            "nps": round((prom - det) / n * 100, 1) if n else None,
        }
        a["_n"] += n; a["_sum"] += (avg_nps or 0) * n; a["_prom"] += prom; a["_det"] += det
    for a in per_agent.values():
        n = a.pop("_n"); s = a.pop("_sum"); prom = a.pop("_prom"); det = a.pop("_det")
        a["summary"] = {
            "n_responses": n,
            "avg_nps":     round(s / n, 2) if n else None,
            "nps":         round((prom - det) / n * 100, 1) if n else None,
        }
    return per_agent


def _compare_session_nps_lens(
    client: Any, db: str, tenant_id: str, since: str, until: str,
    pool_id: str | None,
    accessible_pools: list[str] | None, supervised_agent_types: list[str] | None,
    origin: "str | list[str]" = "live",   # aceito p/ dispatch uniforme; ver nota abaixo
) -> dict:
    """
    session_nps (F10.3a) — voz do cliente no grão SESSION, cruzada ao agente que
    atendeu o contato. Fonte: session_signal (grain='session', metric='nps') ⋈
    atribuição por session_id (último primary não-sintético, F2). O sinal de sessão
    NÃO é atribuível a um agente (agent_key vazio na tabela); aqui o agente vem da
    sessão atendida — é o cruzamento §8 (NPS do agente × NPS da sessão), exibido no
    detalhe. Bucketiza por session_at (regra de ouro §7). N sempre visível.
    """
    ss_conditions = [
        "tenant_id = {tenant_id:String}",
        f"session_at >= '{since}'",
        f"session_at <  '{until}'",
        "grain = 'session'",
        "metric = 'nps'",
    ]
    params: dict = {"tenant_id": tenant_id}
    outer = ["1 = 1"]
    if pool_id:
        outer.append("attr.pool_id = {pool_id:String}")
        params["pool_id"] = pool_id
    if accessible_pools is not None:
        scope: list[str] = []
        if _apply_pool_scope(scope, accessible_pools):
            outer += [s.replace("pool_id", "attr.pool_id") for s in scope]
        else:
            return {}
    # Substrate isolation (ADR): a fonte é session_signal grain=session (sem coluna
    # origin) ⋈ atribuição por segments (SQL compartilhado, que filtra por origem).
    # NPS de sessão só existe p/ tráfego live na prática; passamos origin p/ honrar
    # override e manter a atribuição consistente com as demais lentes.
    attr_sql = _session_agent_attribution_sql(db, origin)

    rows = _rows_to_dicts(client.query(f"""
        SELECT
            attr.agent_key                       AS agent_key,
            any(attr.agent_type)                 AS agent_type,
            any(if(attr.agent_type = 'human',
                   attr.user_login, attr.agent_key)) AS label,
            toString(toDate(ss.session_at))      AS bucket,
            count()                              AS n,
            avg(ss.value_num)                    AS avg_nps,
            countIf(ss.value_num >= 9)           AS promoters,
            countIf(ss.value_num <= 6)           AS detractors
        FROM (
            SELECT session_id, value_num, session_at
            FROM {db}.session_signal FINAL
            WHERE {" AND ".join(ss_conditions)}
        ) AS ss
        JOIN ({attr_sql}) AS attr ON ss.session_id = attr.session_id
        WHERE {" AND ".join(outer)}
        GROUP BY attr.agent_key, toDate(ss.session_at)
        ORDER BY agent_key, bucket
    """, parameters=params))

    per_agent: dict = {}
    for r in rows:
        a = per_agent.setdefault(r["agent_key"], {
            "agent_type": r["agent_type"], "label": r["label"],
            "buckets": {}, "_n": 0, "_sum": 0.0, "_prom": 0, "_det": 0,
        })
        n = int(r["n"] or 0)
        avg_nps = float(r["avg_nps"]) if r["avg_nps"] is not None else None
        prom = int(r["promoters"] or 0)
        det = int(r["detractors"] or 0)
        a["buckets"][r["bucket"]] = {
            "date": r["bucket"], "n": n,
            "avg_nps": round(avg_nps, 2) if avg_nps is not None else None,
            "nps": round((prom - det) / n * 100, 1) if n else None,
        }
        a["_n"] += n; a["_sum"] += (avg_nps or 0) * n; a["_prom"] += prom; a["_det"] += det
    for a in per_agent.values():
        n = a.pop("_n"); s = a.pop("_sum"); prom = a.pop("_prom"); det = a.pop("_det")
        a["summary"] = {
            "n_responses": n,
            "avg_nps":     round(s / n, 2) if n else None,
            "nps":         round((prom - det) / n * 100, 1) if n else None,
        }
    return per_agent


def _compare_wrapup_lens(
    client: Any, db: str, tenant_id: str, since: str, until: str,
    pool_id: str | None,
    accessible_pools: list[str] | None, supervised_agent_types: list[str] | None,
    origin: "str | list[str]" = "live",
) -> dict:
    """
    wrapup — fonte: segments (outcome normalizado + issue_status cru, F1/F5).
    Distribuição de disposições por agente (barras empilhadas, como pause_reason
    — vive em summary.dispositions[]). Só segmentos com issue_status preenchido.
    """
    conditions = [
        "tenant_id = {tenant_id:String}",
        f"started_at >= '{since}'",
        f"started_at <  '{until}'",
        "role = 'primary'",
        "agent_type != 'system'",
        "issue_status != ''",
        "issue_status IS NOT NULL",
    ]
    params: dict = {"tenant_id": tenant_id}
    if pool_id:
        conditions.append("pool_id = {pool_id:String}")
        params["pool_id"] = pool_id
    _apply_pool_scope(conditions, accessible_pools)
    _apply_agent_scope(conditions, supervised_agent_types)
    _apply_origin_scope(conditions, origin)   # substrate isolation (ADR)

    rows = _rows_to_dicts(client.query(f"""
        SELECT
            ak                          AS agent_key,
            any(at)                     AS agent_type,
            any(lbl)                    AS label,
            outc                        AS outcome,
            iss                         AS issue_status,
            count()                     AS cnt
        FROM (
            SELECT
                if(agent_type = 'human',
                   if(user_id != '', user_id, agent_type_id),
                   if(flow_id != '', flow_id, agent_type_id))  AS ak,
                agent_type                                      AS at,
                if(agent_type = 'human',
                   if(user_login != '', user_login, user_id),
                   if(flow_id != '', flow_id, agent_type_id))   AS lbl,
                coalesce(outcome, '')                           AS outc,
                issue_status                                    AS iss
            FROM {db}.segments FINAL
            WHERE {" AND ".join(conditions)}
        )
        GROUP BY ak, outc, iss
        ORDER BY ak, cnt DESC
    """, parameters=params))

    per_agent: dict = {}
    for r in rows:
        a = per_agent.setdefault(r["agent_key"], {
            "agent_type": r["agent_type"], "label": r["label"],
            "buckets": {}, "summary": {"total": 0, "dispositions": []},
        })
        cnt = int(r["cnt"] or 0)
        a["summary"]["dispositions"].append({
            "outcome":      r["outcome"] or "",
            "issue_status": r["issue_status"] or "",
            "count":        cnt,
        })
        a["summary"]["total"] += cnt
    return per_agent


def _compare_escalation_reason_lens(
    client: Any, db: str, tenant_id: str, since: str, until: str,
    pool_id: str | None,
    accessible_pools: list[str] | None, supervised_agent_types: list[str] | None,
    origin: "str | list[str]" = "live",
) -> dict:
    """
    escalation_reason (F7) — fonte: segments.escalation_reason (id normalizado).
    Distribuição por agente dos motivos de escalação (barras empilhadas, como
    pause_reason — vive em summary.reasons[]). Só segmentos da família escalate
    com motivo preenchido. O label legível vem do config escalation_reasons (mapeado
    na UI); aqui reason_label = reason_id como fallback.
    """
    conditions = [
        "tenant_id = {tenant_id:String}",
        f"started_at >= '{since}'",
        f"started_at <  '{until}'",
        "role = 'primary'",
        "agent_type != 'system'",
        f"outcome IN {_ESCALATE_FAMILY_SQL}",
        "escalation_reason != ''",
        "escalation_reason IS NOT NULL",
    ]
    params: dict = {"tenant_id": tenant_id}
    if pool_id:
        conditions.append("pool_id = {pool_id:String}")
        params["pool_id"] = pool_id
    _apply_pool_scope(conditions, accessible_pools)
    _apply_agent_scope(conditions, supervised_agent_types)
    _apply_origin_scope(conditions, origin)   # substrate isolation (ADR)

    rows = _rows_to_dicts(client.query(f"""
        SELECT
            ak                          AS agent_key,
            any(at)                     AS agent_type,
            any(lbl)                    AS label,
            esc                         AS reason_id,
            count()                     AS cnt
        FROM (
            SELECT
                if(agent_type = 'human',
                   if(user_id != '', user_id, agent_type_id),
                   if(flow_id != '', flow_id, agent_type_id))  AS ak,
                agent_type                                      AS at,
                if(agent_type = 'human',
                   if(user_login != '', user_login, user_id),
                   if(flow_id != '', flow_id, agent_type_id))   AS lbl,
                escalation_reason                               AS esc
            FROM {db}.segments FINAL
            WHERE {" AND ".join(conditions)}
        )
        GROUP BY ak, esc
        ORDER BY ak, cnt DESC
    """, parameters=params))

    per_agent: dict = {}
    for r in rows:
        a = per_agent.setdefault(r["agent_key"], {
            "agent_type": r["agent_type"], "label": r["label"],
            "buckets": {}, "summary": {"total": 0, "reasons": []},
        })
        cnt = int(r["cnt"] or 0)
        rid = r["reason_id"] or ""
        a["summary"]["reasons"].append({
            "reason_id":    rid,
            "reason_label": rid,   # UI remapeia pelo config escalation_reasons
            "count":        cnt,
            "total_ms":     0,     # compat com StackedReasonBars (usa count p/ escalação)
        })
        a["summary"]["total"] += cnt
    return per_agent


def _compare_availability_lens(
    client: Any, db: str, tenant_id: str, since: str, until: str,
    lens: str, pool_id: str | None,
    accessible_pools: list[str] | None, supervised_agent_types: list[str] | None,
    origin: "str | list[str]" = "live",
) -> dict:
    """
    availability + pause_reason — fonte: agent_login_intervals + agent_pause_intervals
    (+ busy de segments). Domínio HUMANO (§4 do spec): instance LIKE 'human-%'.
    Substrate isolation: login/pause não têm coluna origin (não são substrato e import
    não os gera); o filtro `origin` aplica-se só ao bloco busy (segments).
    agent_key = user_id. Denominadores fixos (§5): pause% = paused/logged;
    available% implícito; occupancy% = busy/(logged − paused).
    """
    base = ["tenant_id = {tenant_id:String}", "instance_id LIKE 'human-%'"]
    params: dict = {"tenant_id": tenant_id}
    if pool_id:
        base.append("pool_id = {pool_id:String}")
        params["pool_id"] = pool_id
    _apply_pool_scope(base, accessible_pools)
    _apply_agent_scope(base, supervised_agent_types)

    login_rows = _rows_to_dicts(client.query(f"""
        SELECT
            any(user_id)            AS user_id,
            instance_id,
            any(user_login)         AS user_login,
            toString(toDate(logged_in_at)) AS bucket,
            sum(if(duration_ms IS NOT NULL, duration_ms,
                   toUnixTimestamp64Milli(now64(3)) - toUnixTimestamp64Milli(logged_in_at))) AS logged_ms
        FROM {db}.agent_login_intervals FINAL
        WHERE {" AND ".join(base + [f"logged_in_at >= '{since}'", f"logged_in_at < '{until}'"])}
        GROUP BY instance_id, toDate(logged_in_at)
    """, parameters=params))

    pause_rows = _rows_to_dicts(client.query(f"""
        SELECT
            instance_id,
            toString(toDate(paused_at)) AS bucket,
            sum(if(duration_ms IS NOT NULL, duration_ms,
                   toUnixTimestamp64Milli(now64(3)) - toUnixTimestamp64Milli(paused_at))) AS paused_ms
        FROM {db}.agent_pause_intervals FINAL
        WHERE {" AND ".join(base + [f"paused_at >= '{since}'", f"paused_at < '{until}'"])}
        GROUP BY instance_id, toDate(paused_at)
    """, parameters=params))

    reason_rows = _rows_to_dicts(client.query(f"""
        SELECT
            instance_id, reason_id, any(reason_label) AS reason_label,
            count() AS cnt,
            sum(if(duration_ms IS NOT NULL, duration_ms,
                   toUnixTimestamp64Milli(now64(3)) - toUnixTimestamp64Milli(paused_at))) AS total_ms
        FROM {db}.agent_pause_intervals FINAL
        WHERE {" AND ".join(base + [f"paused_at >= '{since}'", f"paused_at < '{until}'"])}
        GROUP BY instance_id, reason_id
    """, parameters=params)) if lens == "pause_reason" else []

    busy_conditions = [
        "tenant_id = {tenant_id:String}",
        f"started_at >= '{since}'", f"started_at <  '{until}'",
        "agent_type = 'human'", "user_id != ''",
        "role IN ('primary', 'specialist')",
    ]
    if pool_id:
        busy_conditions.append("pool_id = {pool_id:String}")
    _apply_pool_scope(busy_conditions, accessible_pools)
    _apply_origin_scope(busy_conditions, origin)   # substrate isolation (ADR): busy só de segments live
    busy_rows = _rows_to_dicts(client.query(f"""
        SELECT
            concat('human-', user_id)      AS instance_id,
            toString(toDate(started_at))   AS bucket,
            sum(duration_ms)               AS busy_ms
        FROM {db}.segments FINAL
        WHERE {" AND ".join(busy_conditions)}
        GROUP BY user_id, toDate(started_at)
    """, parameters=params)) if lens == "availability" else []

    per_agent: dict = {}

    def _agent(instance_id: str, user_login: str = "", user_id: str = "") -> dict:
        ak = user_id or instance_id.removeprefix("human-")
        return per_agent.setdefault(ak, {
            "agent_type": "human",
            "label":      user_login or ak,
            "buckets":    {},
            "summary":    {"logged_ms": 0, "paused_ms": 0, "busy_ms": 0},
        })

    for r in login_rows:
        a = _agent(r["instance_id"], r.get("user_login") or "", r.get("user_id") or "")
        p = a["buckets"].setdefault(r["bucket"], {"date": r["bucket"]})
        p["logged_ms"] = int(r["logged_ms"] or 0)
        a["summary"]["logged_ms"] += int(r["logged_ms"] or 0)
    for r in pause_rows:
        a = _agent(r["instance_id"])
        p = a["buckets"].setdefault(r["bucket"], {"date": r["bucket"]})
        p["paused_ms"] = int(r["paused_ms"] or 0)
        a["summary"]["paused_ms"] += int(r["paused_ms"] or 0)
    for r in busy_rows:
        a = _agent(r["instance_id"])
        p = a["buckets"].setdefault(r["bucket"], {"date": r["bucket"]})
        p["busy_ms"] = int(r["busy_ms"] or 0)
        a["summary"]["busy_ms"] += int(r["busy_ms"] or 0)

    # Derivados por bucket + summary (denominadores fixos §5)
    for a in per_agent.values():
        for p in a["buckets"].values():
            logged = p.get("logged_ms", 0)
            paused = p.get("paused_ms", 0)
            busy   = p.get("busy_ms", 0)
            avail  = max(logged - paused, 0)
            p.setdefault("logged_ms", 0)
            p.setdefault("paused_ms", 0)
            p.setdefault("busy_ms", 0)
            p["available_ms"]  = avail
            p["pause_pct"]     = round(paused / logged, 4) if logged else None
            p["occupancy_pct"] = round(busy / avail, 4) if avail else None
        s = a["summary"]
        logged, paused, busy = s["logged_ms"], s["paused_ms"], s["busy_ms"]
        avail = max(logged - paused, 0)
        s["available_ms"]  = avail
        s["pause_pct"]     = round(paused / logged, 4) if logged else None
        s["occupancy_pct"] = round(busy / avail, 4) if avail else None

    if lens == "pause_reason":
        for r in reason_rows:
            a = _agent(r["instance_id"])
            a["summary"].setdefault("reasons", []).append({
                "reason_id":    r["reason_id"],
                "reason_label": r["reason_label"],
                "count":        int(r["cnt"] or 0),
                "total_ms":     int(r["total_ms"] or 0),
            })

    return per_agent


def _compare_quality_lens(
    client: Any, db: str, tenant_id: str, since: str, until: str,
    pool_id: str | None,
    accessible_pools: list[str] | None, supervised_agent_types: list[str] | None,
    origin: "str | list[str]" = "live",
) -> dict:
    """
    quality — fonte: evaluation_results × atribuição (F2). Bucketiza pela data da
    SESSÃO avaliada (session_started_at — regra de ouro §7), não da avaliação.
    Amostral: N (n_evaluations) sempre presente no ponto e no summary.
    """
    er_conditions = [
        "tenant_id = {tenant_id:String}",
        f"timestamp >= '{since}'",
        f"timestamp <  '{until}'",
    ]
    params: dict = {"tenant_id": tenant_id}
    outer = ["1 = 1"]
    if pool_id:
        outer.append("attr.pool_id = {pool_id:String}")
        params["pool_id"] = pool_id
    if accessible_pools is not None:
        scope: list[str] = []
        if _apply_pool_scope(scope, accessible_pools):
            outer += [s.replace("pool_id", "attr.pool_id") for s in scope]
        else:
            return {}
    attr_sql = _session_agent_attribution_sql(db, origin)   # substrate isolation (ADR)

    rows = _rows_to_dicts(client.query(f"""
        SELECT
            attr.agent_key                       AS agent_key,
            any(attr.agent_type)                 AS agent_type,
            any(if(attr.agent_type = 'human',
                   attr.user_login, attr.agent_key)) AS label,
            toString(toDate(attr.session_started_at)) AS bucket,
            count()                              AS n,
            avg(er.overall_score)                AS avg_score,
            groupUniqArray(er.form_id)           AS form_ids
        FROM (
            SELECT * FROM {db}.evaluation_results FINAL
            WHERE {" AND ".join(er_conditions)}
        ) AS er
        JOIN ({attr_sql}) AS attr ON er.session_id = attr.session_id
        WHERE {" AND ".join(outer)}
        GROUP BY attr.agent_key, toDate(attr.session_started_at)
        ORDER BY agent_key, bucket
    """, parameters=params))

    per_agent: dict = {}
    for r in rows:
        a = per_agent.setdefault(r["agent_key"], {
            "agent_type": r["agent_type"], "label": r["label"],
            "buckets": {}, "_n": 0, "_score_sum": 0.0, "_forms": set(),
        })
        n = int(r["n"] or 0)
        score = float(r["avg_score"]) if r["avg_score"] is not None else None
        a["buckets"][r["bucket"]] = {
            "date": r["bucket"], "avg_score": round(score, 4) if score is not None else None, "n": n,
        }
        a["_n"] += n
        if score is not None:
            a["_score_sum"] += score * n
        # form_ids: união dos formulários que avaliaram este agente no período.
        # Habilita a regra de comparabilidade (item 3 follow-ups A): comparação
        # entre agentes exige mesmo form; entre forms só p/ um único agente.
        for f in (r.get("form_ids") or []):
            if f:
                a["_forms"].add(f)
    for a in per_agent.values():
        n = a.pop("_n")
        ssum = a.pop("_score_sum")
        a["summary"] = {
            "n_evaluations": n,
            "avg_score":     round(ssum / n, 4) if n else None,
            "form_ids":      sorted(a.pop("_forms")),
        }
    return per_agent


def _compare_deploy_lens(
    client: Any, db: str, tenant_id: str, since: str, until: str,
    pool_id: str | None,
    accessible_pools: list[str] | None, supervised_agent_types: list[str] | None,
    origin: "str | list[str]" = "live",
) -> dict:
    """
    deploy (Arc 6 Fase 2 — ancorada no POOL, spec §11) — fonte: evaluation_finalized
    (modo OFICIAL) × atribuição (F2). Espelha a lente `quality`, mas:
      - lê `final_score` de `evaluation_finalized` (não `overall_score` provisório);
      - **a unidade da curva é o `pool_id`** (não o skill/flow_id): um skill pode rodar
        em vários pools, e deploy é pool-centric (`SkillDeployment.pool_ids`). Cada pool
        é uma curva; a versão muda via deploys do pool (markers, no envelope);
      - domain `ai`: só sessões IA (`attr.agent_type != 'human'`) — pools humanos não
        têm deploy de skill.
    Bucketiza pela data da SESSÃO avaliada (session_started_at — regra de ouro §7).
    Os `deploy_markers` (timeline de versão por pool) vêm no envelope, buscados em
    query-time do agent-registry pela camada async. N (n_evaluations) no ponto e no summary.
    """
    fin_conditions = [
        "tenant_id = {tenant_id:String}",
        f"timestamp >= '{since}'",
        f"timestamp <  '{until}'",
    ]
    params: dict = {"tenant_id": tenant_id}
    outer = ["attr.agent_type != 'human'"]  # domain ai — pools humanos não têm deploy
    if pool_id:
        outer.append("attr.pool_id = {pool_id:String}")
        params["pool_id"] = pool_id
    if accessible_pools is not None:
        scope: list[str] = []
        if _apply_pool_scope(scope, accessible_pools):
            outer += [s.replace("pool_id", "attr.pool_id") for s in scope]
        else:
            return {}
    attr_sql = _session_agent_attribution_sql(db, origin)   # substrate isolation (ADR)

    # agent_key = pool_id: a entidade desta lente é o POOL (curva por pool).
    rows = _rows_to_dicts(client.query(f"""
        SELECT
            attr.pool_id                         AS agent_key,
            any(attr.agent_type)                 AS agent_type,
            attr.pool_id                         AS label,
            toString(toDate(attr.session_started_at)) AS bucket,
            count()                              AS n,
            avg(fin.final_score)                 AS avg_score
        FROM (
            SELECT * FROM {db}.evaluation_finalized FINAL
            WHERE {" AND ".join(fin_conditions)}
        ) AS fin
        JOIN ({attr_sql}) AS attr ON fin.session_id = attr.session_id
        WHERE {" AND ".join(outer)}
        GROUP BY attr.pool_id, toDate(attr.session_started_at)
        ORDER BY agent_key, bucket
    """, parameters=params))

    per_agent: dict = {}
    for r in rows:
        a = per_agent.setdefault(r["agent_key"], {
            "agent_type": r["agent_type"], "label": r["label"],
            "buckets": {}, "_n": 0, "_score_sum": 0.0,
        })
        n = int(r["n"] or 0)
        score = float(r["avg_score"]) if r["avg_score"] is not None else None
        a["buckets"][r["bucket"]] = {
            "date": r["bucket"], "avg_score": round(score, 4) if score is not None else None, "n": n,
        }
        a["_n"] += n
        if score is not None:
            a["_score_sum"] += score * n
    for a in per_agent.values():
        n = a.pop("_n")
        ssum = a.pop("_score_sum")
        a["summary"] = {
            "n_evaluations": n,
            "avg_score":     round(ssum / n, 4) if n else None,
        }
    return per_agent


def _compare_deploy_epoch_lens(
    client: Any, db: str, tenant_id: str, since: str, until: str,
    pool_id: str | None,
    accessible_pools: list[str] | None, supervised_agent_types: list[str] | None,
    origin: "str | list[str]" = "live",
) -> dict:
    """
    deploy / **modo epoch** (Arc 6 Fase 2 §IV.8, R15a) — destravado pelo R9.

    Diferente do modo diário: a série de cada pool é bucketizada por
    **`deploy_version`** (não por dia). A versão vem do **carimbo R9 no segmento**
    (`segments.deploy_version`), via JOIN exato `evaluation_finalized.segment_id`
    → `segments.segment_id` — sem inferência por timeline de deploy (que erra no
    overlap de hot-deploy). `skill_id` = `segments.flow_id` (a skill que de fato
    rodou); pool↔skill é N:1, então o pool identifica a skill, mas carimbamos o
    `skill_id` no ponto p/ a chave de eixo (skill|versão) alinhar curvas que
    compartilham skill e desambiguar rótulos de versão entre skills distintas.

    Cada ponto = uma versão: `avg(final_score)` (Oficial), `n` (significância,
    `min_sample` no envelope), `first_seen` (= `min(timestamp)` da avaliação, proxy
    de ordenação quando o agent-registry não tem o deploy). O `deployed_at` real e
    a ordenação final por ele são anexados na camada async (`_attach_epoch_deploy_order`).
    Domain ai: `segments.agent_type != 'human'`; só segmentos com versão carimbada
    (`deploy_version != ''`).
    """
    fin_conditions = [
        "tenant_id = {tenant_id:String}",
        f"timestamp >= '{since}'",
        f"timestamp <  '{until}'",
    ]
    params: dict = {"tenant_id": tenant_id}
    seg_conditions = [
        "tenant_id = {tenant_id:String}",
        "agent_type != 'human'",   # domain ai — pools humanos não têm deploy de skill
        "deploy_version != ''",    # só segmentos com versão carimbada (R9)
    ]
    if pool_id:
        seg_conditions.append("pool_id = {pool_id:String}")
        params["pool_id"] = pool_id
    if accessible_pools is not None:
        if not _apply_pool_scope(seg_conditions, accessible_pools):
            return {}
    _apply_origin_scope(seg_conditions, origin)   # substrate isolation (ADR)

    # agent_key = pool_id (curva por pool); bucket = deploy_version.
    rows = _rows_to_dicts(client.query(f"""
        SELECT
            seg.pool_id          AS agent_key,
            any(seg.agent_type)  AS agent_type,
            seg.pool_id          AS label,
            seg.flow_id          AS skill_id,
            seg.deploy_version   AS version,
            count()              AS n,
            avg(fin.final_score) AS avg_score,
            toString(min(fin.timestamp)) AS first_seen
        FROM (
            SELECT segment_id, final_score, timestamp
            FROM {db}.evaluation_finalized FINAL
            WHERE {" AND ".join(fin_conditions)}
        ) AS fin
        JOIN (
            SELECT segment_id, pool_id, flow_id, deploy_version, agent_type
            FROM {db}.segments FINAL
            WHERE {" AND ".join(seg_conditions)}
        ) AS seg ON fin.segment_id = seg.segment_id
        GROUP BY seg.pool_id, seg.flow_id, seg.deploy_version
        ORDER BY agent_key, first_seen
    """, parameters=params))

    per_agent: dict = {}
    for r in rows:
        a = per_agent.setdefault(r["agent_key"], {
            "agent_type": r["agent_type"], "label": r["label"],
            "buckets": {}, "_n": 0, "_score_sum": 0.0,
        })
        n = int(r["n"] or 0)
        score = float(r["avg_score"]) if r["avg_score"] is not None else None
        skill = r["skill_id"] or ""
        version = r["version"] or ""
        # chave de bucket = skill|versão (desambigua versões entre skills distintas;
        # alinha curvas que compartilham skill). A ordenação final é por deployed_at.
        a["buckets"][f"{skill}|{version}"] = {
            "version":    version,
            "skill_id":   skill,
            "avg_score":  round(score, 4) if score is not None else None,
            "n":          n,
            "first_seen": r["first_seen"] or "",
            "deployed_at": None,   # preenchido na camada async (agent-registry)
        }
        a["_n"] += n
        if score is not None:
            a["_score_sum"] += score * n
    for a in per_agent.values():
        n = a.pop("_n")
        ssum = a.pop("_score_sum")
        a["summary"] = {
            "n_evaluations": n,
            "avg_score":     round(ssum / n, 4) if n else None,
        }
    return per_agent


def _compare_quality_criteria_lens(
    client: Any, db: str, tenant_id: str, since: str, until: str,
    pool_id: str | None,
    accessible_pools: list[str] | None, supervised_agent_types: list[str] | None,
    origin: "str | list[str]" = "live",
) -> dict:
    """
    quality_criteria (F8) — fonte: evaluation_dimension_scores × atribuição (F2).
    Nota média por (agente, dimensão), grão snapshot do período (sem buckets) — vive
    em summary.dimensions[] (como wrapup vive em summary.dispositions[]). A
    comparabilidade é POR FORMULÁRIO: summary.form_id carrega o form do agente; a
    bancada (UI) desabilita/avisa quando as entidades selecionadas misturam forms.
    """
    ds_conditions = [
        "tenant_id = {tenant_id:String}",
        f"timestamp >= '{since}'",
        f"timestamp <  '{until}'",
    ]
    params: dict = {"tenant_id": tenant_id}
    outer = ["1 = 1"]
    if pool_id:
        outer.append("attr.pool_id = {pool_id:String}")
        params["pool_id"] = pool_id
    if accessible_pools is not None:
        scope: list[str] = []
        if _apply_pool_scope(scope, accessible_pools):
            outer += [s.replace("pool_id", "attr.pool_id") for s in scope]
        else:
            return {}
    attr_sql = _session_agent_attribution_sql(db, origin)   # substrate isolation (ADR)

    rows = _rows_to_dicts(client.query(f"""
        SELECT
            attr.agent_key                       AS agent_key,
            any(attr.agent_type)                 AS agent_type,
            any(if(attr.agent_type = 'human',
                   attr.user_login, attr.agent_key)) AS label,
            ds.dimension_id                      AS dimension_id,
            any(ds.dimension_name)               AS dimension_name,
            any(ds.form_id)                      AS form_id,
            count()                              AS n,
            avg(ds.score)                        AS avg_score
        FROM (
            SELECT * FROM {db}.evaluation_dimension_scores FINAL
            WHERE {" AND ".join(ds_conditions)}
        ) AS ds
        JOIN ({attr_sql}) AS attr ON ds.session_id = attr.session_id
        WHERE {" AND ".join(outer)}
        GROUP BY attr.agent_key, ds.dimension_id
        ORDER BY agent_key, dimension_id
    """, parameters=params))

    per_agent: dict = {}
    for r in rows:
        a = per_agent.setdefault(r["agent_key"], {
            "agent_type": r["agent_type"], "label": r["label"],
            "buckets": {}, "summary": {"form_id": r.get("form_id") or "", "dimensions": []},
        })
        score = float(r["avg_score"]) if r["avg_score"] is not None else None
        a["summary"]["dimensions"].append({
            "dimension_id":    r["dimension_id"],
            "dimension_label": r["dimension_name"] or r["dimension_id"],
            "avg_score":       round(score, 4) if score is not None else None,
            "n":               int(r["n"] or 0),
        })
    for a in per_agent.values():
        dims = a["summary"]["dimensions"]
        a["summary"]["n_evaluations"] = max((d["n"] for d in dims), default=0)
    return per_agent


# ─── /reports/agents/cross — F6 cruzamentos (§8) ─────────────────────────────
# As 3 vantagens lado a lado por agente: resolução (segments) × qualidade
# (evaluation_results via atribuição) × NPS (session_signal grain=segment). Devolve as
# métricas cruas por agente; o REALCE de divergência (perception gap, acurácia
# de disposição, estrela) e o quadrante são presentation-layer na UI.

async def query_agents_cross(
    client:    Any,
    database:  str,
    tenant_id: str,
    from_dt:   str | None = None,
    to_dt:     str | None = None,
    *,
    pool_id:                str | None       = None,
    accessible_pools:       list[str] | None = None,
    supervised_agent_types: list[str] | None = None,
    origin:                 "str | list[str]" = "live",
) -> dict:
    since = _ch_fmt(from_dt) if from_dt else _default_from()
    until = _ch_fmt(to_dt, upper=True) if to_dt else _default_to()
    try:
        return await asyncio.to_thread(
            _fetch_agents_cross, client, database, tenant_id, since, until,
            pool_id, accessible_pools, supervised_agent_types, origin,
        )
    except Exception as exc:
        logger.warning("query_agents_cross failed tenant=%s: %s", tenant_id, exc)
        return {"data": [], "meta": {"from_dt": since, "to_dt": until}, "error": "data_unavailable"}


def _fetch_agents_cross(
    client: Any, db: str, tenant_id: str, since: str, until: str,
    pool_id: str | None,
    accessible_pools: list[str] | None, supervised_agent_types: list[str] | None,
    origin: "str | list[str]" = "live",
) -> dict:
    # ── Agregado de segments por agente (resolução, escalação, NPS) ──────────
    seg_conditions = [
        "tenant_id = {tenant_id:String}",
        f"started_at >= '{since}'",
        f"started_at <  '{until}'",
        "role = 'primary'",
        "agent_type != 'system'",
    ]
    params: dict = {"tenant_id": tenant_id}
    if pool_id:
        seg_conditions.append("pool_id = {pool_id:String}")
        params["pool_id"] = pool_id
    _apply_pool_scope(seg_conditions, accessible_pools)
    _apply_agent_scope(seg_conditions, supervised_agent_types)
    _apply_origin_scope(seg_conditions, origin)   # substrate isolation (ADR): seg_rows + NPS join

    seg_rows = _rows_to_dicts(client.query(f"""
        SELECT
            ak                                  AS agent_key,
            any(at)                             AS agent_type,
            any(lbl)                            AS label,
            count()                             AS sessions,
            countIf(outc = 'resolved')          AS resolved,
            countIf(outc IN {_ESCALATE_FAMILY_SQL}) AS escalated
        FROM (
            SELECT
                if(agent_type = 'human',
                   if(user_id != '', user_id, agent_type_id),
                   if(flow_id != '', flow_id, agent_type_id))  AS ak,
                agent_type                                      AS at,
                if(agent_type = 'human',
                   if(user_login != '', user_login, user_id),
                   if(flow_id != '', flow_id, agent_type_id))   AS lbl,
                outcome                                         AS outc
            FROM {db}.segments FINAL
            WHERE {" AND ".join(seg_conditions)}
        )
        GROUP BY ak
    """, parameters=params))

    # ── NPS por agente — fonte: session_signal (item 5 / cutover F10.3b) ──────
    # segments.nps_score NÃO é mais lido (coluna dropada). NPS de segmento vive em
    # session_signal (grain=segment, metric=nps), atribuído ao agente via segment_id
    # (INNER JOIN segments). Bucketização por session_at (regra de ouro §7). Mesclado
    # por agent_key no Python, como o agregado de qualidade.
    ss_conditions = [
        "tenant_id = {tenant_id:String}",
        f"session_at >= '{since}'",
        f"session_at <  '{until}'",
        "grain = 'segment'",
        "metric = 'nps'",
        "value_num IS NOT NULL",
    ]
    nps_rows = _rows_to_dicts(client.query(f"""
        SELECT
            seg.agent_key               AS agent_key,
            count()                     AS nps_n,
            sum(ss.nps)                 AS nps_sum,
            countIf(ss.nps >= 9)        AS promoters,
            countIf(ss.nps <= 6)        AS detractors
        FROM (
            SELECT segment_id, value_num AS nps
            FROM {db}.session_signal FINAL
            WHERE {" AND ".join(ss_conditions)}
        ) AS ss
        INNER JOIN (
            SELECT
                segment_id,
                if(agent_type = 'human',
                   if(user_id != '', user_id, agent_type_id),
                   if(flow_id != '', flow_id, agent_type_id))  AS agent_key
            FROM {db}.segments FINAL
            WHERE {" AND ".join(seg_conditions)}
        ) AS seg ON ss.segment_id = seg.segment_id
        GROUP BY seg.agent_key
    """, parameters=params))
    nps_by_key = {r["agent_key"]: r for r in nps_rows if r.get("agent_key")}

    # ── Agregado de qualidade por agente (via atribuição, por session_at) ────
    attr_sql = _session_agent_attribution_sql(db, origin)   # substrate isolation (ADR)
    eval_outer = ["1 = 1", f"attr.session_started_at >= '{since}'", f"attr.session_started_at < '{until}'"]
    if pool_id:
        eval_outer.append("attr.pool_id = {pool_id:String}")
    if accessible_pools is not None:
        scope: list[str] = []
        if _apply_pool_scope(scope, accessible_pools):
            eval_outer += [s.replace("pool_id", "attr.pool_id") for s in scope]
        # lista vazia (sem pools acessíveis) → eval_agg vazio; o LEFT JOIN no Python ignora
    eval_rows = _rows_to_dicts(client.query(f"""
        SELECT
            attr.agent_key          AS agent_key,
            count()                 AS n_evals,
            avg(er.overall_score)   AS avg_score
        FROM ( SELECT * FROM {db}.evaluation_results FINAL ) AS er
        JOIN ({attr_sql}) AS attr ON er.session_id = attr.session_id
        WHERE {" AND ".join(eval_outer)}
        GROUP BY attr.agent_key
    """, parameters=params))

    eval_by_key = {
        r["agent_key"]: r for r in eval_rows if r.get("agent_key")
    }

    out: list[dict] = []
    for r in seg_rows:
        ak = r["agent_key"]
        sessions = int(r["sessions"] or 0)
        resolved = int(r["resolved"] or 0)
        escalated = int(r["escalated"] or 0)
        npr = nps_by_key.get(ak) or {}
        nps_n = int(npr.get("nps_n") or 0)
        nps_sum = float(npr.get("nps_sum") or 0)
        prom = int(npr.get("promoters") or 0)
        det = int(npr.get("detractors") or 0)
        ev = eval_by_key.get(ak) or {}
        n_evals = int(ev.get("n_evals") or 0)
        avg_score = float(ev["avg_score"]) if ev.get("avg_score") is not None else None
        out.append({
            "agent_key":       ak,
            "agent_type":      r["agent_type"],
            "label":           r["label"],
            "sessions":        sessions,
            "resolution_rate": round(resolved / sessions, 4) if sessions else None,
            "escalation_rate": round(escalated / sessions, 4) if sessions else None,
            "quality_score":   round(avg_score, 4) if avg_score is not None else None,
            "quality_n":       n_evals,
            "nps":             round((prom - det) / nps_n * 100, 1) if nps_n else None,
            "avg_nps":         round(nps_sum / nps_n, 2) if nps_n else None,
            "nps_n":           nps_n,
        })
    out.sort(key=lambda x: (x["label"] or "").lower())
    return {"data": out, "meta": {"from_dt": since, "to_dt": until, "agents": len(out)}}


# ─── /reports/agent-performance/daily (Arc 5 MV — v_agent_performance) ──────

async def query_agent_performance_daily(
    client:    Any,
    database:  str,
    tenant_id: str,
    from_dt:   str | None = None,
    to_dt:     str | None = None,
    *,
    pool_id:                str | None       = None,
    agent_type_id:          str | None       = None,
    accessible_pools:       list[str] | None = None,
    supervised_agent_types: list[str] | None = None,
    origin:                 "str | list[str]" = "live",
) -> dict:
    """
    Returns daily pre-aggregated performance metrics from the mv_agent_performance_daily
    AggregatingMergeTree, exposed via the v_agent_performance readable view.

    One row per (agent_type_id, pool_id, period_date) — no pagination needed since
    the cardinality is bounded by (agent_types × pools × days).

    Metrics per row:
      total_sessions     — total participation windows in that day
      avg_duration_ms    — mean handle time
      resolution_rate    — fraction with outcome = 'resolved'
      escalation_rate    — fraction with outcome = 'escalated'
      transfer_rate      — fraction with outcome = 'transferred'
      human_rate         — fraction of human-agent sessions

    More efficient than querying segments FINAL because the MV is pre-aggregated
    incrementally; ideal for dashboard trend charts and the Arc 7d performance job.
    """
    since_date = _ch_fmt(from_dt)[:10] if from_dt else _default_from()[:10]
    until_date = _ch_fmt(to_dt)[:10]   if to_dt   else _default_to()[:10]

    if accessible_pools is not None and not accessible_pools:
        return {"data": [], "meta": {"total": 0, "from_date": since_date, "to_date": until_date}}
    if supervised_agent_types is not None and not supervised_agent_types:
        return {"data": [], "meta": {"total": 0, "from_date": since_date, "to_date": until_date}}
    try:
        return await asyncio.to_thread(
            _fetch_agent_performance_daily,
            client, database, tenant_id, since_date, until_date,
            pool_id, agent_type_id, accessible_pools, supervised_agent_types,
            origin,
        )
    except Exception as exc:
        logger.warning("query_agent_performance_daily failed tenant=%s: %s", tenant_id, exc)
        return {"data": [], "error": "data_unavailable"}


def _fetch_agent_performance_daily(
    client:          Any,
    db:              str,
    tenant_id:       str,
    since_date:      str,
    until_date:      str,
    pool_id:         str | None,
    agent_type_id:   str | None,
    accessible_pools:       list[str] | None,
    supervised_agent_types: list[str] | None = None,
    origin:                 "str | list[str]" = "live",
) -> dict:
    conditions = [
        "tenant_id = {tenant_id:String}",
        f"toDate(started_at) >= toDate('{since_date}')",
        f"toDate(started_at) <= toDate('{until_date}')",
        "ended_at IS NOT NULL",
    ]
    params: dict = {"tenant_id": tenant_id}

    if pool_id:
        conditions.append("pool_id = {pool_id:String}")
        params["pool_id"] = pool_id
    if agent_type_id:
        conditions.append("agent_type_id = {agent_type_id:String}")
        params["agent_type_id"] = agent_type_id
    _apply_pool_scope(conditions, accessible_pools)
    _apply_agent_scope(conditions, supervised_agent_types)
    _apply_origin_scope(conditions, origin)   # substrate isolation (ADR)

    where = " AND ".join(conditions)

    # C1b-B — daily trend by identity (humans by user_id, AI by flow_id), computed
    # straight from segments so the Human/AI tabs each see their own agents and
    # `agent_type` is available for client-side filtering. The legacy
    # mv_agent_performance_daily / v_agent_performance are keyed by the synthetic
    # agent_type_id and collapse every human into human_agent_{pool}.
    result = client.query(f"""
        SELECT
            agent_key,
            any(agent_type_id)                                                 AS agent_type_id,
            any(agent_type)                                                    AS agent_type,
            anyIf(user_login, user_login != '')                                AS user_login,
            anyIf(flow_id, flow_id != '')                                      AS flow_id,
            pool_id,
            period_date,
            count()                                                            AS total_sessions,
            round(avgOrNull(duration_ms), 0)                                   AS avg_duration_ms,
            round(countIf(outcome = 'resolved')    / greatest(count(), 1), 4)  AS resolution_rate,
            round(countIf(outcome = 'escalated')   / greatest(count(), 1), 4)  AS escalation_rate,
            round(countIf(outcome = 'transferred') / greatest(count(), 1), 4)  AS transfer_rate,
            round(countIf(is_human)                / greatest(count(), 1), 4)  AS human_rate
        FROM (
            SELECT
                agent_type_id,
                agent_type,
                user_login,
                flow_id,
                pool_id,
                duration_ms,
                outcome,
                toDate(started_at) AS period_date,
                (agent_type = 'human') AS is_human,
                if(agent_type = 'human',
                   if(user_id != '', user_id, agent_type_id),
                   if(flow_id != '', flow_id, agent_type_id))                  AS agent_key
            FROM {db}.segments FINAL
            WHERE {where}
        )
        GROUP BY agent_key, pool_id, period_date
        ORDER BY period_date DESC, agent_key, pool_id
    """, parameters=params)

    rows = _rows_to_dicts(result)
    # ClickHouse returns Date columns as Python date objects, which the JSON
    # encoder cannot serialize. Stringify (same pattern as the availability query).
    for row in rows:
        if hasattr(row.get("period_date"), "isoformat"):
            row["period_date"] = row["period_date"].isoformat()
    return {
        "data": rows,
        "meta": {"total": len(rows), "from_date": since_date, "to_date": until_date},
    }


# ─── /reports/sessions/complexity (Arc 5 MV — v_segment_summary) ─────────────

async def query_session_complexity(
    client:    Any,
    database:  str,
    tenant_id: str,
    from_dt:   str | None = None,
    to_dt:     str | None = None,
    *,
    pool_id:          str | None       = None,
    min_handoffs:     int              = 0,
    accessible_pools: list[str] | None = None,
    origin:           "str | list[str]" = "live",
    page:      int = 1,
    page_size: int = 100,
) -> dict:
    """
    Returns session complexity metrics from the mv_segment_summary AggregatingMergeTree,
    exposed via the v_segment_summary readable view joined with the sessions table
    for date-range and pool filtering.

    Ordered by handoff_count DESC so the most complex sessions surface first.

    Metrics per session:
      segment_count      — total participation windows (primary + specialist + supervisor)
      primary_segments   — primary-agent segments
      specialist_segments— specialist segments (conferences)
      human_segments     — human-agent segments
      total_duration_ms  — sum of all segment durations
      handoff_count      — max sequence_index (0 = no handoffs, 1 = one handoff, …)
      escalation_count   — segments with outcome = 'escalated'
      resolved_count     — segments with outcome = 'resolved'

    Use min_handoffs=1 to filter only sessions that had at least one agent transfer.
    """
    since = _ch_fmt(from_dt) if from_dt else _default_from()
    until = _ch_fmt(to_dt, upper=True) if to_dt else _default_to()

    if accessible_pools is not None and not accessible_pools:
        return {"data": [], "meta": _meta(page, page_size, 0, since, until)}
    try:
        return await asyncio.to_thread(
            _fetch_session_complexity,
            client, database, tenant_id, since, until,
            pool_id, min_handoffs, accessible_pools, page, page_size,
            origin,
        )
    except Exception as exc:
        logger.warning("query_session_complexity failed tenant=%s: %s", tenant_id, exc)
        return {"data": [], "error": "data_unavailable"}


def _fetch_session_complexity(
    client:          Any,
    db:              str,
    tenant_id:       str,
    since:           str,
    until:           str,
    pool_id:         str | None,
    min_handoffs:    int,
    accessible_pools: list[str] | None,
    page:            int,
    page_size:       int,
    origin:          "str | list[str]" = "live",
) -> dict:
    offset = (page - 1) * page_size

    # Conditions on the sessions table (for date and pool filtering)
    sess_conditions = [
        "s.tenant_id = {tenant_id:String}",
        f"s.opened_at >= '{since}'",
        f"s.opened_at < '{until}'",
    ]
    params: dict = {"tenant_id": tenant_id}

    if pool_id:
        sess_conditions.append("s.pool_id = {pool_id:String}")
        params["pool_id"] = pool_id
    if accessible_pools:
        pool_list = ", ".join(f"'{p}'" for p in accessible_pools)
        sess_conditions.append(f"s.pool_id IN ({pool_list})")
    _apply_origin_scope(sess_conditions, origin, alias="s.")   # substrate isolation (ADR)

    sess_where = " AND ".join(sess_conditions)

    # Count query
    count_result = client.query(f"""
        SELECT count()
        FROM {db}.v_segment_summary vs
        INNER JOIN (
            SELECT DISTINCT session_id, pool_id
            FROM {db}.sessions FINAL
            WHERE {sess_where}
        ) s ON vs.session_id = s.session_id AND vs.tenant_id = {'{tenant_id:String}'}
        WHERE vs.handoff_count >= {min_handoffs}
    """, parameters=params)
    total = count_result.result_rows[0][0] if count_result.result_rows else 0

    # Data query
    result = client.query(f"""
        SELECT
            vs.session_id,
            s.pool_id,
            vs.segment_count,
            vs.primary_segments,
            vs.specialist_segments,
            vs.human_segments,
            vs.total_duration_ms,
            vs.handoff_count,
            vs.escalation_count,
            vs.resolved_count
        FROM {db}.v_segment_summary vs
        INNER JOIN (
            SELECT DISTINCT session_id, pool_id
            FROM {db}.sessions FINAL
            WHERE {sess_where}
        ) s ON vs.session_id = s.session_id AND vs.tenant_id = {'{tenant_id:String}'}
        WHERE vs.handoff_count >= {min_handoffs}
        ORDER BY vs.handoff_count DESC, vs.session_id
        LIMIT {page_size}
        OFFSET {offset}
    """, parameters=params)

    rows = _rows_to_dicts(result)
    return {"data": rows, "meta": _meta(page, page_size, total, since, until)}


# ─── Arc 8: agent pause availability ──────────────────────────────────────────

async def query_agent_availability(
    client:                 Any,
    database:               str,
    tenant_id:              str,
    from_dt:                str | None = None,
    to_dt:                  str | None = None,
    pool_id:                str | None = None,
    agent_type_id:          str | None = None,
    accessible_pools:       list[str] | None = None,
    supervised_agent_types: list[str] | None = None,
    page:                   int = 1,
    page_size:              int = 100,
) -> dict:
    """
    Aggregate pause intervals per (agent_type_id, pool_id, date).

    Returns:
      data: [{agent_type_id, pool_id, period_date, total_pauses,
              total_pause_ms, reason_breakdown: [{reason_id, reason_label,
              count, total_ms}]}]
      meta: pagination info
    """
    page_size = min(page_size, _MAX_PAGE_SIZE_JSON)
    since = _ch_fmt(from_dt) if from_dt else _default_from()
    until = _ch_fmt(to_dt, upper=True) if to_dt else _default_to()
    try:
        if accessible_pools is not None and len(accessible_pools) == 0:
            return {"data": [], "meta": _meta(page, page_size, 0, since, until)}
        if supervised_agent_types is not None and len(supervised_agent_types) == 0:
            return {"data": [], "meta": _meta(page, page_size, 0, since, until)}
        return await asyncio.to_thread(
            _fetch_agent_availability,
            client, database, tenant_id,
            since, until, pool_id, agent_type_id,
            accessible_pools, supervised_agent_types,
            page, page_size,
        )
    except Exception as exc:
        logger.warning("query_agent_availability failed tenant=%s: %s", tenant_id, exc)
        return {"data": [], "error": "data_unavailable"}


def _fetch_agent_availability(
    client:                 Any,
    db:                     str,
    tenant_id:              str,
    since:                  str,
    until:                  str,
    pool_id:                str | None,
    agent_type_id:          str | None,
    accessible_pools:       list[str] | None,
    supervised_agent_types: list[str] | None,
    page:                   int,
    page_size:              int,
) -> dict:
    offset = (page - 1) * page_size

    # Fase 1b — group by instance_id (per person) instead of agent_type_id, which
    # collapses every human into human_agent_{pool}. Merge logged time
    # (agent_login_intervals) with pauses (agent_pause_intervals) so the tab shows
    # logged / paused / available per identity. user_login comes from the login
    # interval; pauses join on instance_id.
    # Human-only report: availability/pauses/occupancy is a human workforce view.
    # Human instances are always "human-{userId}"; AI instances are "{type}-NNN"
    # (incl. hook agents like nps/wrapup) — exclude them.
    base_conditions = ["tenant_id = {tenant_id:String}", "instance_id LIKE 'human-%'"]
    params: dict = {"tenant_id": tenant_id}
    if pool_id:
        base_conditions.append("pool_id = {pool_id:String}")
        params["pool_id"] = pool_id
    if agent_type_id:
        base_conditions.append("agent_type_id = {agent_type_id:String}")
        params["agent_type_id"] = agent_type_id
    _apply_pool_scope(base_conditions, accessible_pools)
    _apply_agent_scope(base_conditions, supervised_agent_types)

    pause_where = " AND ".join(base_conditions + [f"paused_at >= '{since}'",    f"paused_at <  '{until}'"])
    login_where = " AND ".join(base_conditions + [f"logged_in_at >= '{since}'", f"logged_in_at <  '{until}'"])

    def _dstr(v: Any) -> str:
        return v.isoformat() if hasattr(v, "isoformat") else str(v)

    # ── Logged time per (instance_id, pool_id, date) ──
    # Includes still-open intervals (duration_ms NULL): count elapsed time up to
    # now() so an agent currently logged in shows live logged time (otherwise the
    # tab is empty until logout).
    login_rows = _rows_to_dicts(client.query(f"""
        SELECT
            instance_id,
            pool_id,
            toDate(logged_in_at)        AS period_date,
            any(user_login)             AS user_login,
            any(user_id)                AS user_id,
            any(agent_type_id)          AS agent_type_id,
            sum(if(duration_ms IS NOT NULL, duration_ms,
                   toUnixTimestamp64Milli(now64(3)) - toUnixTimestamp64Milli(logged_in_at))) AS logged_ms,
            count()                     AS total_logins
        FROM {db}.agent_login_intervals FINAL
        WHERE {login_where}
        GROUP BY instance_id, pool_id, period_date
    """, parameters=params))

    # ── Pauses per (instance_id, pool_id, date) ──
    # Includes still-open pauses (duration_ms NULL): count elapsed up to now() so a
    # currently-paused agent shows live (same as the logged-time treatment).
    pause_rows = _rows_to_dicts(client.query(f"""
        SELECT
            instance_id,
            pool_id,
            toDate(paused_at)           AS period_date,
            any(agent_type_id)          AS agent_type_id,
            count()                     AS total_pauses,
            sum(if(duration_ms IS NOT NULL, duration_ms,
                   toUnixTimestamp64Milli(now64(3)) - toUnixTimestamp64Milli(paused_at))) AS total_pause_ms
        FROM {db}.agent_pause_intervals FINAL
        WHERE {pause_where}
        GROUP BY instance_id, pool_id, period_date
    """, parameters=params))

    # ── Reason breakdown per (instance_id, pool_id, date) — for the donut ──
    reason_rows = _rows_to_dicts(client.query(f"""
        SELECT
            instance_id,
            pool_id,
            toDate(paused_at)           AS period_date,
            reason_id,
            reason_label,
            count()                     AS cnt,
            sum(if(duration_ms IS NOT NULL, duration_ms,
                   toUnixTimestamp64Milli(now64(3)) - toUnixTimestamp64Milli(paused_at))) AS total_ms
        FROM {db}.agent_pause_intervals FINAL
        WHERE {pause_where}
        GROUP BY instance_id, pool_id, period_date, reason_id, reason_label
    """, parameters=params))

    # ── Busy time per (instance_id, pool_id, date) — from segments (active
    # handling: primary + specialist roles). Powers occupancy = busy / logged.
    # Joined on instance_id, the same identity key the report groups by. ──
    busy_conditions = [
        "tenant_id = {tenant_id:String}",
        f"started_at >= '{since}'",
        f"started_at <  '{until}'",
        "agent_type = 'human'",
        "user_id != ''",
        "role IN ('primary', 'specialist')",
    ]
    if pool_id:
        busy_conditions.append("pool_id = {pool_id:String}")
    _apply_pool_scope(busy_conditions, accessible_pools)
    # Match the login-interval identity by constructing instance_id from user_id
    # (reliable for humans via C1) rather than relying on segments.instance_id.
    busy_rows = _rows_to_dicts(client.query(f"""
        SELECT
            concat('human-', user_id)   AS instance_id,
            pool_id,
            toDate(started_at)          AS period_date,
            sum(duration_ms)            AS busy_ms
        FROM {db}.segments FINAL
        WHERE {" AND ".join(busy_conditions)}
        GROUP BY user_id, pool_id, period_date
    """, parameters=params))

    merged: dict = {}

    def _slot(inst: str, pool: str, period: str, atype: str) -> dict:
        k = (inst, pool, period)
        if k not in merged:
            merged[k] = {
                "instance_id":    inst,
                "user_login":     "",
                "user_id":        "",
                "agent_type_id":  atype or "",
                "pool_id":        pool,
                "period_date":    period,
                "logged_ms":      0,
                "total_logins":   0,
                "total_pauses":   0,
                "total_pause_ms": 0,
                "busy_ms":        0,
            }
        return merged[k]

    for r in login_rows:
        m = _slot(r["instance_id"], r["pool_id"], _dstr(r["period_date"]), r.get("agent_type_id", ""))
        m["logged_ms"]    = int(r.get("logged_ms") or 0)
        m["total_logins"] = int(r.get("total_logins") or 0)
        if r.get("user_login"):    m["user_login"]    = r["user_login"]
        if r.get("user_id"):       m["user_id"]       = r["user_id"]
        if r.get("agent_type_id"): m["agent_type_id"] = r["agent_type_id"]

    for r in pause_rows:
        m = _slot(r["instance_id"], r["pool_id"], _dstr(r["period_date"]), r.get("agent_type_id", ""))
        m["total_pauses"]   = int(r.get("total_pauses") or 0)
        m["total_pause_ms"] = int(r.get("total_pause_ms") or 0)
        if not m["agent_type_id"] and r.get("agent_type_id"):
            m["agent_type_id"] = r["agent_type_id"]

    for r in busy_rows:
        m = _slot(r["instance_id"], r["pool_id"], _dstr(r["period_date"]), "")
        m["busy_ms"] = int(r.get("busy_ms") or 0)

    breakdown: dict = {}
    for r in reason_rows:
        k = (r["instance_id"], r["pool_id"], _dstr(r["period_date"]))
        breakdown.setdefault(k, []).append({
            "reason_id":    r["reason_id"],
            "reason_label": r["reason_label"],
            "count":        r["cnt"],
            "total_ms":     r["total_ms"],
        })

    rows = []
    for k, m in merged.items():
        m["reason_breakdown"] = breakdown.get(k, [])
        m["available_ms"]     = max((m["logged_ms"] or 0) - (m["total_pause_ms"] or 0), 0)
        rows.append(m)

    # period_date DESC, identity ASC (two-pass stable sort)
    rows.sort(key=lambda x: ((x.get("user_login") or x["agent_type_id"]), x["pool_id"]))
    rows.sort(key=lambda x: x["period_date"], reverse=True)

    total = len(rows)
    paged = rows[offset:offset + page_size]
    return {"data": paged, "meta": _meta(page, page_size, total, since, until)}


# ─── /reports/agent-timeline (timeline swimlanes for one agent) ───────────────

async def query_agent_timeline(
    client:           Any,
    database:         str,
    tenant_id:        str,
    instance_id:      str,
    from_dt:          str | None = None,
    to_dt:            str | None = None,
    accessible_pools: list[str] | None = None,
) -> dict:
    """
    Timeline for a single agent (instance_id) over [from_dt, to_dt]:
      login_intervals — total logged-in bars (agent_login_intervals)
      pause_intervals — pause blocks, with reason (agent_pause_intervals)
      pool_intervals  — per-pool presence bars (agent_pool_intervals)
    All timestamps are ISO strings; open intervals have a null end.
    """
    since = _ch_fmt(from_dt) if from_dt else _default_from()
    until = _ch_fmt(to_dt, upper=True) if to_dt else _default_to()
    if not instance_id:
        return {"data": {}, "error": "instance_id_required"}
    if accessible_pools is not None and len(accessible_pools) == 0:
        return {"data": {"instance_id": instance_id, "login_intervals": [],
                         "pause_intervals": [], "pool_intervals": []},
                "meta": {"from_dt": since, "to_dt": until}}
    try:
        return await asyncio.to_thread(
            _fetch_agent_timeline,
            client, database, tenant_id, instance_id, since, until, accessible_pools,
        )
    except Exception as exc:
        logger.warning("query_agent_timeline failed tenant=%s instance=%s: %s", tenant_id, instance_id, exc)
        return {"data": {}, "error": "data_unavailable"}


def _fetch_agent_timeline(
    client:           Any,
    db:               str,
    tenant_id:        str,
    instance_id:      str,
    since:            str,
    until:            str,
    accessible_pools: list[str] | None,
) -> dict:
    params: dict = {"tenant_id": tenant_id, "instance_id": instance_id}

    def _iso(v: Any) -> str | None:
        if v is None:
            return None
        return v.isoformat() if hasattr(v, "isoformat") else str(v)

    # Login intervals overlapping the window (open intervals have NULL end).
    login_rows = _rows_to_dicts(client.query(f"""
        SELECT interval_id, user_login, user_id, agent_type_id, pool_id,
               logged_in_at, logged_out_at, duration_ms
        FROM {db}.agent_login_intervals FINAL
        WHERE tenant_id = {{tenant_id:String}} AND instance_id = {{instance_id:String}}
          AND logged_in_at < '{until}'
          AND (logged_out_at IS NULL OR logged_out_at >= '{since}')
        ORDER BY logged_in_at
    """, parameters=params))

    # Pause intervals overlapping the window.
    pause_rows = _rows_to_dicts(client.query(f"""
        SELECT interval_id, pool_id, reason_id, reason_label,
               paused_at, resumed_at, duration_ms
        FROM {db}.agent_pause_intervals FINAL
        WHERE tenant_id = {{tenant_id:String}} AND instance_id = {{instance_id:String}}
          AND paused_at < '{until}'
          AND (resumed_at IS NULL OR resumed_at >= '{since}')
        ORDER BY paused_at
    """, parameters=params))

    # Per-pool presence intervals overlapping the window.
    pool_conditions = [
        "tenant_id = {tenant_id:String}",
        "instance_id = {instance_id:String}",
        f"entered_at < '{until}'",
        f"(left_at IS NULL OR left_at >= '{since}')",
    ]
    _apply_pool_scope(pool_conditions, accessible_pools)
    pool_rows = _rows_to_dicts(client.query(f"""
        SELECT interval_id, pool_id, entered_at, left_at, duration_ms
        FROM {db}.agent_pool_intervals FINAL
        WHERE {" AND ".join(pool_conditions)}
        ORDER BY pool_id, entered_at
    """, parameters=params))

    user_login = next((r.get("user_login") for r in login_rows if r.get("user_login")), "")

    for r in login_rows:
        r["logged_in_at"]  = _iso(r.get("logged_in_at"))
        r["logged_out_at"] = _iso(r.get("logged_out_at"))
    for r in pause_rows:
        r["paused_at"]  = _iso(r.get("paused_at"))
        r["resumed_at"] = _iso(r.get("resumed_at"))
    for r in pool_rows:
        r["entered_at"] = _iso(r.get("entered_at"))
        r["left_at"]    = _iso(r.get("left_at"))

    return {
        "data": {
            "instance_id":     instance_id,
            "user_login":      user_login,
            "login_intervals": login_rows,
            "pause_intervals": pause_rows,
            "pool_intervals":  pool_rows,
        },
        "meta": {"from_dt": since, "to_dt": until},
    }


# ─── /reports/pools/volume (Fase 2 — volumetria por pool/canal/endpoint) ──────

async def query_pools_volume(
    client:           Any,
    database:         str,
    tenant_id:        str,
    from_dt:          str | None = None,
    to_dt:            str | None = None,
    *,
    pool_id:          str | None = None,
    channel:          str | None = None,
    bucket:           str | None = None,
    accessible_pools: list[str] | None = None,
    origin:           "str | list[str]" = "live",
) -> dict:
    """
    Volume de contatos por (bucket, pool, canal, endpoint=DNIS) a partir de `sessions`.
    Retorna series (no tempo) + by_channel (donut) + by_endpoint (drill-down) + totals.
    Fase D (queue-attended-model): inclui `rejected` (demanda reprimida) — sessões
    outage na porta, com causa derivada dos segmentos sintéticos `agent_type='system'`
    (Fase B). Desde a fatia 3 (2026-08-02) a única causa PRODUZIDA é `quota` (teto de
    IA); `reservation_full`/`shared_full`/`queue_full` continuam aparecendo em dados
    HISTÓRICOS e por isso não foram removidas da leitura. `totals.contacts` segue
    sendo a demanda total (atendida + reprimida).
    """
    since = _ch_fmt(from_dt) if from_dt else _default_from()
    until = _ch_fmt(to_dt, upper=True) if to_dt else _default_to()
    # NÃO aceita `15min`: `_fetch_pools_volume` não tem essa função de agregação, e o
    # valor cairia no fallback silencioso do seu `bucket_fn`. (Alterado por engano no P3
    # — as três funções deste arquivo têm a MESMA linha de validação, e a edição pegou a
    # primeira. Revertido; a mudança pertence a `query_pools_occupancy`.)
    bkt   = bucket if bucket in ("hour", "day") else "hour"
    empty = {"series": [], "by_channel": [], "by_endpoint": [], "totals": {"contacts": 0, "rejected": 0},
             "rejected": {"series": [], "by_cause": [], "total": 0}}
    if accessible_pools is not None and len(accessible_pools) == 0:
        return {"data": empty, "meta": {"from_dt": since, "to_dt": until, "bucket": bkt}}
    try:
        return await asyncio.to_thread(
            _fetch_pools_volume, client, database, tenant_id, since, until, pool_id, channel, bkt, accessible_pools,
            origin,
        )
    except Exception as exc:
        logger.warning("query_pools_volume failed tenant=%s: %s", tenant_id, exc)
        return {"data": empty, "error": "data_unavailable"}


def _fetch_pools_volume(
    client:           Any,
    db:               str,
    tenant_id:        str,
    since:            str,
    until:            str,
    pool_id:          str | None,
    channel:          str | None,
    bucket:           str,
    accessible_pools: list[str] | None,
    origin:           "str | list[str]" = "live",
) -> dict:
    bucket_fn = "toStartOfHour" if bucket == "hour" else "toStartOfDay"
    conditions = [
        "tenant_id = {tenant_id:String}",
        f"opened_at >= '{since}'",
        f"opened_at <  '{until}'",
    ]
    params: dict = {"tenant_id": tenant_id}
    if pool_id:
        conditions.append("pool_id = {pool_id:String}")
        params["pool_id"] = pool_id
    if channel:
        conditions.append("channel = {channel:String}")
        params["channel"] = channel
    _apply_pool_scope(conditions, accessible_pools)
    _apply_origin_scope(conditions, origin)   # substrate isolation (ADR); rej_where herda
    where = " AND ".join(conditions)

    def _iso(v: Any) -> str:
        return v.isoformat() if hasattr(v, "isoformat") else str(v)

    series = _rows_to_dicts(client.query(f"""
        SELECT {bucket_fn}(opened_at)   AS bucket,
               pool_id, channel,
               coalesce(dnis, '')       AS endpoint,
               count()                  AS contacts
        FROM {db}.sessions FINAL
        WHERE {where}
        GROUP BY bucket, pool_id, channel, endpoint
        ORDER BY bucket
    """, parameters=params))
    for r in series:
        r["bucket"] = _iso(r["bucket"])

    by_channel = _rows_to_dicts(client.query(f"""
        SELECT channel, count() AS contacts
        FROM {db}.sessions FINAL WHERE {where}
        GROUP BY channel ORDER BY contacts DESC
    """, parameters=params))

    by_endpoint = _rows_to_dicts(client.query(f"""
        SELECT channel, coalesce(dnis, '') AS endpoint, count() AS contacts
        FROM {db}.sessions FINAL WHERE {where}
        GROUP BY channel, endpoint ORDER BY contacts DESC
    """, parameters=params))

    total_res = client.query(f"SELECT count() FROM {db}.sessions FINAL WHERE {where}", parameters=params)
    total = total_res.result_rows[0][0] if total_res.result_rows else 0

    # ── Demanda reprimida (Fase D) — rejeição na porta (outcome='outage') ──────
    # Série por bucket/pool/canal vem das sessions (que têm canal); a causa vem
    # dos segmentos sintéticos system/outage (close_reason = outage_cause), que
    # apontam o pool que faltou → "qual ação tomar" (reserva vs capacidade).
    rej_where = f"{where} AND coalesce(outcome, '') = 'outage'"

    rejected_series = _rows_to_dicts(client.query(f"""
        SELECT {bucket_fn}(opened_at)   AS bucket,
               pool_id, channel,
               count()                  AS contacts
        FROM {db}.sessions FINAL
        WHERE {rej_where}
        GROUP BY bucket, pool_id, channel
        ORDER BY bucket
    """, parameters=params))
    for r in rejected_series:
        r["bucket"] = _iso(r["bucket"])

    rejected_by_cause = _rows_to_dicts(client.query(f"""
        SELECT seg.pool_id                        AS pool_id,
               coalesce(seg.close_reason, '')     AS cause,
               count()                            AS contacts
        FROM {db}.segments AS seg FINAL
        INNER JOIN (SELECT session_id FROM {db}.sessions FINAL WHERE {rej_where}) AS ss
            ON seg.session_id = ss.session_id
        WHERE seg.tenant_id = {{tenant_id:String}}
          AND seg.agent_type = 'system' AND seg.outcome = 'outage'
        GROUP BY pool_id, cause
        ORDER BY contacts DESC
    """, parameters=params))

    rej_total_res = client.query(
        f"SELECT count() FROM {db}.sessions FINAL WHERE {rej_where}", parameters=params)
    rejected_total = rej_total_res.result_rows[0][0] if rej_total_res.result_rows else 0

    return {
        "data": {
            "series": series, "by_channel": by_channel, "by_endpoint": by_endpoint,
            "totals": {"contacts": total, "rejected": rejected_total},
            "rejected": {"series": rejected_series, "by_cause": rejected_by_cause, "total": rejected_total},
        },
        "meta": {"from_dt": since, "to_dt": until, "bucket": bucket},
    }


# ─── /reports/pools/queue (Fase 2 — fila + SLA por pool) ─────────────────────

async def query_pools_queue(
    client:           Any,
    database:         str,
    tenant_id:        str,
    from_dt:          str | None = None,
    to_dt:            str | None = None,
    *,
    pool_id:          str | None = None,
    bucket:           str | None = None,
    accessible_pools: list[str] | None = None,
    origin:           "str | list[str]" = "live",
) -> dict:
    """
    Fase D (queue-attended-model) — fila + SLA derivados dos `segments`:
      espera   = duration_ms do segmento `role='queue'` (fila atendida, Fase C);
      abandono = segmento de fila com outcome='abandoned';
      handoff  = segmento de fila não-abandonado seguido de segmento primary real.
    Sessões outage (rejeição na porta) ficam FORA deste relatório — são demanda
    reprimida, reportada no Volume. `queue_events` permanece suplementar
    (tamanho de fila / disponíveis). series (no tempo) + by_pool (agregado).
    """
    since = _ch_fmt(from_dt) if from_dt else _default_from()
    until = _ch_fmt(to_dt, upper=True) if to_dt else _default_to()
    bkt   = bucket if bucket in ("hour", "day") else "hour"
    empty = {"series": [], "by_pool": []}
    if accessible_pools is not None and len(accessible_pools) == 0:
        return {"data": empty, "meta": {"from_dt": since, "to_dt": until, "bucket": bkt}}
    try:
        return await asyncio.to_thread(
            _fetch_pools_queue, client, database, tenant_id, since, until, pool_id, bkt, accessible_pools,
            origin,
        )
    except Exception as exc:
        logger.warning("query_pools_queue failed tenant=%s: %s", tenant_id, exc)
        return {"data": empty, "error": "data_unavailable"}


def _fetch_pools_queue(
    client:           Any,
    db:               str,
    tenant_id:        str,
    since:            str,
    until:            str,
    pool_id:          str | None,
    bucket:           str,
    accessible_pools: list[str] | None,
    origin:           "str | list[str]" = "live",
) -> dict:
    bucket_fn = "toStartOfHour" if bucket == "hour" else "toStartOfDay"

    # Sessions side — keyed on opened_at. Outage (rejeição na porta) excluído:
    # é demanda reprimida (Volume), não comportamento de fila.
    s_conditions = ["tenant_id = {tenant_id:String}", f"opened_at >= '{since}'", f"opened_at <  '{until}'",
                    "coalesce(outcome, '') != 'outage'"]
    params: dict = {"tenant_id": tenant_id}
    if pool_id:
        params["pool_id"] = pool_id
    # Substrate isolation (ADR): filtra sessões por origem; segments entram via JOIN.
    _apply_origin_scope(s_conditions, origin)
    s_where = " AND ".join(s_conditions)

    # F1b — o RECORTE acompanha a ATRIBUIÇÃO. O filtro por pool e o escopo ABAC
    # saíram do `WHERE` de `sessions` (onde falavam de `sessions.pool_id`) e passaram
    # a incidir sobre a MESMA coluna derivada que o relatório agrupa. Filtrar por um
    # fato e agrupar por outro é como o supervisor de um pool sumia de linhas exibidas
    # sob o próprio pool — e, ao contrário do resto de `/reports/*`, aqui a coluna
    # exibida não é `sessions.pool_id`, então não há como o recorte segui-la.
    # `_has_pool` já descarta a linha sem pool nenhum, o que torna o `OR pool_id = ''`
    # dos outros endpoints desnecessário aqui.
    outer_conditions = ["pool_id != ''"]
    if pool_id:
        outer_conditions.append("pool_id = {pool_id:String}")
    _apply_pool_scope(outer_conditions, accessible_pools)
    outer_where = " AND ".join(outer_conditions)

    # Queue-events side (tamanho de fila, disponíveis) — keyed on timestamp.
    q_conditions = ["tenant_id = {tenant_id:String}", f"timestamp >= '{since}'", f"timestamp <  '{until}'"]
    if pool_id:
        q_conditions.append("pool_id = {pool_id:String}")
    _apply_pool_scope(q_conditions, accessible_pools)
    q_where = " AND ".join(q_conditions)

    def _iso(v: Any) -> str:
        return v.isoformat() if hasattr(v, "isoformat") else str(v)

    # Fase D — o ledger de fila são os segments (role='queue', Fase C):
    #   wait_ms  = duration_ms do segmento de fila (NULL p/ fila ao vivo → fora das stats);
    #   q_pool   = pool_id do segmento de fila (= pool-alvo; cobre sessão nunca roteada);
    #   answered = existe segmento primary real (agent_type != 'system').
    # Segmentos começam no open da sessão → filtro started_at >= since é seguro.
    # ⚠️ PRECEDÊNCIA INVERTIDA em 2026-08-14 (F1b). Era
    #     if(ss.pool_id != '', ss.pool_id, segs.q_pool)
    # — a sessão primeiro, o segmento de fila como tapa-buraco. Estava errado, e não
    # por causa do F1b: este relatório mede ONDE ESPEROU, e `sessions.pool_id` nunca
    # foi esse fato (era o que escreveu por último; agora é o pool de ENTRADA). O
    # segmento `role='queue'` É a fila — ele nasce quando o contato entra nela e traz
    # o pool-alvo.
    #
    # Medido antes de inverter (`probe_entry_pool_base.sh`, 2026-08-14): das 15
    # sessões com segmento de fila, **6** tinham `sessions.pool_id` ≠ pool da fila
    # (`aprovacao_credito`≠`limite_processo` 5 · `formfill_demo`≠`formfill_demo_ia` 1)
    # — ou seja, 6 esperas atribuídas ao pool errado, incluindo o SLA e a taxa de
    # abandono. Neste ambiente o carimbo de entrada coincide com o pool da fila, o
    # que faria o F1b "consertar" o número por acidente; coincidência não é fonte.
    #
    # `ss.pool_id` fica como fallback para o caso que NÃO tem segmento de fila e
    # ainda assim precisa aparecer: 5 sessões do tenant têm pool e ZERO segmentos
    # (abandono antes de qualquer agente entrar) — exatamente o que um relatório de
    # fila não pode perder de vista.
    # ── D14-i (2026-08-24): UMA LINHA POR ESPERA, não por sessão ─────────────────
    #
    # Era `_per_session`, e colapsava a sessão ANTES de qualquer leitura de SLA:
    # `anyIf(pool_id, role='queue')` · `anyIf(outcome, …)` · `maxIf(duration_ms, …)`.
    # Com UMA espera por sessão isso é determinístico. Com duas, `anyIf` não dobra —
    # **SORTEIA**, e o sorteado alimenta `abandoned`/`abandon_rate`/`handoff`; o
    # `maxIf` **descarta** a espera menor.
    #
    # Medido em 2026-08-24, tenant demo: **71 segmentos de espera em 59 sessões** ⇒
    # 12 esperas (17% do ledger) invisíveis no relatório. Uma sessão tem CINCO e só
    # uma sobrevive. O caso de motivação não é hipotético — contato
    # `27651d1b-…` esperou em `retencao_humano` e depois em `especialista_onboarding`.
    #
    # ⚠️ **O conserto NÃO é `sum()`.** Somar esperas contra alvos diferentes é
    # exatamente o que a D14 recusa: dá número sem uso prático. O conserto é não
    # colapsar — cada passagem pela fila é uma linha, julgada contra o alvo do pool
    # onde ela aconteceu.
    #
    # ⚠️ O GRÃO DA SAÍDA NÃO MUDOU. `by_pool` e `series` continuam devolvendo uma
    # linha por `(pool[, bucket])`; o colapso vivia na subquery intermediária. O que
    # muda é que uma sessão que esperou em dois pools passa a contar nos DOIS, em vez
    # de num sorteado.
    #
    # ── Duas unidades na mesma linha, e elas são nomeadas ────────────────────────
    # `contacts`/`queued` contam **sessões distintas** (decisão do dono, 2026-08-24:
    # não mudar o significado de coluna que o operador já lê). `waits`, `abandoned`,
    # `handoff`, `avg_wait_ms`, `p95_wait_ms`, `within_sla` e `sla_eligible` contam
    # **passagens**. `abandon_rate` é passagem/passagem — misturar as unidades no
    # numerador e denominador seria a falácia de aditividade de novo.
    #
    # ── Sessões que NÃO esperaram continuam no relatório (decisão do dono) ───────
    # São o denominador de "quanto % esperou" e incluem o abandono ANTES de qualquer
    # agente (5 sessões medidas com pool e ZERO segmentos de fila). O `LEFT JOIN`
    # entrega a linha delas com `q_count=0` e `wait_ms` **NULL** — `duration_ms` é
    # Nullable, então a ausência sobrevive ao join e o `avg()` a ignora, em vez de
    # somar um zero que não é espera.
    #
    # ⚠️ **Herdado, NÃO decidido nesta fatia:** `within_sla` conta a sessão
    # não-enfileirada como dentro do alvo (`coalesce(wait_ms,0) <= sla_target_ms`),
    # o que é defensável ("atendido na hora está dentro") mas mistura não-esperas com
    # esperas no mesmo percentual. Preservado como está para não trocar duas coisas
    # de uma vez; registrado no TODO.
    #
    # A precedência `q_pool → ss.pool_id` continua: o relatório mede ONDE ESPEROU, e
    # `sessions.pool_id` é o pool de ENTRADA (D10), que não é esse fato.
    # ── D14-iii (2026-08-25): o ALVO passa a vir do SEGMENTO ────────────────────
    #
    # Era `ss.sla_target_ms` — o alvo da SESSÃO, copiado igual para todas as
    # esperas dela. A D14-i parou de colapsar as passagens, mas as duas linhas
    # que ela passou a produzir continuavam sendo julgadas contra o MESMO alvo:
    # o defeito que a D14 nomeia sobrevivia à correção que o expôs.
    #
    # `w_sla_target` é `Nullable(Int64)` e a ausência é significativa — ver
    # `sla_source.py`. Duas ausências com a mesma cara, separadas pela época:
    # espera anterior a ela não tinha produtor (a (ii) é forward-only e não há
    # migração possível); espera POSTERIOR sem alvo é o `pool_config` expirado.
    # `sla_unstamped` conta a segunda, abaixo.
    #
    # ⚠️ A linha SEM espera (LEFT JOIN, `q_count=0`) vem com `sla_target_ms`
    # NULL — e isso é correto, não regressão: sessão que não esperou não tem
    # alvo de espera a cumprir. Ela já estava fora do `_sla_eligible` pelo
    # `_queued`, e continua contando em `contacts` como denominador de "quanto %
    # esperou".
    _per_wait = f"""
        SELECT if(w.w_pool != '', w.w_pool, ss.pool_id) AS pool_id,
               ss.session_id                            AS session_id,
               ss.opened_at                             AS opened_at,
               w.w_sla_target                           AS sla_target_ms,
               w.w_started_at                           AS wait_started_at,
               w.w_present                              AS q_count,
               w.w_outcome                              AS q_outcome,
               w.w_wait_ms                              AS wait_ms,
               ss.primary_count                         AS primary_count
        FROM (
            SELECT s.session_id                   AS session_id,
                   s.pool_id                      AS pool_id,
                   s.opened_at                    AS opened_at,
                   coalesce(p.primary_count, 0)   AS primary_count
            FROM (SELECT session_id, pool_id, opened_at
                  FROM {db}.sessions FINAL WHERE {s_where}) s
            LEFT JOIN (
                SELECT session_id,
                       countIf(role = 'primary' AND agent_type != 'system') AS primary_count
                FROM {db}.segments FINAL
                WHERE tenant_id = {{tenant_id:String}} AND started_at >= '{since}'
                GROUP BY session_id
            ) p ON s.session_id = p.session_id
        ) ss
        LEFT JOIN (
            SELECT session_id,
                   pool_id       AS w_pool,
                   outcome       AS w_outcome,
                   duration_ms   AS w_wait_ms,
                   sla_target_ms AS w_sla_target,
                   started_at    AS w_started_at,
                   1             AS w_present
            FROM {db}.segments FINAL
            WHERE tenant_id = {{tenant_id:String}} AND started_at >= '{since}'
              AND role = 'queue'
        ) w ON ss.session_id = w.session_id
    """
    _queued    = "q_count > 0"
    _abandoned = "coalesce(q_outcome, '') = 'abandoned'"
    _handoff   = f"({_queued} AND coalesce(q_outcome, '') != 'abandoned' AND primary_count > 0)"

    # Sessões sem pool resolvido E sem segmento de fila (nunca roteadas nem
    # enfileiradas — ex. webchat que conecta e não engaja) ficam FORA do
    # relatório de fila: não têm comportamento de fila a reportar (a linha
    # "—" que apareciam criava ruído). O volume delas segue no Volume report.
    # (F1b: virou a 1ª cláusula de `outer_conditions`, junto com filtro e ABAC.)

    s_series = _rows_to_dicts(client.query(f"""
        SELECT {bucket_fn}(opened_at)                              AS bucket,
               pool_id,
               round(avg(wait_ms), 0)                              AS avg_wait_ms,
               uniqExact(session_id)                               AS contacts,
               uniqExactIf(session_id, {_queued})                  AS queued,
               countIf({_queued})                                  AS waits,
               countIf({_abandoned})                               AS abandoned
        FROM ({_per_wait})
        WHERE {outer_where}
        GROUP BY bucket, pool_id
    """, parameters=params))

    # F5 — `avg(available_agents)` REMOVIDO desta série (§3.1 do desenho de capacidade
    # compartilhada). A coluna em `queue_events` continua existindo (Nullable, dados
    # históricos), mas não é mais lida: o produtor parou de escrevê-la, e o valor
    # herdado é irrecuperável — não havia o que corrigir, só o que redefinir, e
    # redefinir não backfilla. Três defeitos empilhados: modelo de PERTENCIMENTO
    # (`SCARD`), valor ambíguo (o `1` podia ser filtro de canal, pool pull, ou defeito)
    # e 77% de nulos que o `avg()` ignorava em silêncio enquanto o leitor os convertia
    # para 0 — "nenhum agente disponível" e "não medimos" com a mesma cara.
    q_series = _rows_to_dicts(client.query(f"""
        SELECT {bucket_fn}(timestamp)                              AS bucket,
               pool_id,
               max(queue_position)                                 AS max_queue_len
        FROM {db}.queue_events FINAL WHERE {q_where}
        GROUP BY bucket, pool_id
    """, parameters=params))

    merged: dict = {}
    for r in s_series:
        k = (r["pool_id"], _iso(r["bucket"]))
        merged[k] = {"bucket": _iso(r["bucket"]), "pool_id": r["pool_id"],
                     "avg_wait_ms": int(r.get("avg_wait_ms") or 0), "contacts": int(r.get("contacts") or 0),
                     "queued": int(r.get("queued") or 0), "waits": int(r.get("waits") or 0),
                     "abandoned": int(r.get("abandoned") or 0),
                     "max_queue_len": 0}
    for r in q_series:
        k = (r["pool_id"], _iso(r["bucket"]))
        m = merged.setdefault(k, {"bucket": _iso(r["bucket"]), "pool_id": r["pool_id"],
                                  "avg_wait_ms": 0, "contacts": 0, "queued": 0, "waits": 0,
                                  "abandoned": 0,
                                  "max_queue_len": 0})
        m["max_queue_len"]    = int(r.get("max_queue_len") or 0)
    series = sorted(merged.values(), key=lambda x: (x["bucket"], x["pool_id"]))

    # ── Elegibilidade a SLA (D14-i, 2026-08-24) — TRÊS exclusões, uma regra ──────
    #
    # Regra: **só uma espera CONCLUÍDA e com alvo é julgável.** Antes, o predicado
    # era `sla_target_ms > 0 AND coalesce(wait_ms, 0) <= sla_target_ms` sobre TODA
    # linha, e o `coalesce` era o buraco — ele transformava três ausências
    # diferentes em "esperou zero, logo cumpriu":
    #
    #   1. **contato que nunca enfileirou.** Era descrito como "dentro do SLA por
    #      construção", e o número que derrubou a justificativa foi medido nesta
    #      fatia: `limite_entrega` com 37 contatos, ZERO esperas e **aderência
    #      100%**. Idem `demo_ia`, `formfill_demo`, `aprovacao_deploy`. Verde que
    #      não pode ficar vermelho — a definição de indicador inútil.
    #   2. **espera em curso** (`duration_ms IS NULL` — os 5 segmentos abertos do
    #      `retencao_humano`). Julgar o que não terminou é a mesma mentira, e o
    #      `coalesce` a fabricava como cumprimento.
    #   3. **espera ABANDONADA.** Medido: `especialista_onboarding`, 2 esperas, as
    #      DUAS abandonadas (~83 s contra alvo de 10 min), aderência **100%**. O
    #      cliente que desistiu contava como atendido no prazo — quanto mais cedo
    #      desistisse, melhor o indicador ficava. Inverte o sentido da métrica.
    #
    # ⚠️ **Os números de conformidade MUDAM, e muito.** Declarado antes de medir:
    # `retencao_humano` sai de 0,913 (com 20 de 48 esperas abandonadas — 91% nunca
    # descreveu aquele pool) e os pools sem fila passam a devolver `null`, que a UI
    # já renderiza como ausente. Aderência ausente ≠ aderência zero.
    # ── D14-iii: o alvo é do segmento, e a ÉPOCA separa duas ausências ──────────
    #
    # `coalesce(sla_target_ms, 0) > 0` e não `sla_target_ms > 0`: a coluna virou
    # Nullable, e sobre Nullable um predicado com NULL devolve NULL — o `countIf`
    # PULA a linha em vez de contá-la como falsa. O denominador se moveria sem
    # nada ficar vermelho.
    #
    # `_stamped_era` NÃO é o que exclui a linha antiga (o teste de alvo já a
    # excluiria sozinho). Ele existe para que `sla_unstamped` conte só o que é
    # DEFEITO: espera fechada depois do deploy da (ii) e mesmo assim sem alvo,
    # que é o `{t}:pool_config:{p}` expirado antes do fechamento. Sem a época,
    # esse buraco se esconde dentro do histórico pré-produtor e fica invisível
    # para sempre.
    _stamped_era   = sla_source.segment_sla_epoch_clause("wait_started_at")
    _sla_eligible  = (f"({_queued} AND wait_ms IS NOT NULL"
                      f" AND coalesce(sla_target_ms, 0) > 0 AND {_stamped_era})")
    _sla_unstamped = (f"({_queued} AND wait_ms IS NOT NULL"
                      f" AND coalesce(sla_target_ms, 0) = 0 AND {_stamped_era})")
    by_pool = _rows_to_dicts(client.query(f"""
        SELECT pool_id,
               uniqExact(session_id)                               AS contacts,
               uniqExactIf(session_id, {_queued})                  AS queued,
               countIf({_queued})                                  AS waits,
               countIf({_abandoned})                               AS abandoned,
               countIf({_handoff})                                 AS handoff,
               round(countIf({_abandoned}) / greatest(countIf({_queued}), 1), 4) AS abandon_rate,
               round(avg(wait_ms), 0)                              AS avg_wait_ms,
               round(quantile(0.95)(wait_ms), 0)                   AS p95_wait_ms,
               maxIf(sla_target_ms, {_sla_eligible})               AS sla_target_max,
               countIf({_sla_eligible} AND NOT ({_abandoned})
                                       AND wait_ms <= sla_target_ms) AS within_sla,
               countIf({_sla_eligible})                            AS sla_eligible,
               countIf({_sla_unstamped})                           AS sla_unstamped
        FROM ({_per_wait})
        WHERE {outer_where}
        GROUP BY pool_id ORDER BY contacts DESC
    """, parameters=params))
    for r in by_pool:
        # `maxIf` sem nenhuma linha elegível devolve o DEFAULT do tipo, não NULL
        # — e para `Nullable(Int64)` o default é NULL, mas o driver pode
        # entregar 0 quando a coluna do resultado não é nulável. Normalizar aqui
        # é o que impede "alvo ausente" de virar "alvo zero", que os quatro
        # sites de roteamento leem como prioridade absoluta.
        target = r.pop("sla_target_max", None)
        r["sla_target_ms"] = int(target) if target else None
        elig = int(r.get("sla_eligible") or 0)
        r["sla_unstamped"] = int(r.get("sla_unstamped") or 0)
        r["sla_attainment"] = round(int(r.get("within_sla") or 0) / elig, 4) if elig else None

    return {"data": {"series": series, "by_pool": by_pool},
            "meta": {"from_dt": since, "to_dt": until, "bucket": bucket}}


# ─── /reports/pools/occupancy (Fase 2 — concorrência vs capacidade) ───────────

async def query_pools_occupancy(
    client:           Any,
    database:         str,
    tenant_id:        str,
    from_dt:          str | None = None,
    to_dt:            str | None = None,
    *,
    pool_id:          str | None = None,
    bucket:           str | None = None,
    accessible_pools: list[str] | None = None,
) -> dict:
    """
    Pico de concorrência vs capacidade provisionada, de `pool_occupancy_peaks`
    (re-agrega o pico por minuto para hour/day via max). series + by_pool + total.
    """
    since = _ch_fmt(from_dt) if from_dt else _default_from()
    until = _ch_fmt(to_dt, upper=True) if to_dt else _default_to()
    # P3 — `15min` só AQUI: o pico por minuto já está gravado, então agregar mais fino
    # é leitura retroativa. Os outros endpoints deste arquivo agregam grandezas
    # somáveis, onde um bucket menor não responde a mesma pergunta.
    bkt   = bucket if bucket in ("15min", "hour", "day") else "hour"
    empty = {"series": [], "by_pool": [], "total": None, "total_series": [],
             "admission": {"ai_series": [], "buffer_series": []}}
    if accessible_pools is not None and len(accessible_pools) == 0:
        return {"data": empty, "meta": {"from_dt": since, "to_dt": until, "bucket": bkt}}
    try:
        return await asyncio.to_thread(
            _fetch_pools_occupancy, client, database, tenant_id, since, until, pool_id, bkt, accessible_pools,
        )
    except Exception as exc:
        logger.warning("query_pools_occupancy failed tenant=%s: %s", tenant_id, exc)
        return {"data": empty, "error": "data_unavailable"}


def _fetch_pools_occupancy(
    client:           Any,
    db:               str,
    tenant_id:        str,
    since:            str,
    until:            str,
    pool_id:          str | None,
    bucket:           str,
    accessible_pools: list[str] | None,
) -> dict:
    # P3 — `15min` entra como leitura pura: o grão gravado é de 1 minuto, então
    # qualquer agregação maior é retroativa e não exige escritor novo. `max` re-agrega
    # picos corretamente (máximo de máximos É o máximo) — o que NUNCA seria válido é
    # somar buckets, pela mesma razão que somar pools não é.
    bucket_fn = {
        "15min": "toStartOfInterval(minute, INTERVAL 15 MINUTE)",
        "hour":  "toStartOfHour(minute)",
        "day":   "toStartOfDay(minute)",
    }.get(bucket, "toStartOfHour(minute)")
    # Exclusão por PREFIXO, não por lista. A lista explícita
    # (`'__total__','__reserved__','__shared__','__buffer__'`) deixou as linhas
    # `__capacity_{kind}__` da F4c entrarem em `series`/`by_pool` como se fossem POOLS —
    # a Analytics passaria a exibir "pool __capacity_human__". Defeito introduzido em
    # 2026-08-02 junto com aquelas linhas e encontrado no P3. Prefixo cobre também o
    # próximo marcador que alguém adicionar sem lembrar deste `WHERE`.
    conditions = ["tenant_id = {tenant_id:String}", f"minute >= '{since}'", f"minute <  '{until}'",
                  "NOT startsWith(pool_id, '__')"]
    params: dict = {"tenant_id": tenant_id}
    if pool_id:
        conditions.append("pool_id = {pool_id:String}")
        params["pool_id"] = pool_id
    _apply_pool_scope(conditions, accessible_pools)
    where = " AND ".join(conditions)

    def _iso(v: Any) -> str:
        return v.isoformat() if hasattr(v, "isoformat") else str(v)

    series = _rows_to_dicts(client.query(f"""
        SELECT {bucket_fn}                AS bucket,
               pool_id,
               max(peak_concurrency)      AS peak_concurrency,
               max(provisioned_capacity)  AS capacity,
               max(admitted_peak)         AS admitted
        FROM {db}.pool_occupancy_peaks FINAL
        WHERE {where}
        GROUP BY bucket, pool_id ORDER BY bucket
    """, parameters=params))
    for r in series:
        r["bucket"] = _iso(r["bucket"])

    by_pool = _rows_to_dicts(client.query(f"""
        SELECT pool_id,
               max(peak_concurrency)      AS peak_concurrency,
               max(provisioned_capacity)  AS capacity
        FROM {db}.pool_occupancy_peaks FINAL
        WHERE {where}
        GROUP BY pool_id ORDER BY peak_concurrency DESC
    """, parameters=params))
    for r in by_pool:
        cap  = int(r.get("capacity") or 0)
        peak = int(r.get("peak_concurrency") or 0)
        r["headroom"]    = max(cap - peak, 0)
        r["utilization"] = round(peak / cap, 4) if cap else None

    # Total (tenant-wide) — only for unscoped callers; the row is pool_id='__total__'.
    # total_series alimenta a time-series da aba Capacidade (peak_total no tempo;
    # capacity aqui é a provisionada flashada — o teto configurado do pricing é
    # aplicado sobre `total` pela rota, ver reports.get_pools_occupancy).
    total = None
    total_series: list = []
    if accessible_pools is None:
        tot = _rows_to_dicts(client.query(f"""
            SELECT max(peak_concurrency) AS peak_concurrency, max(provisioned_capacity) AS capacity
            FROM {db}.pool_occupancy_peaks FINAL
            WHERE tenant_id = {{tenant_id:String}} AND minute >= '{since}' AND minute < '{until}'
              AND pool_id = '__total__'
        """, parameters={"tenant_id": tenant_id}))
        if tot:
            cap  = int(tot[0].get("capacity") or 0)
            peak = int(tot[0].get("peak_concurrency") or 0)
            total = {"peak_concurrency": peak, "capacity": cap,
                     "headroom": max(cap - peak, 0), "utilization": round(peak / cap, 4) if cap else None}

        total_series = _rows_to_dicts(client.query(f"""
            SELECT {bucket_fn}                AS bucket,
                   max(peak_concurrency)      AS peak_concurrency,
                   max(provisioned_capacity)  AS capacity
            FROM {db}.pool_occupancy_peaks FINAL
            WHERE tenant_id = {{tenant_id:String}} AND minute >= '{since}' AND minute < '{until}'
              AND pool_id = '__total__'
            GROUP BY bucket ORDER BY bucket
        """, parameters={"tenant_id": tenant_id}))
        for r in total_series:
            r["bucket"] = _iso(r["bucket"])

    # ── Item 7b — séries de admissão (histórico do Monitor: licença de IA ×
    # fila gratuita, peak usado vs limite por bucket) ──────────────────────────
    #
    # **Fatia 3 (2026-08-02) — o produtor mudou, e este leitor mudou junto.**
    # `__reserved__` e `__shared__` deixaram de ser publicadas: mediam os baldes de
    # SESSÃO carvidos de `max_concurrent_sessions`, que somava licenças humanas e de
    # IA num pote só. `__admitted_ai__` é a linha que sobrou (denominador `C_ai`).
    # **Descontinuidade:** `ai_series` começa em 2026-08-02; consulta que abranja
    # data anterior devolve os buckets antigos VAZIOS, e isso é o correto — a série
    # velha tinha outro numerador e outro denominador, então continuá-la seria
    # emendar duas medições diferentes na mesma linha.
    admission: dict = {"ai_series": [], "buffer_series": []}
    if accessible_pools is None:
        adm_rows = _rows_to_dicts(client.query(f"""
            SELECT {bucket_fn}                AS bucket,
                   pool_id,
                   max(peak_concurrency)      AS used,
                   max(provisioned_capacity)  AS cap
            FROM {db}.pool_occupancy_peaks FINAL
            WHERE tenant_id = {{tenant_id:String}} AND minute >= '{since}' AND minute < '{until}'
              AND pool_id IN ('__admitted_ai__','__buffer__')
            GROUP BY bucket, pool_id ORDER BY bucket
        """, parameters={"tenant_id": tenant_id}))
        key_map = {"__admitted_ai__": "ai_series", "__buffer__": "buffer_series"}
        for r in adm_rows:
            admission[key_map[r["pool_id"]]].append({
                "bucket": _iso(r["bucket"]),
                "used":   int(r.get("used") or 0),
                "limit":  int(r.get("cap") or 0),
            })

    # `bucket` aqui é o VALIDADO — `query_pools_occupancy` resolve para `bkt` antes de
    # chamar. O `meta` reporta o bucket APLICADO, não o pedido: até o P3 ele ecoava o
    # parâmetro cru do chamador, então `bucket=xyz` respondia `meta.bucket: "xyz"` sobre
    # dados agregados por hora — o rótulo descrevendo algo que a query não fez.
    return {"data": {"series": series, "by_pool": by_pool, "total": total,
                     "total_series": total_series, "admission": admission},
            "meta": {"from_dt": since, "to_dt": until, "bucket": bucket}}


# ─── /reports/events — unified event stream ───────────────────────────────────
#
# UNION ALL across five source tables:
#   sessions          → session_opened / session_closed
#   messages          → message_sent
#   agent_events      → agent_done / routed
#   agent_pause_intervals → agent_pause / agent_ready
#   workflow_events   → workflow_{event_type}
#
# Each branch normalises to the common shape:
#   event_id, session_id, tenant_id, type, timestamp,
#   channel, pool_id, author_id, author_role, content
#
# The outer WHERE applies user-level filters (event_type, pool_id, channel,
# session_id) so ClickHouse can prune cheaply after the UNION.

_NULL = "CAST(NULL AS Nullable(String))"   # reused in every branch

# Which tables serve which event types (for query pruning)
_SESSION_TYPES  = {"session_opened", "session_closed"}
_MESSAGE_TYPES  = {"message_sent"}
# `routed` e `agent_done` viraram branches SEPARADAS sobre `segments` (antes eram
# uma só, sobre a extinta `agent_events`), então o pruning é por tipo individual —
# não há mais um conjunto a testar. Ver `include_routed`/`include_done`.
_PAUSE_TYPES    = {"agent_pause"}
_READY_TYPES    = {"agent_ready"}


def _events_sql_branches(
    db:               str,
    tenant_id:        str,
    since:            str,
    until:            str,
    event_type:       str | None,
    session_id:       str | None,
    accessible_pools: list[str] | None,
) -> tuple[list[str], dict]:
    """
    Builds the list of UNION ALL branch SQL strings and a shared parameters dict.

    All user-controlled values are passed as ClickHouse named parameters
    ({ev_tid:String} etc.) — never interpolated into the SQL string.

    Optimisation: when event_type is specified, only branches that can
    produce that type are included.  When event_type is None, all branches
    are included.

    Returns: (branches, params) where params must be passed to every query
    that uses the assembled UNION ALL SQL.
    """
    include_session  = (not event_type) or event_type in _SESSION_TYPES
    include_messages = (not event_type) or event_type in _MESSAGE_TYPES
    # `routed` e `agent_done` deixaram de ser uma branch só (eram duas linhas da
    # antiga `agent_events`); agora são duas branches sobre `segments`, então o
    # pruning pode ser por tipo. Sem isto, filtrar por `routed` ainda escaneava a
    # branch de `agent_done` inteira só para o filtro externo descartá-la.
    include_routed   = (not event_type) or event_type == "routed"
    include_done     = (not event_type) or event_type == "agent_done"
    include_pause    = (not event_type) or event_type in _PAUSE_TYPES
    include_ready    = (not event_type) or event_type in _READY_TYPES
    include_workflow = (not event_type) or (event_type and event_type.startswith("workflow_"))

    # Shared named params for all branches
    params: dict = {
        "ev_tid":   tenant_id,
        "ev_since": since,
        "ev_until": until,
    }
    if session_id:
        params["ev_sid"] = session_id

    # accessible_pools — server-controlled (from JWT), not user input.
    # Still use an IN-list but the values come from verified JWT claims.
    pool_scope = ""
    if accessible_pools:
        pool_list = ", ".join(f"'{p}'" for p in accessible_pools)
        pool_scope = f" AND pool_id IN ({pool_list})"

    # session_id filter inside subqueries — parameterised
    sid_filter_sess  = " AND session_id = {ev_sid:String}"    if session_id else ""
    sid_filter_msg   = " AND m.session_id = {ev_sid:String}"  if session_id else ""
    # (`sid_filter_agent` foi consolidado em `sid_filter_sess`: as branches de
    # routed/agent_done agora leem `segments` direto, sem o JOIN com sessions que
    # exigia o prefixo `ae.` — o filtro ficou idêntico ao das sessões.)

    # Isolamento de substrato (ADR): o stream de eventos é superfície OPERACIONAL,
    # então mostra produção. Aplicado a TODAS as branches que têm a coluna — uma
    # única branch filtrando produziria um stream incoerente (sessão importada
    # aparecendo aberta e com mensagens, mas sem nenhum agente).
    # `agent_pause_intervals` não tem `origin`; fica de fora por ausência de coluna.
    origin_live = " AND origin = 'live'"

    branches: list[str] = []

    if include_session:
        # session_opened
        branches.append(f"""
    SELECT
        session_id                        AS event_id,
        session_id,
        tenant_id,
        'session_opened'                  AS type,
        opened_at                         AS timestamp,
        channel,
        pool_id,
        {_NULL}                           AS author_id,
        'system'                          AS author_role,
        {_NULL}                           AS content
    FROM {db}.sessions FINAL
    WHERE tenant_id = {{ev_tid:String}}
      AND opened_at >= {{ev_since:String}} AND opened_at <= {{ev_until:String}}
      {origin_live}{pool_scope}{sid_filter_sess}""")

        # session_closed
        branches.append(f"""
    SELECT
        concat(session_id, ':closed')     AS event_id,
        session_id,
        tenant_id,
        'session_closed'                  AS type,
        assumeNotNull(closed_at)          AS timestamp,
        channel,
        pool_id,
        {_NULL}                           AS author_id,
        'system'                          AS author_role,
        if(close_reason IS NOT NULL, close_reason, {_NULL}) AS content
    FROM {db}.sessions FINAL
    WHERE tenant_id = {{ev_tid:String}}
      AND closed_at IS NOT NULL
      AND closed_at >= {{ev_since:String}} AND closed_at <= {{ev_until:String}}
      {origin_live}{pool_scope}{sid_filter_sess}""")

    if include_messages:
        pool_join_filter = ""
        if accessible_pools:
            pool_list = ", ".join(f"'{p}'" for p in accessible_pools)
            pool_join_filter = f" AND s.pool_id IN ({pool_list})"
        branches.append(f"""
    SELECT
        m.message_id                      AS event_id,
        m.session_id,
        m.tenant_id,
        'message_sent'                    AS type,
        m.timestamp,
        m.channel,
        s.pool_id,
        if(m.author_id IS NOT NULL, m.author_id, {_NULL}) AS author_id,
        m.author_role,
        if(m.content IS NOT NULL, m.content, {_NULL}) AS content
    FROM {db}.messages FINAL m
    LEFT JOIN (
        SELECT session_id, pool_id
        FROM {db}.sessions FINAL
        WHERE tenant_id = {{ev_tid:String}}{pool_join_filter}
    ) s ON m.session_id = s.session_id
    WHERE m.tenant_id = {{ev_tid:String}}
      AND m.timestamp >= {{ev_since:String}} AND m.timestamp <= {{ev_until:String}}
      AND m.visibility = 'all'
      AND m.origin = 'live'{sid_filter_msg}""")

    # ── routed / agent_done — fonte `segments` ────────────────────────────────
    # Substitui a antiga `agent_events` (substrato derivado que reescrevia, com
    # menos campos, o que `segments` já grava). O mapeamento é 1:1 — a tabela
    # antiga guardava DUAS linhas que nenhuma query juntava, enquanto `segments`
    # guarda UMA linha já fechada:
    #     routed     ≈ participant_joined → started_at
    #     agent_done ≈ participant_left   → ended_at (+ outcome)
    #
    # Ganhos: `channel` vem da própria linha (o JOIN com sessions some) e
    # `close_reason` enriquece o conteúdo do agent_done. A fonte antiga ainda
    # chegava vazia no tráfego vivo (o `runtime.ts` não manda outcome nem
    # pool_id), então isto conserta o dado, não só a origem.
    #
    # `author_role` passa a receber `role` (primary/specialist/…) em vez de
    # `agent_type_id` — o campo se chama author_ROLE e as outras branches do UNION
    # já põem papel ali ('system', m.author_role). A identidade do agente segue em
    # `author_id` (instance_id).
    if include_routed:
        branches.append(f"""
    SELECT
        concat(segment_id, ':routed')     AS event_id,
        session_id,
        tenant_id,
        'routed'                          AS type,
        started_at                        AS timestamp,
        channel,
        pool_id,
        if(instance_id != '', instance_id, {_NULL}) AS author_id,
        role                              AS author_role,
        {_NULL}                           AS content
    FROM {db}.segments FINAL
    WHERE tenant_id = {{ev_tid:String}}
      AND started_at >= {{ev_since:String}} AND started_at <= {{ev_until:String}}
      {origin_live}{pool_scope}{sid_filter_sess}""")

    if include_done:
        branches.append(f"""
    SELECT
        concat(segment_id, ':done')       AS event_id,
        session_id,
        tenant_id,
        'agent_done'                      AS type,
        assumeNotNull(ended_at)           AS timestamp,
        channel,
        pool_id,
        if(instance_id != '', instance_id, {_NULL}) AS author_id,
        role                              AS author_role,
        coalesce(outcome, close_reason, {_NULL}) AS content
    FROM {db}.segments FINAL
    WHERE tenant_id = {{ev_tid:String}}
      AND ended_at IS NOT NULL
      AND ended_at >= {{ev_since:String}} AND ended_at <= {{ev_until:String}}
      {origin_live}{pool_scope}{sid_filter_sess}""")

    if include_pause:
        branches.append(f"""
    SELECT
        interval_id                       AS event_id,
        {_NULL}                           AS session_id,
        tenant_id,
        'agent_pause'                     AS type,
        paused_at                         AS timestamp,
        {_NULL}                           AS channel,
        pool_id,
        instance_id                       AS author_id,
        agent_type_id                     AS author_role,
        reason_label                      AS content
    FROM {db}.agent_pause_intervals FINAL
    WHERE tenant_id = {{ev_tid:String}}
      AND paused_at >= {{ev_since:String}} AND paused_at <= {{ev_until:String}}
      {pool_scope}""")

    if include_ready:
        branches.append(f"""
    SELECT
        concat(interval_id, ':r')         AS event_id,
        {_NULL}                           AS session_id,
        tenant_id,
        'agent_ready'                     AS type,
        assumeNotNull(resumed_at)         AS timestamp,
        {_NULL}                           AS channel,
        pool_id,
        instance_id                       AS author_id,
        agent_type_id                     AS author_role,
        reason_label                      AS content
    FROM {db}.agent_pause_intervals FINAL
    WHERE tenant_id = {{ev_tid:String}}
      AND resumed_at IS NOT NULL
      AND resumed_at >= {{ev_since:String}} AND resumed_at <= {{ev_until:String}}
      {pool_scope}""")

    if include_workflow:
        # event_type in workflow_events is e.g. 'triggered', 'completed', etc.
        # We prefix with 'workflow_' to match front-end expectations.
        # If a specific workflow type is requested, strip the prefix for the inner filter.
        wf_type_filter = ""
        if event_type and event_type.startswith("workflow_"):
            params["ev_wf_type"] = event_type[len("workflow_"):]
            wf_type_filter = " AND event_type = {ev_wf_type:String}"
        branches.append(f"""
    SELECT
        we.event_id,
        {_NULL}                           AS session_id,
        we.tenant_id,
        concat('workflow_', we.event_type) AS type,
        we.timestamp,
        {_NULL}                           AS channel,
        {_NULL}                           AS pool_id,
        we.instance_id                    AS author_id,
        'workflow'                        AS author_role,
        if(we.status IS NOT NULL, we.status, {_NULL}) AS content
    FROM {db}.workflow_events FINAL we
    WHERE we.tenant_id = {{ev_tid:String}}
      AND we.timestamp >= {{ev_since:String}} AND we.timestamp <= {{ev_until:String}}
      {wf_type_filter}""")

    return branches, params


async def query_events(
    client:    Any,
    database:  str,
    tenant_id: str,
    from_dt:   str | None = None,
    to_dt:     str | None = None,
    *,
    session_id:       str | None       = None,
    pool_id:          str | None       = None,
    channel:          str | None       = None,
    event_type:       str | None       = None,
    accessible_pools: list[str] | None = None,
    page:      int = 1,
    page_size: int = 100,
) -> dict:
    """
    Unified event stream from sessions, messages, agent_events,
    agent_pause_intervals and workflow_events.

    Returns: { data: [EventRow], meta: { total, page, page_size, from_dt, to_dt } }
    """
    since = _ch_fmt(from_dt) if from_dt else _default_from()
    until = _ch_fmt(to_dt, upper=True) if to_dt else _default_to()
    if accessible_pools is not None and not accessible_pools:
        return {"data": [], "meta": _meta(page, page_size, 0, since, until)}
    try:
        return await asyncio.to_thread(
            _fetch_events, client, database, tenant_id, since, until,
            session_id, pool_id, channel, event_type, accessible_pools, page, page_size,
        )
    except Exception as exc:
        logger.warning("query_events failed tenant=%s: %s", tenant_id, exc)
        return {"data": [], "meta": _meta(page, page_size, 0, since, until), "error": "data_unavailable"}


def _fetch_events(
    client: Any, db: str, tenant_id: str,
    since: str, until: str,
    session_id: str | None, pool_id: str | None,
    channel: str | None, event_type: str | None,
    accessible_pools: list[str] | None,
    page: int, page_size: int,
) -> dict:
    branches, params = _events_sql_branches(
        db, tenant_id, since, until, event_type, session_id, accessible_pools,
    )
    if not branches:
        return {"data": [], "meta": _meta(page, page_size, 0, since, until)}

    union_sql = "\n    UNION ALL\n".join(branches)

    # Outer filters applied after UNION — all values are ClickHouse named params.
    outer_conditions: list[str] = []
    if event_type:
        outer_conditions.append("type = {out_event_type:String}")
        params["out_event_type"] = event_type
    if pool_id:
        outer_conditions.append("pool_id = {out_pool_id:String}")
        params["out_pool_id"] = pool_id
    if channel:
        outer_conditions.append("channel = {out_channel:String}")
        params["out_channel"] = channel
    if session_id:
        outer_conditions.append("session_id = {out_sid:String}")
        params["out_sid"] = session_id

    outer_where = ("WHERE " + " AND ".join(outer_conditions)) if outer_conditions else ""
    offset = (page - 1) * page_size

    count_sql = f"""
        SELECT count()
        FROM ({union_sql}) AS events
        {outer_where}
    """
    total = _count(client, count_sql, params)

    data_sql = f"""
        SELECT event_id, session_id, tenant_id, type, timestamp,
               channel, pool_id, author_id, author_role, content
        FROM ({union_sql}) AS events
        {outer_where}
        ORDER BY timestamp DESC
        LIMIT {page_size} OFFSET {offset}
    """
    result = client.query(data_sql, parameters=params)
    return {"data": _rows_to_dicts(result), "meta": _meta(page, page_size, total, since, until)}


# query_journeys_report / _fetch_journeys — REMOVED (Arc 19 Fase F)
# Journey entity superseded by Arc 19 unified session model.
# See CHANGELOG.md for history (Arcs 10, 16, 17).


# ─── Arc 12: Agent Business Events ────────────────────────────────────────────

async def query_agent_events_series(
    client:    Any,
    database:  str,
    tenant_id: str,
    from_dt:   str | None = None,
    to_dt:     str | None = None,
    *,
    category:    str | None = None,
    pool_id:     str | None = None,
    skill_id:    str | None = None,
    granularity: str = "day",  # day | week | hour
) -> dict:
    """
    Time-series of agent business events aggregated by (period, category).

    granularity:
      hour  — toStartOfHour(emitted_at)
      day   — toDate(emitted_at)           [default]
      week  — toMonday(emitted_at)

    Returns:
      data: list of {period, category, category_l1..l4, count, total_value, avg_value, min_value, max_value}
      meta: {from_dt, to_dt, granularity, category, pool_id, skill_id}
    """
    since = _ch_fmt(from_dt) if from_dt else _default_from()
    until = _ch_fmt(to_dt, upper=True) if to_dt else _default_to()
    try:
        return await asyncio.to_thread(
            _fetch_agent_events_series,
            client, database, tenant_id, since, until,
            category, pool_id, skill_id, granularity,
        )
    except Exception as exc:
        logger.warning("query_agent_events_series failed tenant=%s: %s", tenant_id, exc)
        return {
            "data": [],
            "meta": {
                "from_dt": since, "to_dt": until,
                "granularity": granularity, "category": category,
                "pool_id": pool_id, "skill_id": skill_id,
            },
            "error": "data_unavailable",
        }


def _fetch_agent_events_series(
    client:      Any,
    db:          str,
    tenant_id:   str,
    since:       str,
    until:       str,
    category:    str | None,
    pool_id:     str | None,
    skill_id:    str | None,
    granularity: str,
) -> dict:
    # Truncation function per granularity
    trunc = {
        "hour": "toStartOfHour(emitted_at)",
        "week": "toMonday(emitted_at)",
    }.get(granularity, "toDate(emitted_at)")

    conditions = [
        "tenant_id = {tenant_id:String}",
        f"emitted_at >= '{since}'",
        f"emitted_at <= '{until}'",
    ]
    params: dict = {"tenant_id": tenant_id}

    if category:
        # Support prefix match: "pool.skill" matches "pool.skill.metric_x"
        conditions.append("startsWith(category, {category:String})")
        params["category"] = category
    if pool_id:
        conditions.append("pool_id = {pool_id:String}")
        params["pool_id"] = pool_id
    if skill_id:
        conditions.append("skill_id = {skill_id:String}")
        params["skill_id"] = skill_id

    where = " AND ".join(conditions)

    result = client.query(f"""
        SELECT
            {trunc}              AS period,
            category,
            category_l1,
            category_l2,
            category_l3,
            category_l4,
            count()              AS count,
            sum(value)           AS total_value,
            avg(value)           AS avg_value,
            min(value)           AS min_value,
            max(value)           AS max_value
        FROM {db}.agent_business_events
        WHERE {where}
        GROUP BY period, category, category_l1, category_l2, category_l3, category_l4
        ORDER BY period ASC, category ASC
    """, parameters=params)

    return {
        "data": _rows_to_dicts(result),
        "meta": {
            "from_dt":     since,
            "to_dt":       until,
            "granularity": granularity,
            "category":    category,
            "pool_id":     pool_id,
            "skill_id":    skill_id,
        },
    }


async def query_agent_events_summary(
    client:    Any,
    database:  str,
    tenant_id: str,
    from_dt:   str | None = None,
    to_dt:     str | None = None,
    *,
    category: str | None = None,
    pool_id:  str | None = None,
    group_by: str = "category",  # category | skill_id | pool_id | agent_type_id
    page:     int = 1,
    page_size: int = 100,
) -> dict:
    """
    Aggregated summary of agent business events grouped by the chosen dimension.

    group_by:
      category      — one row per distinct category value  [default]
      skill_id      — one row per skill_id
      pool_id       — one row per pool_id
      agent_type_id — one row per agent_type_id

    Returns:
      data: list of {group_key, count, total_value, avg_value, min_value, max_value,
                     first_seen, last_seen}
      meta: pagination info
    """
    # Validate group_by to prevent injection
    # `segment_id` (Arc 12 fatia 2, 2026-08-03) habilita "KPI de negócio por
    # PARTICIPANTE" — antes a granularidade mais fina era `agent_type_id`, que agrega
    # todos os agentes daquele tipo. É o eixo que permite atribuir serviço executado a
    # quem executou (numa sessão orquestrada há vários emissores) e cruzar com
    # Evaluation, que é chaveada pelo mesmo `segment_id`.
    #
    # Eventos anteriores à fatia têm `segment_id = NULL` e caem num grupo próprio — a
    # ausência é visível em vez de diluída num agente qualquer.
    VALID_GROUP_BY = {"category", "skill_id", "pool_id", "agent_type_id", "segment_id"}
    if group_by not in VALID_GROUP_BY:
        group_by = "category"

    since = _ch_fmt(from_dt) if from_dt else _default_from()
    until = _ch_fmt(to_dt, upper=True) if to_dt else _default_to()
    try:
        return await asyncio.to_thread(
            _fetch_agent_events_summary,
            client, database, tenant_id, since, until,
            category, pool_id, group_by, page, page_size,
        )
    except Exception as exc:
        logger.warning("query_agent_events_summary failed tenant=%s: %s", tenant_id, exc)
        return {
            "data": [],
            "meta": _meta(page, page_size, 0, since, until),
            "error": "data_unavailable",
        }


def _fetch_agent_events_summary(
    client:    Any,
    db:        str,
    tenant_id: str,
    since:     str,
    until:     str,
    category:  str | None,
    pool_id:   str | None,
    group_by:  str,
    page:      int,
    page_size: int,
) -> dict:
    conditions = [
        "tenant_id = {tenant_id:String}",
        f"emitted_at >= '{since}'",
        f"emitted_at <= '{until}'",
    ]
    params: dict = {"tenant_id": tenant_id}

    if category:
        conditions.append("startsWith(category, {category:String})")
        params["category"] = category
    if pool_id:
        conditions.append("pool_id = {pool_id:String}")
        params["pool_id"] = pool_id

    where = " AND ".join(conditions)

    count_sql = f"""
        SELECT count() FROM (
            SELECT {group_by} AS group_key
            FROM {db}.agent_business_events
            WHERE {where}
            GROUP BY group_key
        ) AS s
    """
    total = _count(client, count_sql, params)

    offset = (page - 1) * page_size
    result = client.query(f"""
        SELECT
            {group_by}           AS group_key,
            count()              AS count,
            sum(value)           AS total_value,
            avg(value)           AS avg_value,
            min(value)           AS min_value,
            max(value)           AS max_value,
            min(emitted_at)      AS first_seen,
            max(emitted_at)      AS last_seen
        FROM {db}.agent_business_events
        WHERE {where}
        GROUP BY group_key
        ORDER BY count DESC
        LIMIT {page_size} OFFSET {offset}
    """, parameters=params)

    return {
        "data": _rows_to_dicts(result),
        "meta": _meta(page, page_size, total, since, until),
    }


async def query_agent_events_categories(
    client:    Any,
    database:  str,
    tenant_id: str,
    from_dt:   str | None = None,
    to_dt:     str | None = None,
    *,
    pool_id:  str | None = None,
    skill_id: str | None = None,
) -> dict:
    """
    Catalogue of distinct category values active in the time window.

    Used by the Dashboard AddCardModal to populate the category selector.

    Returns:
      data: list of {category, category_l1, category_l2, category_l3, category_l4,
                     event_count, last_seen}
      sorted by category ASC.
    """
    since = _ch_fmt(from_dt) if from_dt else _default_from()
    until = _ch_fmt(to_dt, upper=True) if to_dt else _default_to()
    try:
        return await asyncio.to_thread(
            _fetch_agent_events_categories,
            client, database, tenant_id, since, until,
            pool_id, skill_id,
        )
    except Exception as exc:
        logger.warning("query_agent_events_categories failed tenant=%s: %s", tenant_id, exc)
        return {"data": [], "meta": {"from_dt": since, "to_dt": until}, "error": "data_unavailable"}


def _fetch_agent_events_categories(
    client:   Any,
    db:       str,
    tenant_id: str,
    since:    str,
    until:    str,
    pool_id:  str | None,
    skill_id: str | None,
) -> dict:
    conditions = [
        "tenant_id = {tenant_id:String}",
        f"emitted_at >= '{since}'",
        f"emitted_at <= '{until}'",
    ]
    params: dict = {"tenant_id": tenant_id}

    if pool_id:
        conditions.append("pool_id = {pool_id:String}")
        params["pool_id"] = pool_id
    if skill_id:
        conditions.append("skill_id = {skill_id:String}")
        params["skill_id"] = skill_id

    where = " AND ".join(conditions)

    result = client.query(f"""
        SELECT
            category,
            any(category_l1)     AS category_l1,
            any(category_l2)     AS category_l2,
            any(category_l3)     AS category_l3,
            any(category_l4)     AS category_l4,
            count()              AS event_count,
            max(emitted_at)      AS last_seen
        FROM {db}.agent_business_events
        WHERE {where}
        GROUP BY category
        ORDER BY category ASC
        LIMIT 500
    """, parameters=params)

    return {
        "data": _rows_to_dicts(result),
        "meta": {"from_dt": since, "to_dt": until, "pool_id": pool_id, "skill_id": skill_id},
    }


# ─── /reports/evaluator-calibration (Arc 13) ─────────────────────────────────

async def query_evaluator_calibration(
    client:    Any,
    database:  str,
    tenant_id: str,
    from_dt:   str | None = None,
    to_dt:     str | None = None,
    *,
    campaign_id:  str | None = None,
    evaluator_id: str | None = None,
    skill_version: str | None = None,
    granularity:   str = "day",   # "day" | "week"
    divergence_threshold: float = 0.25,   # R8b — limiar (0–1) p/ recalibração recomendada
    min_sample_n:         int   = 30,     # R8b — N mínimo p/ disparar o sinal
) -> dict:
    """
    Calibration score time-series per skill version × time.

    calibration_score = approved_count / total_reviewed × 100

    R8b — divergence = 1 − calibration_score/100 (0–1). Cada linha ganha `divergence`
    e `recalibration_recommended` (= divergence > threshold ∧ total ≥ min_n). É SINAL,
    não auto-mutação — o humano decide recalibrar.

    Returns:
      data:    list of {period, skill_version, evaluator_id, total, approved,
                        recalibrated, bias_flagged, calibration_score,
                        divergence, recalibration_recommended}
      summary: {..., recalibration_recommended_count}
      meta:    {..., divergence_threshold, min_sample_n}
    """
    since = _ch_fmt(from_dt) if from_dt else _default_from()
    until = _ch_fmt(to_dt, upper=True) if to_dt else _default_to()
    try:
        return await asyncio.to_thread(
            _fetch_evaluator_calibration,
            client, database, tenant_id, since, until,
            campaign_id, evaluator_id, skill_version, granularity,
            divergence_threshold, min_sample_n,
        )
    except Exception as exc:
        logger.warning("query_evaluator_calibration failed tenant=%s: %s", tenant_id, exc)
        return {
            "data": [],
            "summary": {"total": 0, "approved": 0, "recalibrated": 0, "bias_flagged": 0, "calibration_score": None},
            "meta": {"from_dt": since, "to_dt": until, "campaign_id": campaign_id, "granularity": granularity},
            "error": "data_unavailable",
        }


def apply_divergence_flags(
    rows: list[dict], summary: dict, divergence_threshold: float, min_sample_n: int,
) -> int:
    """R8b — anota `divergence` (1 − score/100, 0–1) e `recalibration_recommended`
    (= divergence > limiar ∧ total ≥ N mínimo) em cada linha; grava o total recomendado
    no summary. Sinal, não auto-mutação. score=null → divergence=None, flag=False.
    Pura (sem I/O) — testável isoladamente. Retorna o nº de linhas recomendadas."""
    recommended_count = 0
    for row in rows:
        score = row.get("calibration_score")
        total = row.get("total") or 0
        if score is None:
            row["divergence"] = None
            row["recalibration_recommended"] = False
            continue
        divergence = round(1.0 - (float(score) / 100.0), 4)
        recommend = divergence > divergence_threshold and total >= min_sample_n
        row["divergence"] = divergence
        row["recalibration_recommended"] = recommend
        if recommend:
            recommended_count += 1
    summary["recalibration_recommended_count"] = recommended_count
    return recommended_count


def _fetch_evaluator_calibration(
    client:       Any,
    db:           str,
    tenant_id:    str,
    since:        str,
    until:        str,
    campaign_id:  str | None,
    evaluator_id: str | None,
    skill_version: str | None,
    granularity:  str,
    divergence_threshold: float = 0.25,
    min_sample_n:         int   = 30,
) -> dict:
    conditions = [
        "tenant_id = {tenant_id:String}",
        f"event_time >= '{since}'",
        f"event_time < '{until}'",
    ]
    params: dict = {"tenant_id": tenant_id}

    if campaign_id:
        conditions.append("campaign_id = {campaign_id:String}")
        params["campaign_id"] = campaign_id
    if evaluator_id:
        conditions.append("evaluator_id = {evaluator_id:String}")
        params["evaluator_id"] = evaluator_id
    if skill_version:
        conditions.append("skill_version = {skill_version:String}")
        params["skill_version"] = skill_version

    where = " AND ".join(conditions)

    # Period truncation expression per granularity
    if granularity == "week":
        period_expr = "toMonday(event_time)"
    else:  # day (default)
        period_expr = "toDate(event_time)"

    # Time-series: group by (period, skill_version, evaluator_id)
    ts_result = client.query(f"""
        SELECT
            {period_expr}                                                         AS period,
            skill_version,
            evaluator_id,
            count()                                                               AS total,
            countIf(decision = 'approved')                                        AS approved,
            countIf(decision = 'recalibrated')                                    AS recalibrated,
            countIf(decision = 'bias_flagged')                                    AS bias_flagged,
            round(
                if(count() > 0, countIf(decision = 'approved') * 100.0 / count(), null),
                2
            )                                                                     AS calibration_score
        FROM {db}.calibration_events FINAL
        WHERE {where}
        GROUP BY period, skill_version, evaluator_id
        ORDER BY period ASC, skill_version ASC
    """, parameters=params)

    rows = _rows_to_dicts(ts_result)

    # Aggregate summary across the whole period
    agg_result = client.query(f"""
        SELECT
            count()                               AS total,
            countIf(decision = 'approved')        AS approved,
            countIf(decision = 'recalibrated')    AS recalibrated,
            countIf(decision = 'bias_flagged')    AS bias_flagged,
            round(
                if(count() > 0,
                   countIf(decision = 'approved') * 100.0 / count(),
                   null),
                2
            )                                     AS calibration_score
        FROM {db}.calibration_events FINAL
        WHERE {where}
    """, parameters=params)

    agg_rows = _rows_to_dicts(agg_result)
    summary  = agg_rows[0] if agg_rows else {
        "total": 0, "approved": 0, "recalibrated": 0, "bias_flagged": 0, "calibration_score": None,
    }

    # Normalise period to ISO string for JSON serialisation
    for row in rows:
        p = row.get("period")
        if p is not None and not isinstance(p, str):
            row["period"] = str(p)

    apply_divergence_flags(rows, summary, divergence_threshold, min_sample_n)

    return {
        "data":    rows,
        "summary": summary,
        "meta": {
            "from_dt":      since,
            "to_dt":        until,
            "campaign_id":  campaign_id,
            "evaluator_id": evaluator_id,
            "skill_version": skill_version,
            "granularity":  granularity,
            "divergence_threshold": divergence_threshold,
            "min_sample_n":         min_sample_n,
        },
    }
