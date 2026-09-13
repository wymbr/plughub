"""
identity/index.py — IdentityIndex: Lookup 1 (resolução) + Lookup 2 (pendências).

Slice 1 é Redis-only (sem cadastro durável). Chaves:

  {t}:identity:{kind}:{value_hash}      → customer_id        (Lookup 1)
  {t}:customer:prospect:{customer_id}   → JSON prospect       (TTL deslizante)
  {t}:pending_by_customer:{customer_id} → HASH sid→PendingEntry (Lookup 2, TTL)

Nenhuma PII em claro nas chaves (§ normalize.hash_anchor). Validação forte de
identidade (identity_verify na retaguarda) está fora da Fase A — aqui a âncora é
tratada como "origem/fraca": deriva/provisiona o customer_id nativo.
"""
from __future__ import annotations

import json
import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

import redis.asyncio as aioredis

from .normalize import (
    DELIVERABLE_KINDS,
    anchor_rank_score,
    effective_verification_class,
    kind_confidence,
)
from .region import PhoneRegion, anchor_hash

logger = logging.getLogger("plughub.channel-gateway.identity")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_customer_id() -> str:
    # nativo, canônico, imutável — nasce aqui e é reusado na promoção ao PG (Slice 2).
    return "cus_" + uuid.uuid4().hex[:24]


def _encode_index(customer_id: str, verification_class: str) -> str:
    """Valor do índice Redis {t}:identity:{kind}:{hash} → JSON {cid, vc}."""
    return json.dumps({"cid": customer_id, "vc": verification_class})


def _decode_index(raw: str | bytes | None) -> tuple[str, str] | None:
    """
    Leitor TOLERANTE do índice: aceita o JSON novo {cid, vc} e a string pura
    legada (Slice 1/2), que é tratada como classe 'claimed'. Retorna (cid, vc)
    ou None quando ausente.
    """
    if raw is None:
        return None
    s = raw.decode() if isinstance(raw, bytes) else raw
    s = s.strip()
    if not s:
        return None
    if s.startswith("{"):
        try:
            d = json.loads(s)
            cid = d.get("cid", "")
            if cid:
                return cid, d.get("vc", "claimed")
            return None
        except Exception:
            return None
    # string pura legada → customer_id claimed
    return s, "claimed"


@dataclass
class CustomerRef:
    customer_id: str
    status:      str            # prospect | identified
    matched_by:  str            # existing | provisioned | ambiguous | durable | none
    #                             ⚠️ `ambiguous` vem SEMPRE com customer_id "" (IDN-12)
    confidence:  float
    # Classe de verificação da âncora vencedora (posse de canal). "none" quando
    # não resolveu. A plataforma respeita isso por padrão no gate de retomada
    # sensível (Fase 3): cross-canal de customer_resumable exige 'possessed'.
    verification_class: str = "none"   # claimed | possessed | none
    # Procedência da âncora vencedora (IDN-07 / ADR D13), lida SÓ do cadastro
    # durável e só quando ele atribui a âncora ao MESMO cliente. None = não
    # registrada, não resolveu, prospect efêmero, ambíguo, ou Redis e PG divergem.
    provenance: str | None = None


@dataclass
class PendingEntry:
    session_id:      str
    customer_id:     str
    resume_token:    str
    pool:            str
    skill_id:        str | None = None
    suspended_at:    str = field(default_factory=_now_iso)
    expires_at:      str | None = None
    policy:          str = "offer"          # offer | auto
    intent:          str | None = None
    context_preview: dict[str, Any] = field(default_factory=dict)
    # Journey J3 — raiz canônica da journey da sessão pendente (workflow em espera).
    # O intake que reconecta lê isto (via pending_workflow_get) para comandar o
    # journey_merge (unificar a journey do novo contato com a do processo pendente).
    root_session_id: str = ""

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @staticmethod
    def from_json(raw: str | bytes) -> "PendingEntry":
        d = json.loads(raw)
        return PendingEntry(**d)


# ── Procedência (PID-12 / ADR D13) ─────────────────────────────────────────────
#
# `verification_class` diz COMO a âncora foi provada; `provenance` diz DE ONDE ela
# veio. A regra que o eixo existe para sustentar — *"OTP só contra âncora
# autoritativa"* (PID-10) — só vale se `authoritative` tiver UMA porta, e o dono
# decidiu qual em 2026-09-12: a importação com credencial (`import_customers`).
#
# Por isso a trava mora no ÍNDICE, e não nas rotas: as rotas de identidade ainda
# não têm credencial (IDN-06), então qualquer garantia escrita lá seria contornável
# por quem chame o método por outro caminho. Aqui, todo escritor que não é a
# importação passa por `_writer_provenance`, que RECUSA `authoritative` alto.

PROVENANCE_AUTHORITATIVE = "authoritative"
PROVENANCE_WRITERS = frozenset({"declared", "channel_origin", "operator"})
# `operator` tem UM escritor: `register_by_operator`, atrás da rota com credencial.


def _writer_provenance(p: str | None) -> str | None:
    """Valida a procedência de um escritor que NÃO é a importação."""
    if p is None:
        return None
    if p == PROVENANCE_AUTHORITATIVE:
        raise ValueError(
            "procedencia `authoritative` so e carimbada por import_customers "
            "(PID-12) — este escritor nao e fonte autoritativa"
        )
    if p not in PROVENANCE_WRITERS:
        raise ValueError("procedencia desconhecida: %r" % (p,))
    return p


def _writer_verification_class(kind: str, vc: str) -> str:
    """Recusa gravar `possessed` em âncora que não recebe código (IDN-13).

    Quem pede isso é defeito do chamador — o único produtor de posse é o
    `otp_verify`, e o desafio já recusa kind não-entregável (PID-10). Recusar alto
    aqui é o que impede um segundo produtor de reabrir o furo em silêncio.
    """
    if vc == "possessed" and kind not in DELIVERABLE_KINDS:
        raise ValueError(
            "`possessed` em ancora %r: posse so se prova recebendo codigo, e so "
            "%s recebem (IDN-13, ADR D8)" % (kind, "/".join(DELIVERABLE_KINDS))
        )
    return vc


# Upsert de chave, ÚNICO para todos os escritores.
#
# ⚠️ As DUAS confianças da âncora — a prova de POSSE (`verification_class`,
# `verified_at`) e a ORIGEM (`provenance`) — são fato do par (âncora, CLIENTE), e
# só sobrevivem enquanto o cliente é o mesmo. Até 2026-09-13 (IDN-09) o `possessed`
# sobrevivia a qualquer reatribuição: o OTP provado por um cliente passava a valer
# para quem a âncora fosse atribuída depois — no Postgres, enquanto o índice Redis
# (`attach_anchor`) já fazia o certo. As duas casas discordavam, e a errada era a
# que responde quando o Redis esfria.
#
# ⚠️ A procedência é sticky SÓ enquanto a âncora pertence ao mesmo cliente. Se o
# `customer_id` muda, ela passa a ser a de quem reatribuiu: senão um escritor sem
# credencial anexaria um telefone importado ao próprio cadastro e HERDARIA o
# `authoritative` — a porta única viraria porta de qualquer um.
# Mesmo cliente: `authoritative` vence sempre (a importação promove uma âncora
# declarada); fora isso, a primeira origem registrada fica.
_SQL_UPSERT_KEY = """
    INSERT INTO identity.customer_secondary_keys
        (tenant_id, kind, value_hash, customer_id, confidence, verification_class,
         verified_at, provenance)
    VALUES ($1, $2, $3, $4, $5, $6, CASE WHEN $6 = 'possessed' THEN NOW() ELSE NULL END, $7)
    ON CONFLICT (tenant_id, kind, value_hash)
        DO UPDATE SET customer_id = EXCLUDED.customer_id,
                      confidence  = GREATEST(identity.customer_secondary_keys.confidence, EXCLUDED.confidence),
                      verification_class = CASE
                          WHEN identity.customer_secondary_keys.customer_id = EXCLUDED.customer_id
                           AND identity.customer_secondary_keys.verification_class = 'possessed'
                              THEN 'possessed'
                          ELSE EXCLUDED.verification_class END,
                      verified_at = CASE
                          WHEN identity.customer_secondary_keys.customer_id = EXCLUDED.customer_id
                              THEN COALESCE(identity.customer_secondary_keys.verified_at, EXCLUDED.verified_at)
                          ELSE EXCLUDED.verified_at END,
                      provenance = CASE
                          WHEN identity.customer_secondary_keys.customer_id <> EXCLUDED.customer_id
                              THEN EXCLUDED.provenance
                          WHEN EXCLUDED.provenance = 'authoritative'
                              THEN 'authoritative'
                          ELSE COALESCE(identity.customer_secondary_keys.provenance, EXCLUDED.provenance)
                      END
"""


def _vencedores(candidates: dict[str, tuple]) -> list[str]:
    """Clientes com o maior score. Mais de um = ambíguo.

    Uma regra só, e em função de módulo de propósito: o Lookup 1 teve duas regras de
    empate (IDN-11), e a mutação do gate troca ESTA função para provar que o caso
    ambíguo mede a regra e não a fixture.
    """
    if not candidates:
        return []
    top = max(c[0] for c in candidates.values())
    return [cid for cid, c in candidates.items() if c[0] == top]


def _pode_apontar(dono: tuple | None, winner: str) -> bool:
    """A âncora fria pode ser apontada ao vencedor no índice? Só sem dono no cadastro,
    ou com o próprio vencedor como dono (IDN-10)."""
    return dono is None or dono[0] == winner


PROVENANCE_OPERATOR = "operator"


def _ancora_de_outro(dono: tuple | None, quente: tuple | None, cid: str) -> bool:
    """A âncora já é de OUTRO cliente — no cadastro ou no índice quente (IDN-08).

    Função de módulo, como `_pode_apontar`: é a regra que o probe muta.
    """
    return bool((dono and dono[0] != cid) or (quente and quente[0] != cid))


@dataclass
class OperatorRegisterResult:
    """Resultado do cadastro feito por um OPERADOR no Console (IDN-08)."""
    outcome:     str = ""          # created | existing | refused
    customer_id: str = ""
    reason:      str = ""          # invalid_anchors | no_anchors | ambiguous | anchor_owned_by_other_customer
    invalid:     list[str] = field(default_factory=list)    # kinds recusados pela normalização
    conflicts:   list[str] = field(default_factory=list)    # kinds que já são de OUTRO cliente
    anchors:     int = 0


@dataclass
class ImportRowResult:
    external_id: str
    customer_id: str = ""
    outcome:     str = ""          # created | updated | refused
    anchors:     int = 0
    reason:      str = ""


class IdentityIndex:
    """
    Índice de identidade sobre Redis. Instanciado pelo WebhookAdapter (reusa o
    mesmo cliente Redis). `salt` é segredo (env); `prospect_ttl_s` /
    `resolution_index_ttl_s` são tuning (defaults; config-api no Slice 2).
    """

    def __init__(
        self,
        redis:                  aioredis.Redis,
        salt:                   str,
        prospect_ttl_s:         int = 2_592_000,   # 30d
        resolution_index_ttl_s: int = 2_592_000,   # 30d
        db_pool:                Any = None,        # asyncpg.Pool | None (Slice 2 durability)
        phone_region:           PhoneRegion = None,  # IDN-14: país do telefone sem DDI, por tenant
    ) -> None:
        self._redis = redis
        self._salt  = salt
        self._phone_region = phone_region
        self._prospect_ttl_s  = prospect_ttl_s
        self._index_ttl_s     = resolution_index_ttl_s
        self._db    = db_pool               # None → Redis-only (Slice 1 behaviour)

    # ── keys ──────────────────────────────────────────────────────────────────

    async def anchor_hash(self, tenant_id: str, kind: str, value: str) -> str:
        """Hash da âncora para o tenant (ValueError se inválida). Toda âncora do
        índice passa por aqui — ver `region.anchor_hash`."""
        return await anchor_hash(self._salt, self._phone_region, tenant_id, kind, value)

    def _identity_key(self, tenant_id: str, kind: str, value_hash: str) -> str:
        return f"{tenant_id}:identity:{kind}:{value_hash}"

    def _prospect_key(self, tenant_id: str, customer_id: str) -> str:
        return f"{tenant_id}:customer:prospect:{customer_id}"

    def _pending_key(self, tenant_id: str, customer_id: str) -> str:
        return f"{tenant_id}:pending_by_customer:{customer_id}"

    def _resume_tokens_key(self, tenant_id: str) -> str:
        return f"{tenant_id}:resume_tokens"

    # ── Lookup 1 — resolução / provisionamento ─────────────────────────────────

    async def resolve_or_provision(
        self,
        tenant_id: str,
        anchors:   list[dict[str, str]],   # [{kind, value}] crus (loopback; hash server-side)
        provision: bool = True,
    ) -> CustomerRef:
        """
        Resolve as âncoras a um customer_id. Se nenhuma casar e provision=True,
        cria um prospect efêmero (customer_id nativo) e indexa as âncoras.

        Desambiguação: cada âncora que casa contribui um candidato com a confiança
        do seu tipo; vence a maior confiança. Empate entre customer_ids diferentes
        na maior confiança → matched_by="ambiguous" (o fluxo decide 'ask').

        ⚠️ **UMA computação de candidatos, qualquer que seja a temperatura do índice**
        (IDN-11, 2026-09-13). Eram duas: com algum hit no Redis decidia-se só entre os
        hits, com empate → `ambiguous`; com o Redis frio, `_pg_resolve` pegava o
        primeiro estritamente maior e seguia. Mesmas âncoras, respostas diferentes —
        e, com o Redis parcialmente quente, o dono no cadastro das âncoras frias nem
        entrava na decisão. Hoje cada âncora vira candidato pela fonte que a conhece
        (Redis quente; senão o cadastro), e vencedor, empate e escrita no índice são
        decididos UMA vez. `matched_by` diz de onde veio a âncora vencedora
        (`existing` = índice, `durable` = cadastro).
        """
        # candidato por cliente: (score, confidence, vc, kind, vh, fonte)
        candidates: dict[str, tuple[float, float, str, str, str, str]] = {}
        valid_anchors: list[tuple[str, str, str]] = []   # (kind, value_hash, normalized-not-stored)
        donos: dict[tuple[str, str], tuple[str, str, float] | None] = {}  # âncora fria → dono no cadastro
        quentes: set[tuple[str, str]] = set()

        def candidato(cid: str, kind: str, vh: str, vc: str, conf: float, fonte: str) -> None:
            # IDN-13: a classe guardada no Redis pode ser o legado do OTP ao CPF.
            vc = effective_verification_class(kind, vc)
            score = anchor_rank_score(kind, vc)
            if cid not in candidates or score > candidates[cid][0]:
                candidates[cid] = (score, conf, vc, kind, vh, fonte)

        for a in anchors:
            kind  = a.get("kind", "")
            value = a.get("value", "")
            try:
                vh = await self.anchor_hash(tenant_id, kind, value)
            except ValueError as exc:
                logger.info("identity: resolve descartou ancora %s invalida — %s", kind, exc)
                continue
            valid_anchors.append((kind, vh, ""))
            hit = _decode_index(await self._redis.get(self._identity_key(tenant_id, kind, vh)))
            if hit:
                quentes.add((kind, vh))
                candidato(hit[0], kind, vh, hit[1], kind_confidence(kind), "existing")
                continue
            dono = await self._pg_key_owner(tenant_id, kind, vh)
            donos[(kind, vh)] = dono
            if dono:
                candidato(dono[0], kind, vh, dono[1], dono[2], "durable")

        if candidates:
            winners = _vencedores(candidates)
            if len(winners) > 1:
                # colisão real: mesmo top-score, ids diferentes → ambíguo (fluxo 'ask').
                # Não escreve NADA no índice sob ambiguidade — nem identidade
                # progressiva, nem reidratação.
                #
                # ⚠️ IDN-12 (2026-09-13): e NÃO devolve `customer_id`. Devolvia o do
                # primeiro candidato, e nenhum dos seis consumidores lia o
                # `matched_by` — o `pending_workflow_get` chegava a entregar o
                # `resume_token` do cliente escolhido ao acaso, e as gravações de
                # pendência gravavam sob ele. Um id arbitrário é o valor plausível na
                # forma mais cara; o vazio faz cada consumidor cair no caminho de
                # "não resolvido" que ele já tinha.
                logger.warning(
                    "identity: resolve AMBIGUO — %d clientes empatam no maior score; "
                    "nenhum customer_id devolvido (IDN-12)", len(winners),
                )
                return CustomerRef("", status="none", matched_by="ambiguous", confidence=0.0)

            winner = winners[0]
            _score, conf, vc, w_kind, w_vh, fonte = candidates[winner]
            # ── Escrita no índice, para as âncoras FRIAS:
            #   · sem dono no cadastro → identidade progressiva: anexa como `claimed`
            #     (reconectar com phone+email indexa o email; depois ele resolve só);
            #   · dono = vencedor → reidrata com a classe durável (não rebaixa possessed);
            #   · dono = OUTRO cliente → não toca, e loga (IDN-10: território de merge).
            # Âncoras quentes não são reescritas: já apontam para alguém, e reapontar
            # a de outro cliente seria o mesmo defeito pelo outro lado.
            for (kind, vh, _n) in valid_anchors:
                if (kind, vh) in quentes:
                    continue
                dono = donos.get((kind, vh))
                if not _pode_apontar(dono, winner):
                    logger.warning(
                        "identity: indice NAO apontou %s para %s — o cadastro a atribui a %s (IDN-10)",
                        kind, winner, dono[0] if dono else "?",
                    )
                    continue
                await self._redis.set(
                    self._identity_key(tenant_id, kind, vh),
                    _encode_index(winner, dono[1] if dono else "claimed"),
                    ex=self._index_ttl_s,
                )
            return CustomerRef(winner, status="identified",
                               matched_by=fonte, confidence=conf,
                               verification_class=vc,
                               provenance=await self._pg_provenance(tenant_id, w_kind, w_vh, winner))

        if not provision:
            return CustomerRef("", status="none", matched_by="none", confidence=0.0)

        # provisiona prospect efêmero + indexa âncoras
        customer_id = _new_customer_id()
        await self._redis.set(
            self._prospect_key(tenant_id, customer_id),
            json.dumps({
                "customer_id": customer_id,
                "status":      "prospect",
                "created_at":  _now_iso(),
                "kinds":       sorted({k for (k, _vh, _n) in valid_anchors}),
            }),
            ex=self._prospect_ttl_s,
        )
        for (kind, vh, _n) in valid_anchors:
            await self._redis.set(
                self._identity_key(tenant_id, kind, vh),
                _encode_index(customer_id, "claimed"),   # provisionada = não-verificada
                ex=self._index_ttl_s,
            )
        conf = max((kind_confidence(k) for (k, _vh, _n) in valid_anchors), default=0.0)
        return CustomerRef(customer_id, status="prospect",
                           matched_by="provisioned", confidence=conf,
                           verification_class="claimed")

    # ── Lookup 2 — pendências por cliente ──────────────────────────────────────

    async def write_pending(
        self, tenant_id: str, customer_id: str, entry: PendingEntry, ttl_s: int,
    ) -> None:
        key = self._pending_key(tenant_id, customer_id)
        await self._redis.hset(key, entry.session_id, entry.to_json())
        # renova o TTL do hash para o maior horizonte visto
        await self._redis.expire(key, ttl_s)

    async def find_pending(
        self, tenant_id: str, customer_id: str,
    ) -> list[PendingEntry]:
        """
        Retorna pendências vivas. Limpa entradas cujo resume_token não está mais
        em {t}:resume_tokens (consumido/expirado → stale).
        """
        key = self._pending_key(tenant_id, customer_id)
        raw = await self._redis.hgetall(key)
        if not raw:
            return []
        tokens_key = self._resume_tokens_key(tenant_id)
        live: list[PendingEntry] = []
        for field_id, value in raw.items():
            sid = field_id.decode() if isinstance(field_id, bytes) else field_id
            try:
                entry = PendingEntry.from_json(value)
            except Exception:
                await self._redis.hdel(key, sid)
                continue
            token_alive = await self._redis.hget(tokens_key, entry.resume_token)
            if not token_alive:
                await self._redis.hdel(key, sid)
                continue
            live.append(entry)
        # ordena mais recente primeiro
        live.sort(key=lambda e: e.suspended_at, reverse=True)
        return live

    async def consume_pending(
        self, tenant_id: str, customer_id: str, session_id: str,
    ) -> None:
        await self._redis.hdel(self._pending_key(tenant_id, customer_id), session_id)

    # ── Durabilidade (Slice 2 — PG schema `identity`) ──────────────────────────

    async def ensure_schema(self) -> None:
        """Cria o schema `identity` e tabelas (idempotente). No-op sem db_pool."""
        if self._db is None:
            return
        async with self._db.acquire() as conn:
            await conn.execute(_IDENTITY_SCHEMA_DDL)
        logger.info("IdentityIndex: PG schema `identity` ensured")
        await self.migrate_undeliverable_possession()

    async def migrate_undeliverable_possession(self) -> int:
        """Rebaixa a `claimed` toda posse gravada em âncora não-entregável (IDN-13).

        Idempotente, roda no boot. O legado nasceu do OTP ao CPF (até a PID-10): a
        classe dizia `possessed` e a prova era saber o número digitado. A leitura já
        o ignora (`effective_verification_class`); a migração faz o DADO parar de
        mentir, no cadastro e na chave quente do índice que aponta para o MESMO
        cliente (com o TTL preservado). `verified_at` vai a NULL: não houve
        verificação. Devolve quantas linhas mudaram, e LOGA — nunca muda calado.
        """
        if self._db is None:
            return 0
        async with self._db.acquire() as conn:
            rows = await conn.fetch(
                """
                UPDATE identity.customer_secondary_keys
                   SET verification_class = 'claimed', verified_at = NULL
                 WHERE verification_class = 'possessed' AND kind <> ALL($1::text[])
             RETURNING tenant_id, kind, value_hash, customer_id
                """,
                list(DELIVERABLE_KINDS),
            )
        indice = 0
        for r in rows:
            k = self._identity_key(r["tenant_id"], r["kind"], r["value_hash"])
            atual = _decode_index(await self._redis.get(k))
            if atual and atual[0] == r["customer_id"] and atual[1] == "possessed":
                await self._redis.set(k, _encode_index(r["customer_id"], "claimed"), keepttl=True)
                indice += 1
        if rows:
            logger.warning(
                "IdentityIndex: %d ancora(s) nao-entregavel(is) com `possessed` rebaixadas a "
                "`claimed` (%d chave(s) quente(s) do indice) — legado do OTP ao CPF (IDN-13)",
                len(rows), indice,
            )
        return len(rows)

    async def _pg_provenance(
        self, tenant_id: str, kind: str, value_hash: str, customer_id: str,
    ) -> str | None:
        """Procedência durável da âncora — SÓ se o cadastro a atribui a `customer_id`.

        ⚠️ **Por que o PG e nunca o Redis** (IDN-07, 2026-09-13): `authoritative` só é
        gravado pela importação, e ela escreve no PG. Copiar a procedência no índice
        Redis criaria uma segunda casa para a mesma confiança — e a IDN-09 acabou de
        medir o custo disso: as duas casas discordaram, e ninguém ficou vermelho.

        ⚠️ **Por que confere o cliente:** o índice Redis pode apontar uma âncora para um
        cliente que o cadastro não reconhece (IDN-10). Devolver a procedência do PG
        nesse caso emprestaria a confiança de um cliente a outro — o furo que a PID-12
        fechou na escrita, reaberto na leitura. Divergência LOGA e devolve None.
        """
        if self._db is None:
            return None
        async with self._db.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT customer_id, provenance FROM identity.customer_secondary_keys
                 WHERE tenant_id = $1 AND kind = $2 AND value_hash = $3 LIMIT 1
                """,
                tenant_id, kind, value_hash,
            )
        if not row:
            return None
        if row["customer_id"] != customer_id:
            logger.warning(
                "identity: pediu-se a procedencia da ancora %s para %s, e o cadastro a "
                "atribui a %s — procedencia NAO informada (IDN-10)",
                kind, customer_id, row["customer_id"],
            )
            return None
        return row.get("provenance")

    async def anchor_provenance(
        self, tenant_id: str, customer_id: str, kind: str, value: str,
    ) -> str | None:
        """Procedência de UMA âncora para UM cliente — a pergunta que a PID-10 faz antes
        de emitir OTP (*"esta âncora é autoritativa PARA ESTE cliente?"*). None quando
        inválida, ausente do cadastro, de outro cliente, ou não registrada."""
        if not customer_id:
            return None
        try:
            vh = await self.anchor_hash(tenant_id, kind, value)
        except ValueError:
            return None
        return await self._pg_provenance(tenant_id, kind, vh, customer_id)

    async def _pg_key_owner(
        self, tenant_id: str, kind: str, value_hash: str,
    ) -> tuple[str, str, float] | None:
        """(customer_id, verification_class, confidence) que o CADASTRO atribui à
        âncora, ou None (sem cadastro durável, ou âncora sem linha). É a fonte da
        âncora fria no Lookup 1 (IDN-11) e a pergunta que a escrita no índice faz
        antes de apontar uma âncora (IDN-10)."""
        if self._db is None:
            return None
        async with self._db.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT customer_id, verification_class, confidence FROM identity.customer_secondary_keys
                 WHERE tenant_id = $1 AND kind = $2 AND value_hash = $3 LIMIT 1
                """,
                tenant_id, kind, value_hash,
            )
        if not row:
            return None
        return (row["customer_id"],
                effective_verification_class(kind, row["verification_class"] or "claimed"),
                float(row["confidence"] or kind_confidence(kind)))

    async def _pg_key_class(self, tenant_id: str, kind: str, value_hash: str) -> str | None:
        """Classe de verificação durável de uma chave (para não rebaixar no reidratar)."""
        if self._db is None:
            return None
        async with self._db.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT verification_class FROM identity.customer_secondary_keys
                 WHERE tenant_id = $1 AND kind = $2 AND value_hash = $3 LIMIT 1
                """,
                tenant_id, kind, value_hash,
            )
        return (effective_verification_class(kind, row["verification_class"]) if row else None)

    async def attach_anchor(
        self, tenant_id: str, customer_id: str, kind: str, value: str,
        verification_class: str = "claimed", persist_durable: bool = False,
        provenance: str | None = None,
    ) -> bool:
        """
        Identidade progressiva — anexa/atualiza UMA âncora a um cliente existente.
        Sempre atualiza o índice Redis (classe embutida). Quando `persist_durable`
        (ex.: pós-OTP `possessed`, ou cliente já identificado), grava/atualiza a
        chave no PG e garante a linha `customers`. Nunca rebaixa uma classe já
        `possessed` para `claimed`. Retorna False se a âncora for inválida.

        `provenance` nunca pode ser `authoritative` aqui (ValueError) — PID-12.
        """
        prov = _writer_provenance(provenance)
        _writer_verification_class(kind, verification_class)
        if not customer_id:
            return False
        try:
            vh = await self.anchor_hash(tenant_id, kind, value)
        except ValueError:
            return False

        # não rebaixa: se o índice já tem possessed e chega claimed, mantém possessed —
        # desde que a posse guardada VALHA (IDN-13: a de um CPF não se preserva).
        existing = _decode_index(await self._redis.get(self._identity_key(tenant_id, kind, vh)))
        eff_vc = verification_class
        if (existing and existing[0] == customer_id and verification_class != "possessed"
                and effective_verification_class(kind, existing[1]) == "possessed"):
            eff_vc = "possessed"

        await self._redis.set(
            self._identity_key(tenant_id, kind, vh),
            _encode_index(customer_id, eff_vc),
            ex=self._index_ttl_s,
        )
        if persist_durable and self._db is not None:
            conf = kind_confidence(kind)
            async with self._db.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO identity.customers (customer_id, tenant_id, status)
                    VALUES ($1, $2, 'identified')
                    ON CONFLICT (customer_id) DO UPDATE SET updated_at = NOW()
                    """,
                    customer_id, tenant_id,
                )
                await conn.execute(
                    _SQL_UPSERT_KEY,
                    tenant_id, kind, vh, customer_id, conf, eff_vc, prov,
                )
        return True

    async def update_attributes(
        self, tenant_id: str, customer_id: str, attributes: dict[str, Any],
    ) -> bool:
        """
        Enriquecimento durável — merge (shallow) de atributos NÃO-SENSÍVEIS /
        MASCARADOS no cadastro (`customers.attributes` JSONB). Garante a linha do
        cliente. O contrato de "não-sensível" é do chamador (fluxo) — aqui só
        persiste. No-op sem db_pool ou sem atributos. Retorna True se gravou.
        """
        if self._db is None or not customer_id or not attributes:
            return False
        async with self._db.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO identity.customers (customer_id, tenant_id, attributes)
                VALUES ($1, $2, $3::jsonb)
                ON CONFLICT (customer_id)
                    DO UPDATE SET attributes = identity.customers.attributes || EXCLUDED.attributes,
                                  updated_at = NOW()
                """,
                customer_id, tenant_id, json.dumps(attributes),
            )
        logger.info("IdentityIndex: attributes merged customer=%s keys=%d", customer_id, len(attributes))
        return True

    async def search_customers(
        self, tenant_id: str, q: str, limit: int = 20,
    ) -> list[dict[str, Any]]:
        """
        Busca manual de cadastro (C1a — Cliente 360): por `customer_id` exato OU
        nome (`attributes->>'nome'`/`'name'` ILIKE). NÃO busca por âncora (telefone/
        email) — âncora exata resolve via `resolve_or_provision` (hash), não por
        texto no PG. Retorna [{customer_id, status, attributes}]. No-op sem db_pool.
        """
        q = (q or "").strip()
        if self._db is None or not q:
            return []
        like = f"%{q}%"
        async with self._db.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT customer_id, status, attributes
                  FROM identity.customers
                 WHERE tenant_id = $1
                   AND status <> 'merged'
                   AND (customer_id = $2
                        OR attributes->>'nome' ILIKE $3
                        OR attributes->>'name' ILIKE $3)
                 ORDER BY updated_at DESC
                 LIMIT $4
                """,
                tenant_id, q, like, limit,
            )
        out: list[dict[str, Any]] = []
        for r in rows:
            attrs = r["attributes"]
            if isinstance(attrs, str):
                try:
                    attrs = json.loads(attrs)
                except Exception:
                    attrs = {}
            out.append({
                "customer_id": r["customer_id"],
                "status":      r["status"],
                "attributes":  attrs or {},
            })
        return out

    async def get_customer(
        self, tenant_id: str, customer_id: str,
    ) -> dict[str, Any] | None:
        """
        Leitura durável de UM cliente por customer_id exato (cadastro §11). Retorna
        {customer_id, status, attributes} ou None (ausente / sem db_pool). Usado pelo
        outbound (Fase 3b) para consultar `attributes.do_not_contact` (opt-out global).
        Não resolve âncora nem provisiona — é read puro.
        """
        if self._db is None or not customer_id:
            return None
        async with self._db.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT customer_id, status, attributes FROM identity.customers "
                "WHERE customer_id = $1 AND tenant_id = $2 AND status <> 'merged'",
                customer_id, tenant_id,
            )
        if not row:
            return None
        attrs = row["attributes"]
        if isinstance(attrs, str):
            try:
                attrs = json.loads(attrs)
            except Exception:
                attrs = {}
        return {
            "customer_id": row["customer_id"],
            "status":      row["status"],
            "attributes":  attrs or {},
        }

    async def promote_to_durable(
        self, tenant_id: str, customer_id: str, anchors: list[dict[str, str]],
        status: str = "prospect", verification_class: str = "claimed",
    ) -> None:
        """
        Promove um cliente efêmero ao PG (gatilho concreto — ex.: registro de
        pendência). Upsert idempotente reusando o mesmo customer_id nativo, e
        grava as chaves secundárias hasheadas com a classe de verificação
        (default `claimed` — promoção por pendência não prova posse). Nunca
        rebaixa uma chave já `possessed`. No-op sem db_pool.
        """
        if self._db is None or not customer_id:
            return
        rows: list[tuple[str, str, float, str | None]] = []
        for a in anchors:
            kind, value = a.get("kind", ""), a.get("value", "")
            # procedência por âncora, declarada por quem EXTRAIU (o adapter sabe se
            # veio do canal ou do contexto). `authoritative` levanta — PID-12.
            prov = _writer_provenance(a.get("provenance"))
            _writer_verification_class(kind, verification_class)
            try:
                vh = await self.anchor_hash(tenant_id, kind, value)
            except ValueError:
                continue
            rows.append((kind, vh, kind_confidence(kind), prov))
        async with self._db.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO identity.customers (customer_id, tenant_id, status)
                VALUES ($1, $2, $3)
                ON CONFLICT (customer_id) DO UPDATE SET updated_at = NOW()
                """,
                customer_id, tenant_id, status,
            )
            for (kind, vh, conf, prov) in rows:
                await conn.execute(
                    _SQL_UPSERT_KEY,
                    tenant_id, kind, vh, customer_id, conf, verification_class, prov,
                )
        logger.info("IdentityIndex: promoted customer=%s to durable (keys=%d, vc=%s)",
                    customer_id, len(rows), verification_class)

    # ── Cadastro pelo OPERADOR — procedência `operator` (IDN-08) ──────────────

    async def register_by_operator(
        self, tenant_id: str, anchors: list[dict[str, str]], *,
        name: str = "", operator: str = "",
    ) -> OperatorRegisterResult:
        """Cadastro feito por um operador no Console, DURÁVEL e com procedência `operator`.

        ⚠️ IDN-08 (2026-09-13): a aba Cliente criava o cadastro pelo `/identity/resolve`
        com `provision: true` — prospect e índice só no Redis, sem procedência nenhuma —
        e mandava `kind: "telefone"`, que não é kind: a âncora era descartada calada e o
        cliente nascia SEM âncora, só com o nome. O operador não é fonte autoritativa,
        mas é uma origem que se registra; o carimbo precisava de um portador durável.

        Regras, todas ditas no resultado em vez de degradar:
          - âncora inválida (kind desconhecido, telefone sem país…) RECUSA o cadastro,
            nomeando os kinds — nunca se cria cliente sem as âncoras que o operador digitou;
          - as âncoras identificam um cliente → `existing`, e as que faltam são anexadas A
            ELE; nenhuma → `created`; mais de um → `ambiguous`;
          - âncora que o cadastro ou o índice já atribuem a OUTRO cliente RECUSA, sem
            escrever nada (território de merge, IDN-10) — nunca se move a âncora;
          - a procedência só vale onde a linha é nova: âncora já registrada do mesmo
            cliente mantém a origem que tinha (`_SQL_UPSERT_KEY`);
          - o nome só é gravado se o cliente ainda não tem um — o operador não
            sobrescreve cadastro existente por um campo de texto livre.
        """
        if self._db is None:
            raise RuntimeError("cadastro pelo operador exige o cadastro duravel (db_pool ausente)")
        res = OperatorRegisterResult()
        chaves: list[tuple[str, str, str]] = []
        for a in anchors or []:
            kind, value = str(a.get("kind", "")), str(a.get("value", ""))
            try:
                chaves.append((kind, value, await self.anchor_hash(tenant_id, kind, value)))
            except ValueError:
                res.invalid.append(kind or "?")
        if res.invalid:
            res.outcome, res.reason = "refused", "invalid_anchors"
            return res
        if not chaves:
            res.outcome, res.reason = "refused", "no_anchors"
            return res

        ref = await self.resolve_or_provision(
            tenant_id, [{"kind": k, "value": v} for (k, v, _h) in chaves], provision=False)
        if ref.matched_by == "ambiguous":
            res.outcome, res.reason = "refused", "ambiguous"
            return res
        cid = ref.customer_id or _new_customer_id()

        for kind, _v, vh in chaves:
            dono = await self._pg_key_owner(tenant_id, kind, vh)
            quente = _decode_index(await self._redis.get(self._identity_key(tenant_id, kind, vh)))
            if _ancora_de_outro(dono, quente, cid):
                res.conflicts.append(kind)
        if res.conflicts:
            logger.warning(
                "identity: cadastro do operador RECUSADO — ancora(s) %s ja atribuida(s) a outro "
                "cliente (operador=%s, IDN-08/IDN-10)", ",".join(res.conflicts), operator or "-",
            )
            res.outcome, res.reason = "refused", "anchor_owned_by_other_customer"
            return res

        for kind, value, _vh in chaves:
            await self.attach_anchor(tenant_id, cid, kind, value, verification_class="claimed",
                                     persist_durable=True, provenance=PROVENANCE_OPERATOR)
        if name.strip():
            atual = await self.get_customer(tenant_id, cid)
            attrs = (atual or {}).get("attributes") or {}
            if not (attrs.get("nome") or attrs.get("name")):
                await self.update_attributes(tenant_id, cid, {"nome": name.strip()})
        res.outcome = "existing" if ref.customer_id else "created"
        res.customer_id, res.anchors = cid, len(chaves)
        logger.info(
            "identity: cadastro do operador %s customer=%s kinds=%s operador=%s (IDN-08)",
            res.outcome, cid, ",".join(k for (k, _v, _h) in chaves), operator or "-",
        )
        return res

    # ── Importação — a ÚNICA porta de `authoritative` (PID-12) ────────────────

    async def import_customers(
        self, tenant_id: str, system: str, rows: list[dict[str, Any]], *, imported_by: str,
    ) -> list[ImportRowResult]:
        """
        Importa a base do tenant como FONTE AUTORITATIVA. Cada linha:
        `{external_id, anchors: [{kind, value}], attributes?}`.

        Decisão do dono (2026-09-12): a confiança é do PROCESSO de importação, feito
        com credencial — por isso quem chama esta função é só a rota que confere
        `contacts.importar_cadastro`, e nenhum outro escritor consegue carimbar
        `authoritative` (ver `_writer_provenance`).

        Identidade da linha = `(system, external_id)` em `customer_external_refs`:
        reimportar a mesma base ATUALIZA, não duplica.

        ⚠️ **Conflito RECUSA a linha, nunca funde.** Âncora que o cadastro durável já
        atribui a OUTRO cliente é território de merge (Fase C); resolvê-la aqui em
        silêncio seria a importação reescrevendo de quem é um telefone sem que
        ninguém decidisse. A linha recusada volta NOMEADA no resultado.

        A importação NÃO prova posse: `verification_class` fica `claimed` (um
        `possessed` já existente do mesmo cliente é preservado pelo upsert).
        """
        if self._db is None:
            raise RuntimeError("importacao exige o cadastro duravel (db_pool ausente)")
        out: list[ImportRowResult] = []
        for r in rows:
            ext = str(r.get("external_id") or "").strip()
            res = ImportRowResult(external_id=ext)
            out.append(res)
            if not ext:
                res.outcome, res.reason = "refused", "external_id vazio"
                continue
            chaves: list[tuple[str, str]] = []
            invalidas: list[str] = []
            for a in r.get("anchors") or []:
                try:
                    chaves.append((a.get("kind", ""), await self.anchor_hash(tenant_id, a.get("kind", ""), a.get("value", ""))))
                except (ValueError, AttributeError):
                    invalidas.append(str(a.get("kind", "?")) if isinstance(a, dict) else "?")
            if invalidas:
                res.outcome, res.reason = "refused", "ancora invalida: %s" % ",".join(invalidas)
                continue
            if not chaves:
                res.outcome, res.reason = "refused", "nenhuma ancora"
                continue
            attrs = r.get("attributes") or {}

            async with self._db.acquire() as conn:
                async with conn.transaction():
                    cid = await conn.fetchval(
                        "SELECT customer_id FROM identity.customer_external_refs "
                        "WHERE tenant_id = $1 AND system = $2 AND external_id = $3",
                        tenant_id, system, ext,
                    )
                    donos: set[str] = set()
                    for (kind, vh) in chaves:
                        dono = await conn.fetchval(
                            "SELECT customer_id FROM identity.customer_secondary_keys "
                            "WHERE tenant_id = $1 AND kind = $2 AND value_hash = $3",
                            tenant_id, kind, vh,
                        )
                        if dono:
                            donos.add(dono)
                    criado = False
                    if cid:
                        alheios = donos - {cid}
                        if alheios:
                            res.outcome = "refused"
                            res.reason = "ancora ja pertence a outro cliente (%d)" % len(alheios)
                            continue
                    elif len(donos) > 1:
                        res.outcome = "refused"
                        res.reason = "ancoras apontam para %d clientes diferentes" % len(donos)
                        continue
                    elif donos:
                        cid = next(iter(donos))
                        outro_ext = await conn.fetchval(
                            "SELECT external_id FROM identity.customer_external_refs "
                            "WHERE tenant_id = $1 AND system = $2 AND customer_id = $3",
                            tenant_id, system, cid,
                        )
                        if outro_ext and outro_ext != ext:
                            res.outcome = "refused"
                            res.reason = "cliente ja importado com outro external_id"
                            continue
                    else:
                        cid, criado = _new_customer_id(), True

                    status = await conn.fetchval(
                        "SELECT status FROM identity.customers WHERE customer_id = $1", cid,
                    )
                    if status == "merged":
                        res.outcome, res.reason = "refused", "cliente de destino esta merged"
                        continue
                    await conn.execute(
                        """
                        INSERT INTO identity.customers (customer_id, tenant_id, status, attributes)
                        VALUES ($1, $2, 'identified', $3::jsonb)
                        ON CONFLICT (customer_id) DO UPDATE
                           SET status     = 'identified',
                               attributes = identity.customers.attributes || EXCLUDED.attributes,
                               updated_at = NOW()
                        """,
                        cid, tenant_id, json.dumps(attrs),
                    )
                    await conn.execute(
                        """
                        INSERT INTO identity.customer_external_refs
                            (tenant_id, system, external_id, customer_id, confidence, resolved_at)
                        VALUES ($1, $2, $3, $4, 1.0, NOW())
                        ON CONFLICT (tenant_id, system, external_id)
                            DO UPDATE SET resolved_at = NOW()
                        """,
                        tenant_id, system, ext, cid,
                    )
                    for (kind, vh) in chaves:
                        await conn.execute(
                            _SQL_UPSERT_KEY,
                            tenant_id, kind, vh, cid, kind_confidence(kind), "claimed",
                            PROVENANCE_AUTHORITATIVE,
                        )
                    res.customer_id = cid
                    res.outcome = "created" if criado else "updated"
                    res.anchors = len(chaves)

            # Índice Redis depois do COMMIT: o Lookup 1 lê o Redis primeiro, e um
            # prospect efêmero apontado para a mesma âncora faria o resolve devolver
            # o prospect em vez do cliente importado. A classe vem do PG (não rebaixa).
            repontadas = 0
            for (kind, vh) in chaves:
                atual = _decode_index(await self._redis.get(self._identity_key(tenant_id, kind, vh)))
                if atual and atual[0] != cid:
                    repontadas += 1
                vc = await self._pg_key_class(tenant_id, kind, vh) or "claimed"
                await self._redis.set(self._identity_key(tenant_id, kind, vh),
                                      _encode_index(cid, vc), ex=self._index_ttl_s)
            if repontadas:
                logger.warning(
                    "identity import: %d ancora(s) do external_id=%s apontavam para prospect "
                    "efemero no Redis e passaram ao cliente importado %s",
                    repontadas, ext, cid,
                )

        contagem = {k: sum(1 for x in out if x.outcome == k) for k in ("created", "updated", "refused")}
        logger.info(
            "identity import: tenant=%s system=%s por=%s linhas=%d criadas=%d atualizadas=%d recusadas=%d",
            tenant_id, system, imported_by, len(out),
            contagem["created"], contagem["updated"], contagem["refused"],
        )
        return out


_IDENTITY_SCHEMA_DDL = """
CREATE SCHEMA IF NOT EXISTS identity;

CREATE TABLE IF NOT EXISTS identity.customers (
    customer_id  TEXT PRIMARY KEY,
    tenant_id    TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'prospect',   -- prospect | identified | merged
    merged_into  TEXT,
    attributes   JSONB NOT NULL DEFAULT '{}',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_identity_customers_tenant ON identity.customers (tenant_id);

CREATE TABLE IF NOT EXISTS identity.customer_secondary_keys (
    tenant_id          TEXT NOT NULL,
    kind               TEXT NOT NULL,
    value_hash         TEXT NOT NULL,
    customer_id        TEXT NOT NULL,
    confidence         DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    verification_class TEXT NOT NULL DEFAULT 'claimed',   -- claimed | possessed
    verified_at        TIMESTAMPTZ,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (tenant_id, kind, value_hash)
);
CREATE INDEX IF NOT EXISTS idx_identity_seckeys_customer ON identity.customer_secondary_keys (customer_id);
-- Migração tolerante p/ DBs do Slice 2 (tabela sem a coluna):
ALTER TABLE identity.customer_secondary_keys
    ADD COLUMN IF NOT EXISTS verification_class TEXT NOT NULL DEFAULT 'claimed';
-- PID-12 / ADR D13: procedência, o segundo eixo da âncora (DE ONDE veio).
-- NULL = "não registrada" — toda linha anterior a 2026-09-13. Nullable de
-- propósito: carimbar `declared` no legado inventaria uma origem que ninguém mediu.
ALTER TABLE identity.customer_secondary_keys
    ADD COLUMN IF NOT EXISTS provenance TEXT;

CREATE TABLE IF NOT EXISTS identity.customer_external_refs (
    tenant_id    TEXT NOT NULL,
    system       TEXT NOT NULL,
    external_id  TEXT NOT NULL,
    customer_id  TEXT NOT NULL,
    confidence   DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    resolved_at  TIMESTAMPTZ,
    PRIMARY KEY (tenant_id, system, external_id)
);
CREATE INDEX IF NOT EXISTS idx_identity_extrefs_customer ON identity.customer_external_refs (customer_id);

CREATE TABLE IF NOT EXISTS identity.customer_merges (
    tenant_id     TEXT NOT NULL,
    from_customer TEXT NOT NULL,
    into_customer TEXT NOT NULL,
    merged_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (tenant_id, from_customer)
);
"""
