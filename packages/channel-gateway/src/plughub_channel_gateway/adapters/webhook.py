"""
adapters/webhook.py
Webhook channel adapter — Arc 19 Unified Session Model.

Architecture: docs/arcos/arc19-unified-session-model.md

This adapter treats workflow execution as a channel, exactly like WhatsApp or
voice.  Each skill registered in a webhook pool is an "endpoint" (analogous to
a DIN in voice or a WA number).  Triggering a workflow creates a normal PlugHub
session with channel_type="webhook".

Inbound / trigger flow:
  POST /v1/channels/webhook/{skill_id}
    { tenant_id, trigger_type, metadata?, customer_id? }
    → session created → conversations.inbound published → returns session_id

Resume flow (after a suspend step):
  POST /v1/channels/webhook/resume/{resume_token}
    → Redis hash lookup: {tenant}:resume_tokens → session_id
    → session_resumed event published → routing engine reallocates
    → returns session_id

Status query:
  GET /v1/channels/webhook/{session_id}/status
    → reads session status from Redis stream metadata
    → returns { session_id, status: "active"|"suspended"|"closed" }

Outbound (ChannelAdapter interface):
  Webhook workflows do not deliver messages to an external channel — they
  orchestrate agents that do.  Therefore deliver_text / deliver_menu /
  deliver_typing are no-ops.  deliver_session_closed is also a no-op because
  session closure is managed by the orchestrator-bridge directly.

Resume token storage (written by skill-flow-engine suspend executor):
  Redis hash: {tenant_id}:resume_tokens
    field: {resume_token}  (opaque 43-char)
    value: {session_id}:{step_id}:{expires_at_iso}
  TTL: same as the session (set by suspend executor via EXPIRE)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import secrets
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

import jwt as pyjwt          # Journey J4c — mint the webchat JWT that pre-binds the survey session
import redis.asyncio as aioredis
from aiokafka import AIOKafkaProducer

from ..config import Settings
from ..identity import IdentityIndex, OtpService, PendingEntry
from .base import ChannelAdapter

logger = logging.getLogger("plughub.channel-gateway.webhook")

# A5.4 — masking net-pass de PII em valores de edição de aprovação (defesa em
# profundidade; o mecanismo primário é o `masked` field-level do DialogForm, que
# nem chega ao servidor). Nunca gravar PII crua na stream/log.
#
# ⚠️ ALINHADO em 2026-08-26 (fase V2 do arco ALLOWLIST). Esta tabela dizia em prosa
# *"mesmos alvos das DEFAULT_MASKING_RULES"* e JÁ HAVIA DIVERGIDO — era o 6º de sete
# inventários de categoria do repositório, e o único com produtor vivo divergente:
#   · cartão casava `\b(?:\d[ -]?){13,16}\b` (13 a 16 dígitos, qualquer separador),
#     enquanto o canônico casa `(?:\d{4}[\s-]?){3}\d{4}` — grupos de 4;
#   · CPF devolvia `***.***.***-00`, o canônico devolve `*********00`;
#   · cartão devolvia `**** 3456`, o canônico devolve `************3456`;
#   · telefone devolvia `(***4321`, o canônico devolve `(*******4321`;
#   · nenhuma linha carregava a CATEGORIA, então nada aqui podia ser auditado nem
#     comparado com o resto.
# A divergência não era teórica e nem era de duas portas: MEDIDAS as três
# (`infra/test/q_masking_display_parity.sh`, 5 vetores), NENHUMA das cinco linhas era
# unânime. A única coincidência — e-mail entre TS e esta porta — é acidente
# aritmético: aqui era `"***"` fixo, lá é `ceil(len(prefixo)/4)`, e o prefixo do vetor
# tinha 10 caracteres.
#
# Estrutura agora espelha `quality-ingest/masking.py` (que espelha
# `DEFAULT_MASKING_RULES` em @plughub/schemas/audit.ts): tabela de dados + UMA função
# de aplicação, em vez de lambdas por linha. Regexes, replacements e semântica de
# preserve são os canônicos, e o gate `probe_masking_rule_parity.sh` compara as três
# portas sobre os MESMOS vetores.
#
# Dívida declarada, não escondida: continua sendo CÓPIA. O fim dela é o catálogo
# (`masking.types` no config-api) ser lido em runtime — o que exige recusar alto se a
# config não vier, porque degradar em masking é vazar PII. Fase própria.
_PII_RULES: list[dict[str, Any]] = [
    {
        "category":             "cpf",
        "pattern":              re.compile(r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b"),
        "replacement":          "***.***.***.--",
        "preserve_last_digits": 2,
    },
    {
        "category":             "credit_card",
        "pattern":              re.compile(r"\b(?:\d{4}[\s-]?){3}\d{4}\b"),
        "replacement":          "**** **** **** ****",
        "preserve_last_digits": 4,
    },
    {
        "category":             "phone",
        # `(?<!\w)` e não `\b` — ver audit.ts: com `\b` o `\(?` é ramo morto e o
        # parêntese de abertura ficava órfão (`(***4321`).
        "pattern":              re.compile(r"(?<!\w)(?:\+55\s?)?(?:\(?\d{2}\)?[\s-]?)?9?\d{4}[-\s]?\d{4}\b"),
        "replacement":          "(##) ****-####",
        "preserve_last_digits": 4,
    },
    {
        "category":         "email_addr",
        "pattern":          re.compile(r"\b[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}\b"),
        "replacement":      "****@****.***",
        "preserve_pattern": re.compile(r"(@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})$"),
    },
]


def _mask_match(match_text: str, rule: dict[str, Any]) -> str:
    """Constrói o display mascarado de UM trecho casado.

    Semântica CANÔNICA = `MaskingService.buildDisplay`
    (`mcp-server-plughub/src/lib/masking.ts`), que é a que produz o `display_partial`
    entregue ao cliente pelo WebSocket:
      1. `preserve_pattern` → `"*" × ceil(len(prefixo)/4)` + trecho preservado;
      2. `preserve_last_digits` → `"*" × (n_dígitos − N)` + últimos N dígitos;
      3. senão → `replacement` (ÚLTIMO recurso, não a forma padrão).

    ⚠️ Escolha registrada em 2026-08-26, depois de medir as três portas lado a lado
    (`infra/test/q_masking_display_parity.sh`): nenhuma das cinco linhas era unânime.
    Alinhar na direção do `replacement` mudaria o que o operador vê no stream vivo e
    deixaria tokens já gravados com a grafia antiga.
    """
    preserve_pattern = rule.get("preserve_pattern")
    if preserve_pattern is not None:
        m = preserve_pattern.search(match_text)
        if m:
            preserved = m.group(1) if m.lastindex else m.group(0)
            prefix = match_text[: len(match_text) - len(preserved)]
            masked_len = max(1, -(-len(prefix) // 4))  # ceil(len/4)
            return f"{'*' * masked_len}{preserved}"
    keep = rule.get("preserve_last_digits") or 0
    if keep > 0:
        digits = re.sub(r"\D", "", match_text)
        if len(digits) > keep:
            return f"{'*' * (len(digits) - keep)}{digits[-keep:]}"
    return rule["replacement"]


# ── Fase F (D7) — resume terminal-uma-vez ─────────────────────────────────────
#
# Toda workflow suspensa tem SEMPRE mais de um retomador possível: quem deveria
# retomá-la e o scanner de prazo, que varre `*:resume_tokens` a cada 60 s no
# MESMO processo (e no mesmo event loop) deste endpoint. Onde há item de trabalho
# parqueado existe um terceiro — o encerramento do supervisor, que não tem rota
# própria: ele faz este mesmo resume com `decision=timeout`. Os três entram por
# `handle_resume`, então a unicidade é propriedade de UMA função.
#
# A janela ficava entre o HGET do token (topo) e o HDEL (fim), com um round-trip
# HTTP ao árbitro, o xadd e o publish no meio — `await`s de sobra para dois
# resumes se interlaçarem sem precisar de duas réplicas. Medido em 2026-08-04:
# duas expirações reais (`acw_expired`, 30/07, pool `retencao_humano-int`)
# atravessaram essa janela com um humano segurando o item havia ~100 s.
#
# POR QUE UM LOCK, E NÃO SUBIR O HDEL. O apagamento tardio não é descuido: ele
#   (a) preserva a retentabilidade quando o caminho estoura no meio — o token
#       sobrevive e o scanner tenta de novo em 60 s;
#   (b) faz um submit recusado por posse (403 do A5) NÃO consumir o item.
# Subir o HDEL trocaria a corrida por item permanentemente irresumível — o
# estado "sem saída" que a medição mostrou não existir em nenhum dos 4 itens
# vivos. O lock dá exclusão mútua sem tocar em nenhum dos dois efeitos.
#
# O árbitro (`work_task_expire`, routing) segue idempotente de propósito: essa é
# a propriedade certa para ele. O que estava errado era ela ser a ÚNICA linha de
# defesa — idempotência devolve 200 tendo feito nada, que é indistinguível de
# sucesso para quem chama. A recusa sobe para o token; o segundo não chega lá.
_RESUME_INFLIGHT_TTL_S = 45      # teto do corpo do resume; o lock sai no `finally`
_RESUME_TERMINAL_TTL_S = 90000   # 25 h — mesma convenção do ledger `work_task`


def _resume_inflight_key(tenant_id: str, resume_token: str) -> str:
    """`{tenant}:resume_inflight:{token}` — o lock de exclusão mútua."""
    return f"{tenant_id}:resume_inflight:{resume_token}"


def _resume_terminal_key(tenant_id: str, resume_token: str) -> str:
    """`{tenant}:resume_terminal:{token}` — quem encerrou, por quê, quando."""
    return f"{tenant_id}:resume_terminal:{resume_token}"


def _resume_meta_key(tenant_id: str, resume_token: str) -> str:
    """
    `{tenant}:resume_meta:{token}` — o registro POR TOKEN (Fase 1 do arco de workflow).

    ⚠️ **Por que ele existe: o TTL de `{tenant}:resume_tokens` é do HASH, não do
    token.** Todos os escritores aplicam `EXPIRE` na chave inteira, e o hash é
    compartilhado por TODAS as sessões do tenant — então o último escritor redefine
    o prazo de todos. Um `collect` de 1 h escrito depois **encurta** um token de
    suspend de 48 h, e quando o hash vence os dois somem juntos. Enquanto o resume
    era só interno isso era higiene; com uma porta externa o prazo do token vira
    contrato com um terceiro ("seu link vale 48 h"), e a plataforma não o cumpria.

    Guarda `expires_at`, `suspend_reason`, `step_id` e `opened_at`, com TTL do
    PRÓPRIO token. Dois consumidores, ambos vivos:
      1. **fallback de resolução** — se a entrada do hash sumiu mas o meta está vivo,
         o token é reidratado (e reinserido no hash, para o scanner de prazo voltar a
         enxergá-lo). Nunca ressuscita token CONSUMIDO: o registro terminal é
         checado antes.
      2. **substrato da transição** (D4/Fase 2) — `suspend_reason` e
         `resume_expires_at` não existem em lugar nenhum durável hoje
         (`workflow_events` tem produtor morto e ZERO linhas). Lido com `get`,
         **nunca `getdel`**, antes do consumo — é o que permite ao escritor do
         resume mandar a LINHA INTEIRA ao `ReplacingMergeTree` sem reidratar do
         banco.
    """
    return f"{tenant_id}:resume_meta:{resume_token}"


_RESUME_META_BUFFER_S = 3600   # mesma folga de 1 h que os escritores do hash usam


def _terminal_cause(payload: dict[str, Any]) -> str:
    """
    A causa do encerramento, das TRÊS que existem. Era uma expressão inline no
    meio do `handle_resume` (e por isso não tinha teste); virou função porque a
    Fase F passou a precisar dela em dois lugares — o encerramento do item no
    routing e o registro terminal que dá NOME à recusa do segundo.

    A distinção vem do `source`, escrito pelo gatilho: o tool marca "agent", o
    scanner "timeout_scanner", o endpoint do supervisor "supervisor:{sub}".

    ⚠️ **Isso vale para os gatilhos internos, não para a porta pública.** O
    `POST /v1/channels/webhook/resume/{token}` repassa o `payload` do corpo
    verbatim, então um chamador externo PODE declarar `source: "supervisor:x"` e
    obter o carimbo `acw_supervisor_closed` — no segmento e, desde a Fase F,
    também no registro terminal (que é durável por 25 h). A exposição é anterior
    a esta fase (a expressão inline lia o mesmo campo), mas a Fase F a tornou
    persistente, então ela deixa de ser aceitável por omissão.
    O fecho pede ler `_resolve_approver_principal` primeiro — sem principal
    verificado no caminho genérico de form-fill, um downgrade cego derrubaria o
    encerramento legítimo do supervisor, que nunca foi exercitado e portanto não
    reclamaria. Item próprio em `TODO.md`; não misturar com a corrida da D7.

    Espelha `_wrapup_close_reason` do orchestrator-bridge, que carimba o mesmo
    fato no SEGMENTO — lá `task_submitted`, aqui `task_done`, por o nome do
    routing já existir antes. Mesmo eixo, dois domínios; não inventar um terceiro.
    """
    if payload.get("decision") != "timeout":
        return "task_done"
    if str(payload.get("source") or "").startswith("supervisor"):
        return "acw_supervisor_closed"
    return "acw_expired"


def _resume_actor(payload: dict[str, Any], approver: dict[str, Any] | None) -> str:
    """
    QUEM está encerrando. Preferência pelo principal verificado (o aprovador do
    A5, que passou por JWT+ABAC); na ausência dele, o `source` do payload — que
    o `handle_resume` garante existir (`setdefault("external")`), então esta
    função nunca devolve vazio.
    """
    if approver and approver.get("decided_by"):
        return f"human:{approver['decided_by']}"
    return str(payload.get("source") or "external")


class ResumeAlreadyTerminalError(RuntimeError):
    """
    Fase F (D7) — este resume já foi encerrado, ou está sendo encerrado agora
    por outro. Mapeia para **409**, não 404.

    A diferença não é cosmética. 404 afirma *"o token nunca existiu ou venceu"*,
    e era exatamente o que o agente recebia quando um supervisor encerrava o item
    que ele estava preenchendo: a tela dizia que a sessão dele tinha expirado. A
    D7 pede recusa explícita, e recusa sem nome — sobre uma causa errada — não é
    explícita. 409 afirma *"já acabou"*, e o detalhe diz por quem e por quê.

    `state`:
      · `in_flight` — outro resume está NO MEIO do caminho (corrida real);
      · `terminal`  — outro resume já concluiu (o caso sequencial).
    """

    def __init__(
        self,
        *,
        state:      Literal["in_flight", "terminal"],
        session_id: str = "",
        by:         str = "",
        cause:      str = "",
        at:         str = "",
    ) -> None:
        self.state      = state
        self.session_id = session_id
        self.by         = by
        self.cause      = cause
        self.at         = at
        super().__init__(
            f"resume already {state} (by={by or '?'} cause={cause or '?'} at={at or '?'})"
        )

    def as_detail(self) -> dict[str, str]:
        """Corpo do 409 — o que a tela precisa para dizer a frase certa."""
        return {
            "error":      "resume_already_terminal",
            "state":      self.state,
            "session_id": self.session_id,
            "closed_by":  self.by,
            "cause":      self.cause,
            "closed_at":  self.at,
        }


def _mask_pii(value: Any) -> str | None:
    """Mascara PII formatada num valor (net-pass). None permanece None.

    Ordem das regras é contrato — a mesma de `DEFAULT_MASKING_RULES`.
    """
    if value is None:
        return None
    s = str(value)
    for rule in _PII_RULES:
        s = rule["pattern"].sub(lambda m, r=rule: _mask_match(m.group(0), r), s)
    return s


# Trigger types understood by the webhook adapter
TriggerType = Literal["api", "webhook", "task", "scheduled", "yaml_auto"]


class WebhookAdapter(ChannelAdapter):
    """
    Channel-level singleton adapter for the 'webhook' channel (Arc 19).

    Exposes HTTP endpoints for triggering, resuming, and querying the status
    of webhook-based workflow sessions.  The adapter itself is stateless —
    all session state lives in the Core Redis stream and the routing engine.

    Args:
        producer:  Kafka producer for publishing normalised inbound events.
        redis:     Async Redis client.
        settings:  Gateway settings (env vars).
    """

    channel = "webhook"

    def __init__(
        self,
        producer: AIOKafkaProducer,
        redis:    aioredis.Redis,
        settings: Settings,
        db_pool:  Any = None,
    ) -> None:
        self._producer = producer
        self._redis    = redis
        self._settings = settings

        # Identity Resolver (Fase A) — co-located module. Redis index (Slice 1) +
        # optional PG durability (Slice 2, reuses the gateway's asyncpg pool).
        # Flag-gated so the legacy pending_workflow path is unaffected when off.
        # Salt is a SECRET → env only (PLUGHUB_IDENTITY_SALT); TTLs are tuning.
        self._identity_enabled = os.getenv("PLUGHUB_IDENTITY_RESOLVER_ENABLED", "true").lower() in ("1", "true", "yes")
        salt = os.getenv("PLUGHUB_IDENTITY_SALT", "plughub_identity_demo_salt")
        self._identity = IdentityIndex(
            redis=redis,
            salt=salt,
            prospect_ttl_s=int(os.getenv("PLUGHUB_IDENTITY_PROSPECT_TTL_S", "2592000")),
            resolution_index_ttl_s=int(os.getenv("PLUGHUB_IDENTITY_INDEX_TTL_S", "2592000")),
            db_pool=db_pool,
        )

        # OTP de posse de canal (Fase 2) — step-up componível, acionado pelo fluxo.
        # Entrega mockada no demo: PLUGHUB_OTP_DEV_RETURN_CODE=true loga+retorna o
        # código (default true no demo, DEVE ser false em produção).
        self._otp = OtpService(
            redis=redis,
            salt=salt,
            ttl_s=int(os.getenv("PLUGHUB_OTP_TTL_S", "300")),
            max_attempts=int(os.getenv("PLUGHUB_OTP_MAX_ATTEMPTS", "5")),
            rl_window_s=int(os.getenv("PLUGHUB_OTP_RL_WINDOW_S", "900")),
            rl_max=int(os.getenv("PLUGHUB_OTP_RL_MAX", "3")),
            code_digits=int(os.getenv("PLUGHUB_OTP_CODE_DIGITS", "6")),
            dev_return_code=os.getenv("PLUGHUB_OTP_DEV_RETURN_CODE", "true").lower() in ("1", "true", "yes"),
        )

    async def ensure_identity_schema(self) -> None:
        """Create the PG `identity` schema/tables (idempotent). Called at startup."""
        if self._identity_enabled:
            await self._identity.ensure_schema()

    # Valores que significam "não tem", mas chegam como TEXTO. Uma tag semeada por
    # `context_json` (string JSON com `{{...}}`) cujo ref não resolveu vira a string
    # literal "null"/"undefined" — que é truthy em Python e passaria por qualquer
    # `if not value`. Normalizar aqui, num ponto só, em vez de em cada leitor.
    _CTX_EMPTY = {"", "null", "none", "undefined"}

    async def _read_ctx_tag(
        self, tenant_id: str, session_id: str | None, tag: str
    ) -> str | None:
        """Read a single ContextStore tag of a session. Fail-soft → None."""
        if not session_id:
            return None
        try:
            raw = await self._redis.hget(f"{tenant_id}:ctx:{session_id}", tag)
            if not raw:
                return None
            entry = json.loads(raw)
            val = entry.get("value") if isinstance(entry, dict) else entry
            if val is None:
                return None
            text = str(val).strip()
            return text if text.lower() not in self._CTX_EMPTY else None
        except Exception:
            return None

    async def _read_ctx_root(self, tenant_id: str, session_id: str | None) -> str | None:
        """
        Journey J1: read `core.contact.root_session_id` from a session's ContextStore.

        Returns the raw value, or None when absent/unreadable. Used to inherit the
        caller's TRANSITIVE root when spawning a child session (trigger-from-session
        / delegate): child.root = caller.root, not caller.session_id. Fail-soft — a
        missing/broken entry falls back to the caller resolving root = self upstream.
        """
        return await self._read_ctx_tag(tenant_id, session_id, "core.contact.root_session_id")

    async def _resolve_signal_target(
        self,
        tenant_id:   str,
        session_id:  str,          # caller (workflow) session
        caller_root: str,
        grain:       str,
    ) -> str:
        """
        S2 — traduz o GRÃO do sinal na CHAVE contra a qual ele será gravado.

        Isto NÃO é regra de negócio no core: é a definição de o que cada grão SIGNIFICA
        no modelo de sessão da plataforma (a mesma natureza de `root_session_id`). O que
        é regra de negócio — pesquisar a journey ou a sessão — fica no `config_json` do
        deploy; aqui só se resolve o que a plataforma já sabe:

          journey  → a raiz canônica da journey        (caller.core.contact.root_session_id)
          session  → a sessão de origem pesquisada     (caller.core.workflow.origin_session_id,
                     i.e. a sessão que disparou o workflow de survey)
          workflow → o próprio workflow                (a sessão chamadora)
          segment  → a sessão QUE CONTÉM o segmento    (também a de origem — o
                     `segment_id` viaja à parte, em `core.survey.segment_id`)

        Note que `segment` e `session` resolvem para a MESMA sessão-chave: o que os separa
        não é a chave, é o `segment_id` + `agent_key` que acompanham o sinal (é assim que
        `session_signal` modela atribuição por agente).
        """
        if grain == "journey":
            return caller_root
        if grain == "workflow":
            return session_id
        if grain in ("session", "segment"):
            origin = await self._read_ctx_tag(
                tenant_id, session_id, "core.workflow.origin_session_id"
            )
            if not origin:
                raise ValueError(
                    f"signal_grain='{grain}' exige que o workflow tenha uma sessão de "
                    "origem (core.workflow.origin_session_id) — este collect não foi disparado "
                    "a partir de uma sessão."
                )
            return origin
        raise ValueError(f"signal_grain desconhecido: '{grain}'")

    @staticmethod
    def _ctx_entry(value: str, source: str, now_iso: str) -> str:
        """JSON-encode a ContextStore entry (confidence 1.0, agents_only)."""
        return json.dumps({
            "value":      value,
            "confidence": 1.0,
            "source":     source,
            "visibility": "agents_only",
            "updated_at": now_iso,
        })

    # ──────────────────────────────────────────────────────────────────────────
    # Trigger — create a new webhook session
    # ──────────────────────────────────────────────────────────────────────────

    async def handle_trigger(
        self,
        skill_id:          str,
        tenant_id:         str,
        trigger_type:      TriggerType = "api",
        metadata:          dict[str, Any] | None = None,
        customer_id:       str | None = None,
        origin_session_id: str | None = None,
        root_session_id:   str | None = None,
        context:           dict[str, Any] | None = None,
        pool_id:           str | None = None,
        journey:           str = "inherit",
    ) -> str:
        """
        Create a new webhook session.

        **S4 — o endereço canônico é o POOL** (`pool_id`); `skill_id` é legado.

        pool_id: quando set, o routing engine atribui este pool DIRETO, e o pool roda o
          skill do seu slot `current` COM O CONFIG daquele slot. O endereço fica estável
          entre versões do skill, e não há ambiguidade quando o MESMO skill está deployado
          em N pools com configs diferentes (o desenho do survey: um `skill_survey_
          outbound_v1` em três pools, um por grão). `skill_id` pode vir vazio aqui.

        skill_id (LEGADO): funciona como "DNIS" do canal webhook — o router o casa contra
          o `webhook_skill_id` de cada pool. **Só é endereço enquanto UM pool o declara**;
          com N pools o router rejeita (não escolhe por score, não misroteia em silêncio).

        The customer_id is the "ANI" — optional, defaults to a generated UUID
        when the trigger is not customer-initiated (e.g. scheduled, api).

        origin_session_id: Arc 19 — session that triggered this workflow
          (e.g. a webchat intake session). Written to ContextStore as
          core.workflow.origin_session_id so agents can trace the provenance.

        context: Arc 19 — seed ContextStore entries for the new session.
          Dict of {tag: value} pairs (string values). Written atomically
          before the routing engine allocates an instance, so the skill-flow
          can read them from step 1 via @ctx.* resolution.
          Example: {"session.numero_atual": "11999999999"}

        journey (T3): "inherit" (default) — a sessão entra na journey do chamador;
          "new" — ela inicia a PRÓPRIA journey (raiz = ela mesma), mantendo
          `origin_session_id` apontando para o pai. Proveniência atravessa a fronteira;
          pertença não. Use quando o cliente pediu algo SEM RELAÇÃO com o processo atual.

        Returns the new session_id.
        """
        session_id  = str(uuid.uuid4())
        customer_id = customer_id or f"sys:{trigger_type}:{uuid.uuid4().hex[:8]}"

        # ── T3: PROVENIÊNCIA ≠ PERTENÇA ─────────────────────────────────────────
        #
        # `origin_session_id` (quem me criou) e `root_session_id` (de que processo faço
        # parte) eram a MESMA aresta: a raiz era herdada INCONDICIONALMENTE do chamador.
        # Mas nem toda filha continua o processo do pai — se o cliente pede algo SEM
        # RELAÇÃO no meio de um atendimento, o processo novo era engolido pela journey do
        # antigo, e não havia como dizer "isto é outra coisa".
        #
        # `journey="new"` corta a PERTENÇA (a sessão nasce como sua própria raiz) e
        # PRESERVA a PROVENIÊNCIA (`origin_session_id` segue apontando para o pai, logo
        # abaixo, no evento). Resultado: duas journeys — e o fio que as liga.
        #
        # Simétrico ao `journey_merge`: `new` corta no nascimento, `merge` une depois.
        # Não há split retroativo (união não tem inverso) ⇒ na dúvida, corte.
        if journey == "new":
            resolved_root = session_id
        elif root_session_id:
            resolved_root = root_session_id
        elif origin_session_id:
            resolved_root = (
                await self._read_ctx_root(tenant_id, origin_session_id)
                or origin_session_id
            )
        else:
            resolved_root = session_id

        now_str = datetime.now(timezone.utc).isoformat()
        event = {
            "event_id":          str(uuid.uuid4()),
            "session_id":        session_id,
            "tenant_id":         tenant_id,
            "channel":           "webhook",
            # When pool_id is set, the routing engine assigns it directly and runs
            # the pool's DEPLOYED skill (stable address). Desde a Fase C do ADR
            # adr-webhook-endpoint-single-registry isso vale para as DUAS portas —
            # a slug externa (`/channel/webhook/{slug}`) e a interna
            # (`/v1/channels/webhook/{identifier}`) —, ambas resolvendo pelo MESMO
            # registro `ChannelEndpoint`. When None, routing ainda resolve pelo
            # `skill_id` (fallback por `webhook_skill_id`), que sai na Fase E.
            "pool_id":           pool_id,
            # Endereço discado. Enquanto `pool_id` é None ele é a CHAVE DE ROTEAMENTO
            # (fallback); com `pool_id` preenchido o router o ignora e ele sobrevive
            # como registro de qual endereço foi usado — o papel de DNIS (D5).
            "skill_id":          skill_id,
            "customer_id":       customer_id,
            "trigger_type":      trigger_type,
            "metadata":          metadata or {},
            "origin_session_id": origin_session_id,
            "root_session_id":   resolved_root,   # Journey J1
            # Journey T4: rótulo da aresta. Só quando HÁ aresta — uma sessão de topo
            # (sem chamador) não foi "criada" por ninguém: ela é a raiz da árvore.
            "spawn_reason":      "trigger" if origin_session_id else None,
            "timestamp":         now_str,
            # Arc 19: required by ConversationInboundEvent schema in routing-engine.
            # For webhook sessions there is no prior wait time — started_at == trigger time.
            "started_at":        now_str,
        }

        # ── Seed ContextStore before publishing to Kafka ─────────────────────
        # Writing context entries BEFORE routing ensures that when the routing
        # engine allocates a skill-flow instance and the first step runs, all
        # seeded tags are already available via @ctx.* resolution.
        #
        # context_entries format: {tag: value} — both strings.
        # origin_session_id is always written as core.workflow.origin_session_id
        # (confidence 1.0, visibility agents_only) when provided.
        ctx_key   = f"{tenant_id}:ctx:{session_id}"
        now_iso   = datetime.now(timezone.utc).isoformat()
        ctx_writes: dict[str, str] = {}

        # Journey J1: root is never null — always seed core.contact.root_session_id so
        # any child spawned from THIS session inherits the transitive root.
        ctx_writes["core.contact.root_session_id"] = self._ctx_entry(
            resolved_root, "webhook_trigger", now_iso
        )

        if origin_session_id:
            ctx_writes["core.workflow.origin_session_id"] = json.dumps({
                "value":      origin_session_id,
                "confidence": 1.0,
                "source":     "webhook_trigger",
                "visibility": "agents_only",
                "updated_at": now_iso,
            })
            # Journey T4: rótulo da aresta no ctx — o bridge o relê para carimbar a linha
            # de close (a sobrevivente no ReplacingMergeTree). Só existe quando há aresta.
            ctx_writes["core.contact.spawn_reason"] = self._ctx_entry(
                "trigger", "webhook_trigger", now_iso,
            )

        for tag, value in (context or {}).items():
            ctx_writes[tag] = json.dumps({
                "value":      str(value),
                "confidence": 1.0,
                "source":     "webhook_trigger",
                "visibility": "agents_only",
                "updated_at": now_iso,
            })

        if ctx_writes:
            await self._redis.hset(ctx_key, mapping=ctx_writes)
            # TTL 24h — extended by skill-flow-engine suspend executor if needed
            await self._redis.expire(ctx_key, 86_400)

        # ── session:{id}:meta — a sessão precisa saber de que TENANT ela é ───────
        #
        # Escrito aqui desde 2026-08-18, e o motivo é um defeito medido ao vivo. Esta
        # porta criava sessão SEM `meta`; toda outra origem escreve (o canal na conexão,
        # o `delegate`/`collect` no caminho interno). Quem lê a chave trata a ausência
        # como "use o default", e um dos leitores é `conversation_escalate`, que
        # inventava `tenant_id="default"`: o contato escalado ia parar num namespace
        # sem instância nenhuma (`default:pool:…:queue`) e morria em silêncio DEPOIS de
        # o cliente ser avisado da transferência. Reproduzido em `8b3e2b27`, com
        # `escalated_human` no segmento e nenhum segmento `primary`.
        #
        # A recusa do lado do escalate (`tenant_unknown`) fecha a perda silenciosa; esta
        # escrita fecha a AUSÊNCIA que a provoca. As duas são necessárias e nenhuma
        # substitui a outra: a primeira impede inventar, a segunda faz haver o que ler.
        #
        # Escrito ANTES do publish pelo mesmo argumento do ContextStore acima: quando o
        # routing alocar e o primeiro step rodar, a chave já tem de existir.
        #
        # `pool_id` fica FORA de propósito — ele é reescrito pelo bridge na alocação
        # (`process_routed`), e semeá-lo aqui gravaria o pool de ENTRADA num campo que
        # os leitores tomam por "pool que está atendendo".
        try:
            _meta = {
                "tenant_id":  tenant_id,
                "channel":    "webhook",
                "contact_id": customer_id or session_id,
            }
            if customer_id:
                _meta["customer_id"] = customer_id
            await self._redis.setex(
                f"session:{session_id}:meta", 86_400, json.dumps(_meta),
            )
        except Exception as exc:
            # Degradação nunca silenciosa: sem esta chave a escalação desta sessão será
            # RECUSADA lá na frente (`tenant_unknown`), e sem esta linha o operador veria
            # a recusa sem a causa.
            logger.error(
                "webhook trigger: session:%s:meta NÃO escrito (%s: %s) — uma escalação "
                "desta sessão será recusada por tenant desconhecido",
                session_id, type(exc).__name__, exc,
            )

        await self._publish(event, topic="conversations.inbound")

        logger.info(
            "webhook trigger: session=%s skill=%s trigger_type=%s tenant=%s "
            "origin=%s ctx_tags=%d",
            session_id, skill_id, trigger_type, tenant_id,
            origin_session_id or "-", len(ctx_writes),
        )
        return session_id

    # ──────────────────────────────────────────────────────────────────────────
    # Resume — wake a suspended session
    # ──────────────────────────────────────────────────────────────────────────

    async def _routing_work_task_holder(
        self, tenant_id: str, pool_id: str, session_id: str,
    ) -> dict | None:
        """
        A5 — consulta a POSSE do item no ÁRBITRO (routing HTTP API), em vez de ler o
        Redis do routing direto (invariante do árbitro único).

        Devolve o veredicto INTEIRO do árbitro
        (`{found, instance_id?, claimant_user_id?, claimed_at?, via, in_queue}`), e não
        só o holder: `found=False, in_queue=True` é uma resposta positiva ("ninguém
        detém, está na fila") de que o chamador precisa para recusar. A versão anterior
        colapsava esse caso em `None` — indistinguível de falha de rede — e por isso o
        check só sabia falhar aberto.

        `None` fica reservado ao que é de fato desconhecido: pool ausente, árbitro não
        configurado, HTTP não-200 ou falha de rede. Só aí o chamador degrada para
        permissivo, e com log.
        """
        if not pool_id:
            return None
        import httpx
        base = (getattr(self._settings, "routing_engine_url", "") or "").rstrip("/")
        if not base:
            logger.warning(
                "A5: routing_engine_url não configurada — posse do item %s NÃO "
                "conferida (submit degrada para permissivo)", session_id,
            )
            return None
        headers: dict[str, str] = {}
        tok = getattr(self._settings, "routing_admin_token", "")
        if tok:
            headers["X-Admin-Token"] = tok
        try:
            async with httpx.AsyncClient(timeout=5) as c:
                r = await c.post(
                    f"{base}/v1/work_queue/holder",
                    json={"tenant_id": tenant_id, "pool_id": pool_id, "session_id": session_id},
                    headers=headers,
                )
            if r.status_code != 200:
                logger.warning("A5 work_task holder lookup HTTP %s", r.status_code)
                return None
            data = r.json()
            return data if isinstance(data, dict) else None
        except Exception as exc:
            logger.warning("A5 work_task holder lookup failed: %s", exc)
            return None

    async def _routing_work_task_expire(
        self, tenant_id: str, pool_id: str, session_id: str, reason: str,
    ) -> dict | None:
        """
        I5 — pede ao ÁRBITRO que encerre o item de trabalho (ZREM + lease + vaga).
        Mesmo caminho de confiança do `_routing_work_task_holder`: o gateway solicita, o
        routing decide. Falha de rede degrada para None **com log** — o resume segue
        (o workflow não pode ficar preso porque a limpeza falhou), mas o motivo
        aparece; sem isso o item ficaria pendurado sem nenhum registro do porquê.
        """
        if not pool_id:
            return None
        import httpx
        base = (getattr(self._settings, "routing_engine_url", "") or "").rstrip("/")
        if not base:
            logger.warning(
                "work_task_expire: routing_engine_url não configurada — item %s do "
                "pool %s não foi encerrado", session_id, pool_id,
            )
            return None
        headers: dict[str, str] = {}
        tok = getattr(self._settings, "routing_admin_token", "")
        if tok:
            headers["X-Admin-Token"] = tok
        try:
            async with httpx.AsyncClient(timeout=5) as c:
                r = await c.post(
                    f"{base}/v1/work_queue/expire",
                    json={
                        "tenant_id":  tenant_id,
                        "pool_id":    pool_id,
                        "session_id": session_id,
                        "reason":     reason,
                    },
                    headers=headers,
                )
            if r.status_code != 200:
                logger.warning(
                    "work_task_expire: HTTP %s (session=%s pool=%s)",
                    r.status_code, session_id, pool_id,
                )
                return None
            return r.json()
        except Exception as exc:
            logger.warning(
                "work_task_expire: falhou (session=%s pool=%s): %s",
                session_id, pool_id, exc,
            )
            return None

    async def _write_approval_decision(
        self,
        tenant_id:         str,
        session_id:        str,
        payload:           dict[str, Any],
        approver:          dict[str, Any] | None,
        claim_instance_id: str | None,
    ) -> None:
        """
        A5.4 — grava a DECISÃO de aprovação como evento `message` (agents_only) na stream
        canônica, com o bloco ApprovalDecisionMeta no payload. Autor = o aprovador
        (internal/possessed) ou a credencial externa (system/claimed). Valores de `edits`
        mascarados (net-pass). Best-effort: falha não interrompe o resume.
        """
        now_iso = datetime.now(timezone.utc).isoformat()

        if approver:
            principal_type = str(approver.get("principal_type", "human"))
            decided_by     = str(approver.get("decided_by", ""))
            verification   = str(approver.get("verification_class", "possessed"))
            author_id      = claim_instance_id or (f"human-{decided_by}" if decided_by else "approver")
            author_role    = "specialist"
        else:
            principal_type = "system"
            decided_by     = str(payload.get("source") or "external")
            verification   = "claimed"
            author_id      = "webhook_adapter"
            author_role    = "system"

        # threshold_in_force — declarado pelo passo de aprovação do workflow (A5.5, via
        # ctx tag session.approval_threshold). Default SEGURO = possessed na ausência.
        threshold = await self._read_ctx_tag(
            tenant_id, session_id, "session.approval_threshold"
        ) or "possessed"
        deploy_version = await self._read_ctx_tag(
            tenant_id, session_id, "session.deploy_version"
        )
        # Escopo do segmento do aprovador (single-source). Ausente → "" (v1); o bridge
        # pode carimbar session.approval_segment_id ao anexar o humano (refinamento).
        segment_id = await self._read_ctx_tag(
            tenant_id, session_id, "session.approval_segment_id"
        ) or ""

        raw_edits = payload.get("field_edits") or []
        edits: list[dict[str, Any]] = []
        for e in raw_edits:
            if isinstance(e, dict):
                edits.append({
                    "field":  str(e.get("field", "")),
                    "before": _mask_pii(e.get("before")),
                    "after":  _mask_pii(e.get("after")),
                })

        attachments = payload.get("attachments_viewed")
        attachments = [str(a) for a in attachments] if isinstance(attachments, list) else []
        choice = str(payload.get("choice") or "")

        approval_block: dict[str, Any] = {
            "choice":             choice,
            "edits":              edits,
            "principal_type":     principal_type,
            "decided_by":         decided_by,
            "verification_class": verification,
            "threshold_in_force": threshold,
            "attachments_viewed": attachments,
            "decided_at":         now_iso,
        }
        if deploy_version:
            approval_block["deploy_version"] = deploy_version

        message_payload = {
            "content":           {"type": "text", "text": f"Aprovação: {choice}", "metadata": {}},
            "masked":            True,
            "masked_categories": [],
            "approval":          approval_block,
        }

        try:
            await self._redis.xadd(
                f"session:{session_id}:stream",
                {
                    "event_id":    str(uuid.uuid4()),
                    "type":        "message",
                    "timestamp":   now_iso,
                    "author_id":   author_id,
                    "author_role": author_role,
                    "visibility":  json.dumps("agents_only"),
                    "segment_id":  segment_id,
                    "payload":     json.dumps(message_payload),
                },
                maxlen=1000,
            )
            logger.info(
                "A5.4: approval decision recorded session=%s choice=%s principal=%s class=%s",
                session_id, choice, principal_type, verification,
            )
        except Exception as exc:
            logger.warning("A5.4: could not write approval decision message: %s", exc)

    async def resume_required_abac(
        self, tenant_id: str, resume_token: str
    ) -> tuple[str, str] | None:
        """
        Camada E2 (gate por tipo de tarefa): resolve, SERVER-SIDE, qual capacidade
        ABAC a submissão deste resume exige — a partir do contexto da workflow
        suspensa (autoritativo; o autor do workflow declarou no `delegate.context`),
        NUNCA de valor client-asserted.

        Precedência:
          1. `session.resume_abac` = "modulo.campo" (ex.: "approvals.decide") →
             (modulo, campo). É a declaração explícita do autor.
          2. `session.decisions` presente (marcador de APROVAÇÃO, retrocompat) e sem
             `resume_abac` → ("approvals", "decide") — preserva o gate das aprovações
             existentes sem exigir que elas passem a declarar o campo.
          3. Nada → None = **form-fill genérico** (ex.: wrap-up): o binding do claim
             (instance==human-{sub} + caller==claimant) já autoriza; sem ABAC extra.

        Fail-soft: token desconhecido/erro → None (o handle_resume trata 404; e o
        default seguro aqui é não exigir aprovação sobre um form-fill genérico —
        o claim continua sendo verificado).
        """
        try:
            token_value = await self._redis.hget(
                f"{tenant_id}:resume_tokens", resume_token
            )
            if not token_value:
                return None
            session_id = token_value.split(":", 2)[0]
            explicit = await self._read_ctx_tag(tenant_id, session_id, "session.resume_abac")
            if explicit and "." in explicit:
                mod, _, field = explicit.partition(".")
                mod, field = mod.strip(), field.strip()
                if mod and field:
                    return (mod, field)
            # Retrocompat: aprovação sem resume_abac explícito é reconhecida pelo
            # marcador `session.decisions` (mesmo sinal que o ApprovalPanel usa).
            if await self._read_ctx_tag(tenant_id, session_id, "session.decisions"):
                return ("approvals", "decide")
            return None
        except Exception:
            return None

    async def handle_resume(
        self,
        resume_token:  str,
        tenant_id:     str,
        payload:       dict[str, Any] | None = None,
        resume_origin: str = "token",
        approver:          dict[str, Any] | None = None,
        claim_pool_id:     str | None = None,
        claim_instance_id: str | None = None,
    ) -> str | None:
        """
        Resolve a resume_token to a session_id and publish a session_resumed event.

        The resume_token was generated by the skill-flow-engine suspend executor
        and stored in Redis hash {tenant_id}:resume_tokens.

        resume_origin (Identity Resolver nível b §11) tags how the resume was
        triggered: "token" (explicit resume_token — webhook endpoint / timeout
        scanner, the default), "same_channel" (intra-channel reconnect) or
        "identity" (cross-channel Lookup-2 offer). Slice 3 wires only "token";
        the reconnect-offer origins land with the Fase B resume path.

        Returns session_id on success, None if the token is unknown/expired.

        Fase F (D7): levanta `ResumeAlreadyTerminalError` (→ 409) quando outro
        gatilho já encerrou este resume, ou o está encerrando agora. Ver o bloco
        de comentário do topo do módulo para por que a exclusão é um lock e não
        um HDEL antecipado.
        """
        # Fase E.3: garante uma fonte de resume (resumed_by). Quem entra aqui sem
        # source é o resume externo (curl/operador/API); o tool workflow_resume marca
        # "agent" e o timeout scanner marca "timeout_scanner".
        payload = dict(payload or {})
        payload.setdefault("source", "external")

        actor        = _resume_actor(payload, approver)
        inflight_key = _resume_inflight_key(tenant_id, resume_token)

        # ── Fase F — EXCLUSÃO MÚTUA, antes de qualquer await que possa ceder ──
        # `SET NX` é uma operação atômica única: exatamente um chamador recebe
        # verdadeiro. Não usa Lua de propósito — o valor do token continua
        # intocado até o consumo, então a retentabilidade e o 403 do A5 seguem
        # como estavam.
        try:
            won = await self._redis.set(
                inflight_key, actor, nx=True, ex=_RESUME_INFLIGHT_TTL_S,
            )
        except Exception as exc:
            # O lock é a única linha de defesa da unicidade, mas uma falha de
            # Redis não pode recusar resume legítimo. Degrada permissivo E
            # BARULHENTO — degradação silenciosa aqui reapareceria como duplo
            # encerramento sem nada vermelho.
            logger.warning(
                "Fase F: lock de resume indisponível (token=%s tenant=%s): %s — "
                "seguindo SEM exclusão mútua",
                resume_token, tenant_id, exc,
            )
            won = True

        if not won:
            holder = ""
            try:
                _raw = await self._redis.get(inflight_key)
                # Decodifica defensivamente: o cliente pode ou não vir com
                # `decode_responses`, e sem isto o 409 sairia com
                # `closed_by: "b'agent'"` — um nome que não é nome. Mesmo cuidado
                # que o `read_work_task` já toma neste arquivo.
                holder = (_raw if isinstance(_raw, str) else _raw.decode()) if _raw else ""
            except Exception:
                pass
            logger.warning(
                "Fase F 409: resume do token=%s (tenant=%s) recusado — outro "
                "encerramento EM CURSO por %s; chamador=%s",
                resume_token, tenant_id, holder or "?", actor,
            )
            raise ResumeAlreadyTerminalError(state="in_flight", by=holder or "?")

        try:
            return await self._handle_resume_locked(
                resume_token      = resume_token,
                tenant_id         = tenant_id,
                payload           = payload,
                actor             = actor,
                resume_origin     = resume_origin,
                approver          = approver,
                claim_pool_id     = claim_pool_id,
                claim_instance_id = claim_instance_id,
            )
        finally:
            # Solto SEMPRE, inclusive no 403 do A5 e em falha no meio: segurar o
            # lock por 45 s depois de uma recusa transformaria uma recusa legítima
            # em indisponibilidade temporária do item.
            try:
                await self._redis.delete(inflight_key)
            except Exception:
                pass

    async def _read_resume_terminal(
        self, tenant_id: str, resume_token: str,
    ) -> dict[str, Any] | None:
        """Registro terminal do token, se houver. Ausência ≠ erro."""
        try:
            raw = await self._redis.get(_resume_terminal_key(tenant_id, resume_token))
            return json.loads(raw) if raw else None
        except Exception:
            return None

    async def _write_resume_terminal(
        self,
        tenant_id:    str,
        resume_token: str,
        session_id:   str,
        step_id:      str,
        actor:        str,
        cause:        str,
    ) -> None:
        """
        Grava QUEM encerrou e POR QUÊ, para o próximo a chegar poder ser recusado
        **com nome**. Sem isto, depois que o token some o perdedor volta a receber
        "token não encontrado" — a recusa muda sem que a causa apareça.

        TTL 25 h: o prazo do item é ≤ 24 h, então a janela cobre todo o período em
        que alguém ainda poderia plausivelmente clicar em enviar.
        """
        try:
            await self._redis.set(
                _resume_terminal_key(tenant_id, resume_token),
                json.dumps({
                    "session_id": session_id,
                    "step_id":    step_id,
                    "by":         actor,
                    "cause":      cause,
                    "at":         datetime.now(timezone.utc).isoformat(),
                }),
                ex=_RESUME_TERMINAL_TTL_S,
            )
        except Exception as exc:
            # Best-effort: perder o registro custa o NOME da recusa, não a
            # unicidade (que é do lock + do consumo do token). Mas nunca calado.
            logger.warning(
                "Fase F: não gravou o registro terminal (token=%s): %s — a próxima "
                "recusa vai sair sem causa",
                resume_token, exc,
            )

    # ──────────────────────────────────────────────────────────────────────────
    # Registro POR TOKEN — Fase 1 do arco de workflow. Ver `_resume_meta_key`.

    async def _write_resume_meta(
        self,
        tenant_id:      str,
        resume_token:   str,
        session_id:     str,
        step_id:        str,
        expires_at:     str,
        suspend_reason: str,
        ttl_s:          int,
    ) -> None:
        """Grava o registro do token com TTL do PRÓPRIO token. Best-effort e barulhento."""
        try:
            await self._redis.set(
                _resume_meta_key(tenant_id, resume_token),
                json.dumps({
                    "session_id":     session_id,
                    "step_id":        step_id,
                    "expires_at":     expires_at,
                    "suspend_reason": suspend_reason,
                    "opened_at":      datetime.now(timezone.utc).isoformat(),
                }),
                ex=max(ttl_s, 60),
            )
        except Exception as exc:
            # Perder o meta custa o fallback de prazo e o substrato da transição —
            # não custa o resume, que segue pelo hash. Nunca calado: sem este log,
            # a Fase 2 nasceria com `suspend_reason` vazio sem explicação.
            logger.warning(
                "resume_meta: não gravou o registro do token=%s (tenant=%s): %s",
                resume_token, tenant_id, exc,
            )

    async def _read_resume_meta(
        self, tenant_id: str, resume_token: str,
    ) -> dict[str, Any] | None:
        """`get`, nunca `getdel` — o consumo é explícito, no fim do resume."""
        try:
            raw = await self._redis.get(_resume_meta_key(tenant_id, resume_token))
            return json.loads(raw) if raw else None
        except Exception:
            return None

    async def _extend_hash_ttl(self, hash_key: str, ttl_s: int) -> None:
        """
        `EXPIRE` que só ESTENDE, nunca encurta.

        O `EXPIRE` cru que os escritores faziam é o defeito: sendo o hash
        compartilhado pelo tenant inteiro, um token curto escrito depois derrubava
        o prazo de um token longo escrito antes. Compara com o TTL corrente e só
        escreve se for maior. Não é atômico — duas escritas simultâneas podem
        intercalar —, mas o pior caso passa a ser *não estender*, e não *encurtar*:
        a direção do erro é a segura, e o `resume_meta` cobre o resto.

        ⚠️ **`-1` significa "existe, SEM expiração" — e aqui isso é BOOTSTRAP, não
        "infinito a preservar".** A v1 desta função tratava `-1` como valor a nunca
        tocar, raciocinando *"sem expiração é mais longo que qualquer TTL, logo
        defini-lo seria encurtar"*. Só que uma chave recém-criada pelo `HSET` nasce
        com `-1`: o efeito real foi que o hash **nunca mais recebia TTL nenhum** e
        passava a viver para sempre, com todo token órfão dentro. Eu não consertei o
        encurtamento — troquei-o por *não expira nunca*, que é pior por ser
        silencioso: nada fica vermelho quando uma chave apenas deixa de morrer.
        Medido pelo P6 do `gate_external_resume.sh` em 2026-08-10 (`TTL = -1`), e só
        porque aquele passo lê o TTL antes de julgar.

        Nenhum escritor deste hash quer chave perpétua ⇒ `-1` **define**.
        """
        try:
            current = await self._redis.ttl(hash_key)
            if current is None:
                return
            # -2 = chave inexistente → EXPIRE é no-op inócuo.
            # -1 = existe sem TTL   → bootstrap: DEFINE (ver docstring).
            #  n = existe com TTL   → só estende.
            if current == -1 or current < ttl_s:
                await self._redis.expire(hash_key, ttl_s)
        except Exception:
            pass   # non-fatal — hash compartilhado entre sessões

    async def _handle_resume_locked(
        self,
        resume_token:  str,
        tenant_id:     str,
        payload:       dict[str, Any],
        actor:         str,
        resume_origin: str = "token",
        approver:          dict[str, Any] | None = None,
        claim_pool_id:     str | None = None,
        claim_instance_id: str | None = None,
    ) -> str | None:
        """
        Corpo do resume, já sob o lock da Fase F. Separado de `handle_resume` só
        para que a exclusão mútua tenha um `finally` que não dependa de nenhum
        `return` do corpo — não há mudança de comportamento nesta divisão.
        """
        hash_key    = f"{tenant_id}:resume_tokens"
        token_value = await self._redis.hget(hash_key, resume_token)

        if not token_value:
            # Fase F — token ausente tem DUAS causas, e até aqui elas saíam com a
            # mesma resposta. Se há registro terminal, o item foi encerrado (o
            # caso do supervisor que encerra enquanto o agente preenche): 409 com
            # a causa. Sem registro, é ausência honesta: 404 como antes.
            terminal = await self._read_resume_terminal(tenant_id, resume_token)
            if terminal:
                logger.warning(
                    "Fase F 409: resume do token=%s (tenant=%s) recusado — já "
                    "encerrado por %s (%s) em %s; chamador=%s",
                    resume_token, tenant_id, terminal.get("by"),
                    terminal.get("cause"), terminal.get("at"), actor,
                )
                raise ResumeAlreadyTerminalError(
                    state      = "terminal",
                    session_id = str(terminal.get("session_id") or ""),
                    by         = str(terminal.get("by") or "?"),
                    cause      = str(terminal.get("cause") or "?"),
                    at         = str(terminal.get("at") or ""),
                )
            # ── Fase 1 — fallback pelo registro POR TOKEN ────────────────────
            # Chega aqui quem NÃO foi consumido (o terminal acima já respondeu por
            # esses). Sobram dois casos, e até agora saíam iguais: token que de
            # fato venceu, e token vivo cujo HASH COMPARTILHADO expirou por causa
            # de outra sessão. O segundo é o defeito do TTL de hash — o resume era
            # recusado com 404 enquanto o prazo do próprio token ainda corria.
            #
            # ⚠️ A ORDEM importa: o terminal vem ANTES. O meta sobrevive ao
            # consumo (é apagado junto, mas best-effort), e ressuscitar token
            # consumido reabriria a porta que a Camada F fechou.
            meta = await self._read_resume_meta(tenant_id, resume_token)
            if meta and meta.get("session_id"):
                still_valid = True
                try:
                    still_valid = datetime.fromisoformat(
                        str(meta.get("expires_at") or "")
                    ) > datetime.now(timezone.utc)
                except Exception:
                    # Sem prazo legível, o TTL da própria chave já é o limite —
                    # ela estar aqui significa que não venceu.
                    still_valid = True
                if still_valid:
                    token_value = (
                        f"{meta['session_id']}:{meta.get('step_id') or ''}"
                        f":{meta.get('expires_at') or ''}"
                    )
                    # REINSERE no hash: sem isto o token volta a ser invisível ao
                    # scanner de prazo, que varre `*:resume_tokens`. Um token que
                    # resume mas nunca pode expirar seria um vazamento novo,
                    # trocado por um defeito velho.
                    try:
                        await self._redis.hset(hash_key, resume_token, token_value)
                        await self._extend_hash_ttl(hash_key, _RESUME_META_BUFFER_S)
                    except Exception:
                        pass
                    logger.warning(
                        "resume_meta: token=%s (tenant=%s) REIDRATADO — a entrada do "
                        "hash compartilhado tinha sumido, mas o prazo do token "
                        "(%s) ainda corre. Sintoma do TTL de hash: outra sessão "
                        "encurtou `resume_tokens`. Resume segue.",
                        resume_token, tenant_id, meta.get("expires_at"),
                    )
                else:
                    logger.warning(
                        "resume_meta: token=%s (tenant=%s) VENCIDO de fato em %s",
                        resume_token, tenant_id, meta.get("expires_at"),
                    )
                    return None
            else:
                logger.warning(
                    "webhook resume: unknown or expired token=%s tenant=%s",
                    resume_token, tenant_id,
                )
                return None

        # token_value format: "{session_id}:{step_id}:{expires_at_iso}"
        parts = token_value.split(":", 2)
        if len(parts) < 2:
            logger.error(
                "webhook resume: malformed token value=%r token=%s",
                token_value, resume_token,
            )
            return None

        session_id = parts[0]
        step_id    = parts[1]

        # ── A5 / Fase A (D6) — o submit confere POSSE contra o árbitro ──────────
        # A instância que submete precisa DETER o item. Leitura via o ÁRBITRO (nunca o
        # Redis do routing direto), que responde lease → registro durável → `in_queue`.
        #
        # QUATRO ramos, e a distinção entre os dois últimos é o ponto da Fase A. Até
        # aqui havia dois: "detido por outro" (403) e "tudo o mais" (passa), e esse
        # "tudo o mais" misturava *ninguém detém* com *não sei* — o primeiro é motivo
        # de recusa, o segundo não. Depois de um re-parque (F5 do Console: WS cai, o
        # bridge re-rota, o item volta ao ZSET) a lease some, e o antigo fail-open
        # deixava a ABA VELHA submeter sobre trabalho que já estava de volta na fila,
        # disponível para outro agente. Ver ADR § D6 e § 1.
        if approver is not None and claim_instance_id:
            holder = await self._routing_work_task_holder(
                tenant_id, claim_pool_id or "", session_id,
            )
            if holder is None:
                # (4) árbitro inalcançável / pool ausente = DESCONHECIDO. Degrada para
                # permissivo — recusar submissão legítima por causa de uma falha de
                # rede é pior — mas nunca em silêncio.
                logger.warning(
                    "A5: posse NÃO conferida para session=%s (árbitro sem resposta); "
                    "submit liberado pelo binding instance==human-{sub}", session_id,
                )
            else:
                held_by = holder.get("instance_id")
                if held_by and held_by != claim_instance_id:
                    # (2) forja / segundo dono — o caso que o A5 já cobria.
                    logger.warning(
                        "A5 403: session=%s detida por %s (via %s), submit veio de %s",
                        session_id, held_by, holder.get("via"), claim_instance_id,
                    )
                    raise PermissionError(
                        "resume: caller does not hold the claim on this session"
                    )
                if not held_by and holder.get("in_queue"):
                    # (3) NINGUÉM detém e o item está na FILA. Resposta positiva, não
                    # ausência: o claim é um ZREM, então membro do ZSET é item sem dono.
                    # Submeter aqui é submeter sobre trabalho disponível a outro agente.
                    logger.warning(
                        "A5 403: session=%s está NA FILA do pool %s (ninguém detém) — "
                        "submit de %s recusado; reivindique o item antes de submeter",
                        session_id, claim_pool_id or "?", claim_instance_id,
                    )
                    raise PermissionError(
                        "resume: work item is back in the queue — claim it before submitting"
                    )
                if not held_by:
                    # (4') sem posse e fora da fila: pool push, item já encerrado, ou
                    # claim anterior à Fase A (sem registro durável). Ausência honesta.
                    logger.info(
                        "A5: sem posse registrada para session=%s e item fora da fila "
                        "(push / encerrado / claim pré-Fase A) — submit liberado pelo "
                        "binding instance==human-{sub}", session_id,
                    )
                else:
                    logger.info(
                        "A5 OK: session=%s detida por %s (via %s)",
                        session_id, held_by, holder.get("via"),
                    )

        # A5.5 — expõe a classe de confiança do AUTOR no payload do resume, para o
        # `choice` do workflow gatear ($.pipeline_state.<delegate>.verification_class
        # contra o limiar declarado no passo). Só em resumes de aprovação; sem approver
        # (externo/sistema) → claimed. O limiar (session.approval_threshold) é convenção
        # de autoria no context do delegate; o default seguro (possessed) mora no A5.4.
        if approver is not None or isinstance(payload.get("field_edits"), list):
            payload.setdefault(
                "verification_class",
                approver.get("verification_class", "possessed") if approver else "claimed",
            )
            payload.setdefault(
                "principal_type",
                approver.get("principal_type", "human") if approver else "system",
            )

        now_iso = datetime.now(timezone.utc).isoformat()

        # ── Arc 19 Fase B: write session_resumed to canonical stream ─────────
        # Published BEFORE the conversations.inbound event so that consumers
        # (analytics-api, Monitor) see the transition before re-allocation fires.
        try:
            await self._redis.xadd(
                f"session:{session_id}:stream",
                {
                    "event_id":    str(uuid.uuid4()),
                    "type":        "session_resumed",
                    "timestamp":   now_iso,
                    "author_id":   "webhook_adapter",
                    "author_role": "system",
                    "visibility":  json.dumps("agents_only"),
                    "segment_id":  "",
                    "payload":     json.dumps({
                        "step_id":       step_id,
                        "resume_token":  resume_token,
                        "resume_origin": resume_origin,
                        "payload":       payload or {},
                    }),
                },
                maxlen=500,
            )
            # Restore status key to "active" so get_status() reflects the transition.
            # keepttl=True preserves the existing TTL (set by persistSuspendWebhook in Fase C).
            await self._redis.set(
                f"{tenant_id}:session:{session_id}:status",
                "active",
                keepttl=True,
            )
        except Exception as _exc:
            # Non-fatal: stream write failure must not block the resume flow.
            logger.warning(
                "Could not write session_resumed to stream: session=%s — %s",
                session_id, _exc,
            )

        # ── A5.4 — audita a DECISÃO de aprovação como `message` agents_only ──────
        # Só quando o resume carrega uma decisão de aprovação (field_edits presente —
        # enviado pelo ApprovalPanel). Autor = aprovador (possessed) ou credencial
        # externa (claimed). Best-effort: não interrompe o resume.
        if isinstance(payload.get("field_edits"), list):
            await self._write_approval_decision(
                tenant_id, session_id, payload, approver, claim_instance_id,
            )

        # Resolve the session's REAL channel + pool — a resume must NOT redefine them.
        # A webchat session being resumed (e.g. Session A-new's delegate step) must
        # stay "webchat"; only genuine webhook workflows stay "webhook". Likewise the
        # pool_id must be preserved: parse_inbound writes pool_id from this event, so
        # omitting it makes the ReplacingMergeTree overwrite the sessions row's pool
        # with '' on every resume.
        resume_channel = "webhook"
        resume_pool    = ""
        try:
            raw_meta = await self._redis.get(f"session:{session_id}:meta")
            if raw_meta:
                _meta_r       = json.loads(raw_meta)
                resume_channel = _meta_r.get("channel", "webhook") or "webhook"
                resume_pool    = _meta_r.get("pool_id", "") or ""
        except Exception:
            pass

        # Journey J1: a resume re-publishes conversations.inbound for the SAME session,
        # so parse_inbound would reset root to self. Preserve the session's own root
        # (read from its ContextStore; fallback self) to keep a resumed CHILD grouped.
        resume_root = await self._read_ctx_root(tenant_id, session_id) or session_id

        # Publish session_resumed to the canonical stream via conversations.inbound
        # The Core / orchestrator-bridge will handle re-allocation.
        event = {
            "event_id":     str(uuid.uuid4()),
            "session_id":   session_id,
            "tenant_id":    tenant_id,
            "channel":      resume_channel,
            "pool_id":      resume_pool,
            "event_type":    "session_resumed",
            "resume_token":  resume_token,
            "resume_origin": resume_origin,
            "step_id":       step_id,
            "payload":       payload or {},
            "root_session_id": resume_root,   # Journey J1: preserve on re-open
            "timestamp":     now_iso,
            # Arc 19: required by ConversationInboundEvent schema in routing-engine.
            # On resume, elapsed_ms could reflect the suspend duration, but the
            # routing engine does not use it for webhook re-allocation. Use now_iso.
            "started_at":   now_iso,
        }

        # ── I5 — encerra o item de trabalho que este delegate parqueou ───────────
        # Roda em TODO resume (submit, timeout, supervisor): é UM caminho, idempotente,
        # que faz só o que restou. Ramo exclusivo do caso raro apodrece sem ser exercitado.
        #
        # A ORDEM é load-bearing, presa entre dois vizinhos:
        #   · DEPOIS do check A5 (caller==claimant), que lê a lease que este bloco apaga;
        #   · ANTES do publish, porque um flow pode RE-DELEGAR no on_timeout — publicar
        #     primeiro criaria um item NOVO que esta limpeza (tardia) apagaria, e o
        #     sintoma seria uma tarefa que some da inbox sem ninguém a ter tocado.
        _wt = await self.read_work_task(tenant_id, session_id)
        if _wt:
            # Fase F — a expressão inline que morava aqui virou `_terminal_cause`,
            # porque o registro terminal precisa da MESMA causa. Duas cópias da
            # regra divergiriam, e a divergência sairia como um encerramento
            # gravado com um nome e recusado com outro.
            _reason = _terminal_cause(payload)
            await self._routing_work_task_expire(
                tenant_id  = tenant_id,
                pool_id    = str(_wt.get("pool_id") or ""),
                # o id que está DE FATO no ZSET (== session_id em conferência)
                session_id = str(_wt.get("queue_session_id") or session_id),
                reason     = _reason,
            )
            try:
                await self._redis.delete(self.work_task_key(tenant_id, session_id))
            except Exception:
                pass
        else:
            # Ausência é esperada em resume de `suspend` puro (sem delegate) — mas é
            # também o sintoma de um ledger perdido. Logar em debug mantém o rastro
            # sem poluir o caminho normal.
            logger.debug(
                "work_task: nenhum item parqueado para session=%s (resume de suspend?)",
                session_id,
            )

        await self._publish(event, topic="conversations.inbound")

        # Fase F — o registro terminal é escrito ANTES do consumo, e a ordem é
        # load-bearing: entre o HDEL e a escrita haveria um instante em que o
        # token já não existe e a causa ainda não existe, e um resume caindo ali
        # levaria o 404 antigo — a recusa sem nome que esta fase remove.
        await self._write_resume_terminal(
            tenant_id, resume_token, session_id, step_id, actor,
            _terminal_cause(payload),
        )

        # ── D4 / Fase 2 — FECHA a transição, com a linha INTEIRA ────────────────
        # Evento analítico próprio em `conversations.events` — irmão do
        # `session_suspended` que o bridge publica. NÃO é o `session_resumed` do
        # `conversations.inbound` logo acima: aquele é o comando que faz o routing
        # realocar, e o consumer de analytics o lê por outro parser. Dois eventos com
        # o mesmo nome em tópicos diferentes é o preço de não sobrecarregar o caminho
        # de execução com carga analítica; o comentário existe para que o próximo
        # leitor não conclua que um deles é duplicata.
        #
        # ⚠️ Lido do `resume_meta` AQUI, antes do `HDEL` e do delete do meta: é o que
        # torna esta linha COMPLETA e satisfaz o invariante do ReplacingMergeTree
        # (substitui a linha inteira). Sem isto o resume apagaria `suspend_reason` da
        # linha sobrevivente — e ele não tem outra fonte viva no sistema.
        try:
            _tr_meta = await self._read_resume_meta(tenant_id, resume_token) or {}
            if not _tr_meta:
                logger.warning(
                    "transição: resume_meta AUSENTE no fechamento (token=%s session=%s) "
                    "— a linha fecha sem motivo nem início reais",
                    resume_token, session_id,
                )
            await self._publish(
                {
                    "event_type":        "session_resumed",
                    "session_id":        session_id,
                    "tenant_id":         tenant_id,
                    "timestamp":         now_iso,
                    "resume_token":      resume_token,
                    "step_id":           step_id,
                    "resume_origin":     resume_origin,
                    "pool_id":           resume_pool,
                    "suspend_reason":    _tr_meta.get("suspend_reason") or "",
                    "suspended_at":      _tr_meta.get("opened_at") or "",
                    "resume_expires_at": _tr_meta.get("expires_at") or None,
                    # `expired` quando quem retomou foi o prazo, não uma resposta —
                    # a mesma distinção que `_terminal_cause` faz, na moeda da lacuna.
                    "outcome": (
                        "expired"
                        if _terminal_cause(payload) in ("acw_expired", "acw_supervisor_closed")
                        else "resumed"
                    ),
                },
                topic=self._settings.kafka_topic_events,
            )
        except Exception as _tr_exc:
            # Best-effort: perder a linha analítica não pode desfazer um resume que
            # já aconteceu. Mas nunca calado — transição faltando vira lacuna sem
            # explicação num relatório que promete explicá-la.
            logger.warning(
                "transição: não publicou o fechamento (token=%s): %s",
                resume_token, _tr_exc,
            )

        # Clean up the token after successful resume (one-shot)
        await self._redis.hdel(hash_key, resume_token)
        # O registro POR TOKEN morre junto. Best-effort: se sobrar, quem chegar
        # depois é barrado pelo registro TERMINAL, que é checado antes do
        # fallback — sobra vira lixo com TTL, nunca token ressuscitado.
        try:
            await self._redis.delete(_resume_meta_key(tenant_id, resume_token))
        except Exception:
            pass

        logger.info(
            "webhook resume: session=%s step=%s token=%s tenant=%s",
            session_id, step_id, resume_token, tenant_id,
        )
        return session_id

    # ──────────────────────────────────────────────────────────────────────────
    # Status query
    # ──────────────────────────────────────────────────────────────────────────

    async def get_status(
        self,
        session_id: str,
        tenant_id:  str,
    ) -> dict[str, str]:
        """
        Return the current status of a webhook session.

        Reads the session status from the Redis stream metadata key written by
        Core.  Falls back to "closed" when the key has expired (TTL elapsed).

        Returns { "session_id": ..., "status": "active"|"suspended"|"closed" }.
        """
        # Core writes session status at: {tenant_id}:session:{session_id}:status
        # (a simple Redis string, TTL same as the stream)
        status_key = f"{tenant_id}:session:{session_id}:status"
        status     = await self._redis.get(status_key)

        if status is None:
            # Key expired → session is closed (or never existed)
            status = "closed"

        return {"session_id": session_id, "status": status}

    # ──────────────────────────────────────────────────────────────────────────
    # ChannelAdapter interface — no-ops for webhook channel
    # ──────────────────────────────────────────────────────────────────────────

    async def deliver_text(self, payload: dict) -> None:
        """
        Webhook workflows do not deliver messages to external channels.
        Outbound notifications are handled by agent-skill task steps.
        """

    async def deliver_menu(self, payload: dict) -> None:
        """
        Menu steps are forbidden in workflow profile (Arc 19 segregation).
        No-op in case an event leaks through.
        """

    async def deliver_typing(self, payload: dict) -> None:
        """Typing indicators have no meaning in the webhook channel."""

    async def deliver_session_closed(self, payload: dict) -> None:
        """
        Session closure for webhook sessions is managed by the
        orchestrator-bridge (via complete() step → agent_done → session_closed).
        No external party needs to be notified here.
        """

    # ──────────────────────────────────────────────────────────────────────────
    # Delegate — create a child session in a normal (non-webhook) pool
    # ──────────────────────────────────────────────────────────────────────────

    async def handle_delegate(
        self,
        tenant_id:          str,
        pool_id:            str,
        customer_id:        str,
        origin_session_id:  str,
        resume_token:       str,
        context:            dict[str, str],
        timeout_hours:      float,
        customer_resumable: bool = False,
        resume_policy:      str  = "offer",
    ) -> str:
        """
        Create a child session in a specific (non-webhook) pool for delegate I/O.

        Called by the skill-flow-service persistDelegate callback when a workflow
        delegate step executes for the first time.

        Responsibilities:
          1. Generate child_session_id
          2. Write workflow_resume_token + context entries to child ContextStore
          3. Publish conversations.inbound with pool_id set directly
             (routing engine uses pool_id as direct assignment)
          4. Return child_session_id

        The context keys are written as "session.{key}" in ContextStore so the
        child session's agent can read them via @ctx.session.{key}.
        workflow_resume_token is always written as core.workflow.resume_token.
        origin_session_id is always written as core.workflow.origin_session_id.

        Args:
            tenant_id:         tenant identifier
            pool_id:           target pool for the child session (direct assignment)
            customer_id:       customer identifier (same as parent sessions)
            origin_session_id: root session (Session A) — star topology
            resume_token:      token for the agent to resume the parent workflow
            context:           key→value pairs to seed in child ContextStore
                               (keys WITHOUT "session." prefix — added automatically)
            timeout_hours:     child session TTL hint (+1h buffer added)
        Returns the new child_session_id.
        """
        child_session_id = str(uuid.uuid4())
        now_iso = datetime.now(timezone.utc).isoformat()

        # Journey J1: child inherits the caller's TRANSITIVE root (not origin, which
        # is 1-hop). Read the caller's core.contact.root_session_id; fallback = origin
        # (caller treated as its own root when it predates J1 / has no entry).
        caller_root = await self._read_ctx_root(tenant_id, origin_session_id) or origin_session_id

        # ── Seed ContextStore before publishing to Kafka ─────────────────────
        ctx_key = f"{tenant_id}:ctx:{child_session_id}"
        ctx_writes: dict[str, str] = {}

        # Journey J1: seed child root so a grandchild delegate inherits it too.
        ctx_writes["core.contact.root_session_id"] = self._ctx_entry(
            caller_root, "delegate_step", now_iso
        )

        # Always write workflow_resume_token so the agent can call workflow_resume
        ctx_writes["core.workflow.resume_token"] = json.dumps({
            "value":      resume_token,
            "confidence": 1.0,
            "source":     "delegate_step",
            "visibility": "agents_only",
            "updated_at": now_iso,
        })

        # Always write origin_session_id (star topology root)
        ctx_writes["core.workflow.origin_session_id"] = json.dumps({
            "value":      origin_session_id,
            "confidence": 1.0,
            "source":     "delegate_step",
            "visibility": "agents_only",
            "updated_at": now_iso,
        })
        # Journey T4: rótulo da aresta — este filho existe porque um workflow delegou I/O.
        ctx_writes["core.contact.spawn_reason"] = self._ctx_entry(
            "delegate", "delegate_step", now_iso,
        )

        # Write caller-provided context entries with session. prefix
        for key, value in context.items():
            # Avoid double-prefix if caller already used "session." prefix
            store_key = key if key.startswith("session.") else f"session.{key}"
            ctx_writes[store_key] = json.dumps({
                "value":      str(value),
                "confidence": 1.0,
                "source":     "delegate_step",
                "visibility": "agents_only",
                "updated_at": now_iso,
            })

        ttl_s = int(timeout_hours * 3600) + 3600  # +1h buffer
        if ctx_writes:
            await self._redis.hset(ctx_key, mapping=ctx_writes)
            await self._redis.expire(ctx_key, ttl_s)

        # ── Publish conversations.inbound with direct pool_id ─────────────────
        # pool_id set directly → routing engine assigns to this pool without
        # skill_id resolution. channel=webchat so pool agents can handle it.
        event = {
            "event_id":          str(uuid.uuid4()),
            "session_id":        child_session_id,
            "tenant_id":         tenant_id,
            "channel":           "webchat",     # delegate sessions use webchat
            "pool_id":           pool_id,        # direct pool assignment
            "skill_id":          None,           # not a webhook pool
            "customer_id":       customer_id,
            "trigger_type":      "delegate",
            "metadata":          {},
            "origin_session_id": origin_session_id,
            "root_session_id":   caller_root,   # Journey J1: transitive root
            "spawn_reason":      "delegate",    # Journey T4: rótulo da aresta
            "timestamp":         now_iso,
            "started_at":        now_iso,
            # I5 — prazo do item (mesmo motivo do caminho de conferência): o TTL do
            # JSON na fila acompanha o deadline em vez do default de 4 h.
            "work_item_deadline": (
                datetime.now(timezone.utc) + timedelta(hours=timeout_hours)
            ).isoformat(),
        }
        await self._publish(event, topic="conversations.inbound")

        logger.info(
            "webhook delegate: child_session=%s pool=%s origin=%s customer=%s tenant=%s ctx_tags=%d",
            child_session_id, pool_id, origin_session_id, customer_id, tenant_id, len(ctx_writes),
        )

        # ── I5 — ledger do item de trabalho ───────────────────────────────────
        # Aqui o item vai ao ZSET sob o id da FILHA, mas o ledger é chaveado pela
        # sessão que o RESUME resolve — que NÃO é o `origin_session_id` recebido
        # (esse é a RAIZ da topologia estrela; num delegate aninhado a raiz e o
        # chamador direto divergem). A associação exata token→sessão já existe em
        # `{t}:resume_tokens`, escrita pelo persistSuspendWebhook ANTES deste
        # dispatch (delegate.ts: passo 1 antes do passo 3). Lê-se de lá em vez de
        # inferir. Este caminho está inerte hoje (todo delegate roda como
        # conferência); o ledger o cobre para que voltar a usá-lo não reabra a I5.
        try:
            _tok_val = await self._redis.hget(f"{tenant_id}:resume_tokens", resume_token)
            _tok_val = (
                _tok_val if isinstance(_tok_val, str)
                else (_tok_val.decode() if _tok_val else "")
            )
            _tok_parts = _tok_val.split(":", 2) if _tok_val else []
            if len(_tok_parts) >= 2:
                await self._write_work_task(
                    tenant_id        = tenant_id,
                    session_id       = _tok_parts[0],
                    queue_session_id = child_session_id,
                    pool_id          = pool_id,
                    resume_token     = resume_token,
                    step_id          = _tok_parts[1],
                    assigned_to      = "",
                    deadline         = _tok_parts[2] if len(_tok_parts) > 2 else "",
                    ttl_s            = ttl_s,
                )
            else:
                logger.warning(
                    "work_task: delegate sem token em resume_tokens (token=%s child=%s) — "
                    "item de trabalho ficará sem caminho de expiração",
                    resume_token, child_session_id,
                )
        except Exception as _e:
            logger.warning("work_task: ledger do delegate roteado falhou: %s", _e)

        # ── Pending workflow lookup key (customer reconnect detection) ─────────
        # When the customer reconnects and their intake agent collects
        # contact_identifier, the agent calls pending_workflow_get which reads
        # this key to find the active resume_token without scanning the full
        # ContextStore.  Key deleted by get_pending_workflow when token is consumed.
        contact_id = context.get("contact_identifier") or context.get("session.contact_identifier")
        if contact_id:
            pending_key   = f"{tenant_id}:pending_workflow:{contact_id}"
            pending_value = json.dumps({
                "resume_token":     resume_token,
                "child_session_id": child_session_id,
                "pool":             pool_id,          # ← pool to delegate to on reconnect
                "context":          dict(context),
            })
            try:
                await self._redis.set(pending_key, pending_value, ex=ttl_s)
                logger.debug(
                    "webhook delegate: pending_workflow key written contact=%s session=%s",
                    contact_id, child_session_id,
                )
            except Exception as _e:
                logger.warning("webhook delegate: could not write pending_workflow key: %s", _e)

        # ── Identity Resolver dual-write (Fase A · Slice 1 + Slice 3 gate) ────
        # Generalize the pending lookup: resolve/provision a native customer_id
        # from the context anchors and register the pending under it, so a
        # reconnect from ANOTHER channel (different handle resolving to the same
        # customer) finds it. Additive to the legacy contact_id key above.
        # Slice 3: gated on customer_resumable — cross-channel indexing is a
        # per-delegation decision declared in the flow (spec §6), not automatic.
        # resume_policy is carried on the PendingEntry so the reconnect flow
        # (Fase B) knows whether to offer or auto-resume.
        if self._identity_enabled and customer_resumable:
            try:
                anchors = self._anchors_from_context(context)
                if anchors:
                    ref = await self._identity.resolve_or_provision(tenant_id, anchors, provision=True)
                    if ref.customer_id:
                        await self._identity.write_pending(
                            tenant_id, ref.customer_id,
                            PendingEntry(
                                session_id=origin_session_id,
                                customer_id=ref.customer_id,
                                resume_token=resume_token,
                                pool=pool_id,
                                skill_id=context.get("skill_id"),
                                intent=context.get("intent"),
                                policy=resume_policy,
                                context_preview=self._pending_context_preview(context),
                                root_session_id=caller_root,   # Journey J3
                            ),
                            ttl_s=ttl_s,
                        )
                        # Concrete trigger (§5): a registered pending must survive the
                        # ephemeral window → promote the customer to the durable PG store.
                        await self._identity.promote_to_durable(tenant_id, ref.customer_id, anchors)
                        logger.info(
                            "identity: pending_by_customer written customer=%s session=%s matched_by=%s",
                            ref.customer_id, origin_session_id, ref.matched_by,
                        )
            except Exception as _e:
                logger.warning("identity: dual-write failed (non-fatal): %s", _e)

        return child_session_id

    # ── Journey J4c — collect handler (N2 resolver + routed child session) ────
    async def _reachable_channels(
        self, tenant_id: str, customer_id: str,
    ) -> list[str]:
        """
        Reachability slot (N2 input). Which channels can the platform reach this
        customer on? v1 = best-effort empty (the "web" survey surface needs no
        address and is added by the caller as universal fallback). Future: query
        the Identity Resolver secondary_keys (phone→sms, email→email). This is a
        cross-cutting fact — it never depends on which process is asking.
        """
        # TODO(J4c fase 2): consult Identity Resolver reachable keys.
        return []

    async def _negotiate_channel(
        self,
        tenant_id:      str,
        customer_id:    str,
        channel_policy: dict[str, Any] | None,
    ) -> tuple[str, str]:
        """
        Journey J4c — resolvedor N2. Devolve **(canal, pool)**.

        **CEGO AO PROCESSO**: nunca ramifica por `skill_id`/`campaign_id` nem por
        qualquer identidade de processo — repare que a assinatura sequer os recebe,
        então o invariante é estrutural, não uma convenção.

        Inputs, todos cross-cutting:
          - alcançabilidade (Resolvedor de Identidade — slot)
          - consentimento (slot — vazio v1)
          - política do tenant (slot — vazio v1)
          - o `channel_policy` DECLARATIVO, que é **config de negócio injetada no
            deploy** (`config_json` do slot → `$.config.*`), não conteúdo do skill.

        O mapa `channels` (canal → pool) é a peça central: suas CHAVES são os canais
        permitidos e seus VALORES, o pool que atende cada um. Antes o pool vinha de
        `ChannelEndpoint(channel, identifier="default")` — uma constante mágica no
        core, que ainda por cima só permitia UM pool de collect por canal. Quem atende
        varia por negócio: é config, não é problema do core.
        """
        policy    = channel_policy or {}
        channels  = policy.get("channels") or {}
        exclude   = set(policy.get("exclude") or [])
        preferred = policy.get("preferred_order") or []

        if not channels:
            raise ValueError(
                "collect sem `channel_policy.channels` (mapa canal→pool). Esse mapa é "
                "config de negócio e deve ser injetado no deploy do skill "
                "(config_json do slot → $.config.channel_policy)."
            )

        reachable = await self._reachable_channels(tenant_id, customer_id)

        # Candidatos = canais do mapa − exclude. Se soubermos a alcançabilidade do
        # cliente, ela filtra; `webchat` é o fallback universal (não exige endereço —
        # o próprio link tokenizado é o ponto de entrada).
        allowed = [c for c in channels.keys() if c not in exclude]
        if reachable:
            narrowed = [c for c in allowed if c in reachable or c == "webchat"]
            if narrowed:
                allowed = narrowed
        if not allowed:
            raise ValueError(
                f"nenhum canal elegível: mapa={list(channels)} exclude={sorted(exclude)} "
                f"alcançáveis={reachable}"
            )

        chosen = next((c for c in preferred if c in allowed), allowed[0])
        return chosen, channels[chosen]

    async def handle_collect(
        self,
        *,
        tenant_id:          str,
        session_id:         str,               # caller (N3 workflow) session
        customer_id:        str,
        step_id:            str,
        collect_token:      str,
        target:             dict[str, Any],
        interaction:        str,
        prompt:             str,
        channel:            str | None = None,
        requires:           list[str] | None = None,
        channel_policy:     dict[str, Any] | None = None,
        options:            list[dict[str, Any]] | None = None,
        fields:             list[dict[str, Any]] | None = None,
        dialog_form_id:     str = "",
        signal_grain:       str = "journey",
        timeout_hours:      float = 48.0,
        campaign_id:        str = "",
        # Identity Resolver (nível b) — gate the pending_by_customer dual-write.
        customer_resumable: bool = False,
        resume_policy:      str  = "offer",
    ) -> dict[str, Any]:
        """
        N2 handler for a `collect` step (Journey J4c) — LAZY. Delivers the survey
        invitation link and SUSPENDS; it does NOT create a session or allocate any
        resource. The child contact session is created only when the customer
        engages (opens the link) — see GET /survey/{collect_token} (J4c-3):

          1. Negotiate the channel (N2 resolver — process-agnostic).
          2. Resolve the survey POOL for that channel (ChannelEndpoint) — stored on
             the pending so the click can route the inbound to it.
          3. Store the collect pending ({tenant}:collect:{collect_token}) with the
             caller-workflow resume mapping (caller_session/step_id), the inherited
             transitive root, the survey pool, form_id and negotiated channel.
          4. Deliver the invitation link `/survey/{collect_token}` (mock/dev).
          5. Return send_at/expires_at → the workflow suspends. No click by the
             deadline → nothing was allocated (only a pending key that expires).

        On click, a STANDARD inbound is created (routed → tenant quota + pool
        max_concurrent_sessions + Core `sessions` metering enforced only for real
        engagements) and the dialog_runner renders the DialogForm live (customer
        present). N3 stays channel-agnostic (sets `channel_policy`, never a channel).
        Returns { send_at, expires_at, link }.
        """
        now_dt  = datetime.now(timezone.utc)
        now_iso = now_dt.isoformat()

        # Journey J1: the (future) child inherits the caller's TRANSITIVE root.
        caller_root = await self._read_ctx_root(tenant_id, session_id) or session_id

        # ── N2: negocia canal E pool a partir do mapa de negócio (config de deploy) ──
        # O `channel` fixo só existe para transporte realmente fixo (collect interno a
        # um sistema); para outbound-ao-cliente ele seria N3 escolhendo o canal.
        if channel:
            pool_from_map = ((channel_policy or {}).get("channels") or {}).get(channel)
            if not pool_from_map:
                raise ValueError(
                    f"`channel` fixo '{channel}' não está no mapa channel_policy.channels"
                )
            negotiated, pool_id = channel, pool_from_map
        else:
            negotiated, pool_id = await self._negotiate_channel(
                tenant_id, customer_id, channel_policy,
            )

        # ── S2: grão → CHAVE do sinal ─────────────────────────────────────────
        # Resolvido AQUI (e não no runner) porque a tradução grão→chave é semântica do
        # modelo de sessão, e só o chamador tem o contexto (raiz, sessão de origem). O
        # runner recebe o alvo pronto pelo ctx e continua 100% genérico — sem grão nem
        # métrica hardcoded. Falha alto em grão inválido/insuportável: gravar o sinal na
        # chave errada é pior do que não gravar (contamina o relatório em silêncio).
        signal_target_id = await self._resolve_signal_target(
            tenant_id, session_id, caller_root, signal_grain,
        )

        # Atribuição por segmento — quem ESCOLHE o segmento é o GATILHO (política mora no
        # skill; ele propaga a escolha via `context_json` do workflow_trigger). O core só
        # expõe os fatos (`core.contact.last_primary_segment_id` etc., escritos pelo bridge no
        # ctx pré-hook) e transporta a escolha. Default de produto = último segmento
        # primary, mas isso está no YAML do gatilho, não aqui.
        signal_segment_id = ""
        signal_agent_key  = ""
        if signal_grain == "segment":
            signal_segment_id = await self._read_ctx_tag(
                tenant_id, session_id, "core.survey.segment_id",
            ) or ""
            signal_agent_key = await self._read_ctx_tag(
                tenant_id, session_id, "core.survey.agent_key",
            ) or ""
            if not signal_segment_id:
                raise ValueError(
                    "signal_grain='segment' exige `core.survey.segment_id` no ctx do "
                    "workflow — o gatilho deve escolher o segmento e propagá-lo no "
                    "context_json do workflow_trigger (a escolha é política do skill)."
                )

        # ── Segurança Fase B (J4c) — pool da SESSÃO PESQUISADA ────────────────
        # O pool da resposta = o pool da sessão contra a qual o sinal é gravado
        # (signal_target_id), NÃO o pool de infra do survey (o runner roda no pool
        # de survey, não no atendimento pesquisado). Lê `core.pool.id` do ctx do
        # alvo (escrito pela Routing Engine no `_write_pool_context`). Congela no
        # pending → `handle_collect_engage` semeia `core.survey.pool_id` → o runner
        # o carimba no `survey_record`. Vazio (ex.: ctx do alvo expirado) = admin-only
        # (decisão C) — logado, nunca degrada em silêncio.
        signal_pool_id = await self._read_ctx_tag(
            tenant_id, signal_target_id, "core.pool.id"
        ) or ""
        if not signal_pool_id:
            logger.warning(
                "collect: pool da sessão pesquisada ausente no ctx (target=%s grain=%s) "
                "— a resposta nascerá SEM pool (admin-only). ctx do alvo expirado?",
                signal_target_id, signal_grain,
            )

        # ── LAZY: store the collect pending — NO session until the customer clicks ──
        ttl_s      = int(timeout_hours * 3600) + 3600
        expires_at = (now_dt + timedelta(hours=timeout_hours)).isoformat()
        await self._redis.set(
            f"{tenant_id}:collect:{collect_token}",
            json.dumps({
                "caller_session_id": session_id,     # N3 workflow to resume on completion
                "step_id":           step_id,
                "root_session_id":   caller_root,    # journey membership for the inbound
                "pool_id":           pool_id,        # survey pool for the click inbound
                "channel":           negotiated,
                "form_id":           dialog_form_id, # DialogForm the runner will render
                "signal_grain":      signal_grain,      # S2 — grão do sinal (config do deploy)
                "signal_target_id":  signal_target_id,  # S2 — chave já resolvida p/ o runner
                "signal_pool_id":    signal_pool_id,    # Segurança Fase B — pool da sessão pesquisada
                "signal_segment_id": signal_segment_id, # S3 — atribuição (grain=segment)
                "signal_agent_key":  signal_agent_key,  # S3 — atribuição (grain=segment)
                "customer_id":       customer_id,
                "tenant_id":         tenant_id,
                "status":            "pending",
                "created_at":        now_iso,
                "expires_at":        expires_at,
            }),
            ex=ttl_s,
        )

        # ── Resume: the collect_token DOUBLES AS the resume_token ─────────────
        # Reuses the existing webhook resume machinery end-to-end: handle_resume()
        # does HGET on {tenant}:resume_tokens → "{session_id}:{step_id}:{expires_at}"
        # and resumes the suspended caller with resumeContext{step_id, input, payload}.
        # So the survey runner just calls workflow_resume(collect_token, answers) at
        # the end — no new topic, no new consumer.
        await self._redis.hset(
            f"{tenant_id}:resume_tokens",
            collect_token,
            f"{session_id}:{step_id}:{expires_at}",
        )
        # Fase 1: só ESTENDE. O `expire` cru daqui é justamente o que encurtava o
        # prazo de tokens longos de outras sessões — o hash é do tenant inteiro.
        await self._extend_hash_ttl(f"{tenant_id}:resume_tokens", ttl_s)
        # Registro POR TOKEN — prazo próprio + substrato da transição (D4).
        await self._write_resume_meta(
            tenant_id, collect_token, session_id, step_id, expires_at,
            suspend_reason="input",   # collect = coleta de resposta do alvo
            ttl_s=ttl_s,
        )

        # ── Identity Resolver dual-write (Slice 3 gate) — simétrico ao delegate ───
        # Sem isto o `collect` é retomável SÓ pelo token opaco: o cliente que volta
        # por outro canal não encontra nada, e o processo fica esperando um clique
        # num link cuja entrega real é trilha não construída. Era o gate assimétrico
        # — `handle_delegate` e `handle_delegate_conference` honravam os campos, este
        # handler os descartava, e o modo de falha era sucesso pelo caminho antigo.
        #
        # Duas diferenças de FATO em relação ao delegate, não de estilo:
        #   • o resume_token é o próprio `collect_token` (já gravado no hash
        #     {t}:resume_tokens acima) — não há token à parte a inventar;
        #   • `pool` aqui é o pool NEGOCIADO pelo N2, i.e. quem atende o cliente
        #     neste pending; no delegate é o pool a quem delegar na reconexão.
        # `expires_at` é preenchido porque o collect o conhece (o delegate não o passa).
        if self._identity_enabled and customer_resumable:
            try:
                pending_customer_id, anchors = await self._resolve_pending_customer(
                    tenant_id, session_id, customer_id,
                )
                if not pending_customer_id:
                    # Nunca degradar mudo: sem cliente resolvido a pendência seria
                    # gravada sob uma chave que ninguém consulta — invisível para
                    # sempre, e indistinguível de "não havia o que gravar".
                    logger.warning(
                        "identity: collect customer_resumable=true mas nenhum cliente "
                        "resolvido (caller=%s customer_id=%r âncoras=%d) — pendência "
                        "NÃO indexada; a retomada cross-canal não vai funcionar",
                        session_id, customer_id, len(anchors),
                    )
                else:
                    await self._identity.write_pending(
                        tenant_id, pending_customer_id,
                        PendingEntry(
                            session_id=session_id,          # workflow chamador suspenso
                            customer_id=pending_customer_id,
                            resume_token=collect_token,     # o collect_token É o token
                            pool=pool_id,                   # pool negociado pelo N2
                            policy=resume_policy,
                            expires_at=expires_at,
                            root_session_id=caller_root,    # Journey J3
                        ),
                        ttl_s=ttl_s,
                    )
                    if anchors:
                        # Gatilho concreto (§5): pendência registrada tem de sobreviver
                        # à janela efêmera → promove o cliente ao store durável.
                        await self._identity.promote_to_durable(
                            tenant_id, pending_customer_id, anchors,
                        )
                    logger.info(
                        "identity: pending_by_customer written (collect) customer=%s "
                        "caller=%s token=%s policy=%s",
                        pending_customer_id, session_id, collect_token, resume_policy,
                    )
            except Exception as _e:
                logger.warning("identity: collect dual-write failed (non-fatal): %s", _e)

        # ── Deliver the invitation link (mock/dev). The collect_token IS the token. ──
        # TODO(J4c fase 2): real SMS/email delivery via the negotiated channel provider.
        link = f"/survey/{collect_token}"
        logger.info(
            "webhook collect (lazy): token=%s channel=%s pool=%s root=%s link=%s "
            "— suspended, no session/resource until click",
            collect_token, negotiated, pool_id, caller_root, link,
        )

        return {"send_at": now_iso, "expires_at": expires_at, "link": link}

    async def handle_collect_engage(
        self,
        *,
        tenant_id:          str,
        collect_token:      str,
        jwt_secret_default: str,
        session_ttl_s:      int = 4 * 3600,
    ) -> dict[str, Any] | None:
        """
        Journey J4c — the customer ENGAGED (opened the survey link). **This is the
        only place a session is created**: until now the collect was suspended with
        zero resource. The customer is present, so the survey is SYNCHRONOUS and the
        dialog_runner can render the DialogForm live (agent profile: `menu` works).

        Mechanism — reuses the whole existing webchat path, zero new adapter:
          • Pre-seed the session ContextStore. The analytics consumer's J1 root
            enrichment reads `core.contact.root_session_id` from the ctx (not the event),
            so seeding it BEFORE the inbound makes the session a journey member N1
            by construction. `collect_token` + `dialog_form_id` are read by the runner.
          • Mint a webchat JWT carrying `session_id` — the webchat adapter honours
            that claim, so the page connects as a NORMAL webchat client and the
            existing inbound → Routing (quota + max_concurrent_sessions) → Core
            (`sessions` metering) path runs untouched. Limits apply to real
            engagements only.

        Idempotent: re-opening the link reuses the same survey session.
        Returns { jwt, pool_id, session_id, form_id } or None if unknown/expired.
        """
        raw = await self._redis.get(f"{tenant_id}:collect:{collect_token}")
        if not raw:
            return None
        pending = json.loads(raw if isinstance(raw, str) else raw.decode())

        now_iso    = datetime.now(timezone.utc).isoformat()
        session_id = pending.get("survey_session_id") or ""

        if not session_id:
            session_id = str(uuid.uuid4())
            ctx_key    = f"{tenant_id}:ctx:{session_id}"
            ctx_writes: dict[str, str] = {
                # Journey J1: root BEFORE the inbound → consumer enrichment stamps it.
                "core.contact.root_session_id": self._ctx_entry(
                    pending.get("root_session_id") or session_id, "collect_engage", now_iso,
                ),
                "core.workflow.origin_session_id": json.dumps({
                    "value": pending.get("caller_session_id") or "", "confidence": 1.0,
                    "source": "collect_engage", "visibility": "agents_only",
                    "updated_at": now_iso,
                }),
                # Journey T4: rótulo da aresta. Esta sessão nasce pelo WEBCHAT (o cliente
                # abriu o link), e o adapter de webchat não sabe que ela veio de um
                # `collect` — então o rótulo tem de ser semeado AQUI, antes do inbound.
                # O bridge o relê do ctx para carimbar a linha de close.
                "core.contact.spawn_reason": self._ctx_entry("collect", "collect_engage", now_iso),
                # The runner resumes N3 with this at the end (workflow_resume).
                "core.workflow.resume_token": json.dumps({
                    "value": collect_token, "confidence": 1.0, "source": "collect_engage",
                    "visibility": "agents_only", "updated_at": now_iso,
                }),
                "session.collect_token": json.dumps({
                    "value": collect_token, "confidence": 1.0, "source": "collect_engage",
                    "visibility": "agents_only", "updated_at": now_iso,
                }),
            }
            if pending.get("form_id"):
                # Dialog primitive binding — the single generic runner reads this.
                ctx_writes["core.workflow.dialog_form_id"] = json.dumps({
                    "value": pending["form_id"], "confidence": 1.0,
                    "source": "collect_engage", "visibility": "agents_only",
                    "updated_at": now_iso,
                })
            # S2 — grão + chave do sinal, já resolvidos no handle_collect. O runner lê
            # ambos daqui: ele não sabe (nem precisa saber) que grão está pesquisando.
            # Retrocompat: pendings criados antes do S2 não têm os campos → default
            # journey / raiz, que é exatamente o que eles faziam hardcoded.
            ctx_writes["core.survey.grain"] = self._ctx_entry(
                pending.get("signal_grain") or "journey", "collect_engage", now_iso,
            )
            ctx_writes["core.survey.target_id"] = self._ctx_entry(
                pending.get("signal_target_id")
                or pending.get("root_session_id")
                or session_id,
                "collect_engage", now_iso,
            )
            # S3 — atribuição por segmento. Só existe quando grain=segment; nos outros
            # grãos a tag fica AUSENTE e a ref `@ctx.core.survey.segment_id` do runner
            # resolve para null — por isso `segment_id`/`agent_key` são `.nullish()` no
            # SurveyRecordInputSchema (o runner é um só para todos os grãos).
            if pending.get("signal_segment_id"):
                ctx_writes["core.survey.segment_id"] = self._ctx_entry(
                    pending["signal_segment_id"], "collect_engage", now_iso,
                )
            if pending.get("signal_agent_key"):
                ctx_writes["core.survey.agent_key"] = self._ctx_entry(
                    pending["signal_agent_key"], "collect_engage", now_iso,
                )
            # Segurança Fase B (J4c) — pool da sessão pesquisada (resolvido no
            # handle_collect). O runner o lê como @ctx.core.survey.pool_id e o passa
            # ao survey_record. Ausente (pending legado ou ctx do alvo expirado) → a tag
            # não existe → ref resolve null → pool vazio (admin-only, decisão C).
            if pending.get("signal_pool_id"):
                ctx_writes["core.survey.pool_id"] = self._ctx_entry(
                    pending["signal_pool_id"], "collect_engage", now_iso,
                )
            await self._redis.hset(ctx_key, mapping=ctx_writes)
            await self._redis.expire(ctx_key, session_ttl_s)

            pending["survey_session_id"] = session_id
            pending["status"]            = "engaged"
            pending["engaged_at"]        = now_iso
            try:
                await self._redis.set(
                    f"{tenant_id}:collect:{collect_token}",
                    json.dumps(pending), keepttl=True,
                )
            except TypeError:   # older redis-py without keepttl
                await self._redis.set(
                    f"{tenant_id}:collect:{collect_token}",
                    json.dumps(pending), ex=session_ttl_s,
                )
            logger.info(
                "collect engaged: token=%s survey_session=%s pool=%s root=%s "
                "— session created ONLY now (customer present)",
                collect_token, session_id, pending.get("pool_id"),
                pending.get("root_session_id"),
            )

        # ── Mint the webchat JWT (pre-binds session_id) ───────────────────────
        secret = jwt_secret_default
        try:
            per_tenant = await self._redis.get(f"{tenant_id}:config:webchat:jwt_secret")
            if per_tenant:
                secret = per_tenant if isinstance(per_tenant, str) else per_tenant.decode()
        except Exception:   # non-fatal — fall back to the default secret
            pass

        token = pyjwt.encode(
            {
                "sub":        pending.get("customer_id") or session_id,
                "session_id": session_id,
                "tenant_id":  tenant_id,
                "exp":        int(time.time()) + session_ttl_s,
            },
            secret,
            algorithm="HS256",
        )
        return {
            "jwt":        token if isinstance(token, str) else token.decode(),
            "pool_id":    pending.get("pool_id") or "",
            "session_id": session_id,
            "form_id":    pending.get("form_id") or "",
        }

    # Spec de preview usada quando o delegate NÃO declara `preview`. Preserva o
    # comportamento histórico (portabilidade) para os fluxos que já existiam.
    _LEGACY_PREVIEW_SPEC: dict[str, str] = {
        "operadora_destino": "plain",
        "numero_atual":      "last_4",
    }

    @staticmethod
    def _apply_preview_mask(value: str, mask: str) -> str | None:
        """
        Aplica UMA máscara de preview. Devolve None quando o campo não deve ser
        exibido — seja por `hidden`, seja por máscara desconhecida (o chamador
        loga esta segunda). Vocabulário espelha `masking.context_rules`.
        """
        if mask == "hidden":
            return None
        if mask == "plain":
            return value
        if mask in ("last_2", "last_4"):
            keep = 2 if mask == "last_2" else 4
            alnum = "".join(ch for ch in value if ch.isalnum())
            return ("***" + alnum[-keep:]) if len(alnum) >= keep else "***"
        if mask == "email_domain":
            local, sep, domain = value.partition("@")
            if not sep or not domain:
                return "***"
            return f"{local[:1]}***@{domain}" if local else f"***@{domain}"
        return None

    @staticmethod
    def _pending_context_preview(context: dict[str, Any]) -> dict[str, str]:
        """
        Build a minimal, PII-conscious preview for the pending entry — shown to
        the customer in the cross-channel reconnect offer.

        O delegate declara O QUE mostrar e COMO mascarar, num mapa JSON em
        `context["preview"]`:

            preview: '{"numero_cartao": "last_4", "limite_solicitado": "plain"}'

        Máscaras: plain | last_2 | last_4 | email_domain | hidden (omite).
        Sem `preview`, cai na spec legada (portabilidade) — fluxos anteriores a
        esta mudança seguem idênticos.

        Duas decisões de segurança, ambas deliberadas:
          * máscara DESCONHECIDA omite o campo E loga. Rebaixar para `plain` seria
            trocar um erro de configuração por um vazamento silencioso.
          * spec ilegível cai no legado e loga — nunca em "mostrar tudo".

        Chaves ausentes no contexto são simplesmente omitidas.
        """
        raw_spec = context.get("preview") or context.get("session.preview")
        spec: dict[str, str] = dict(WebhookAdapter._LEGACY_PREVIEW_SPEC)
        if raw_spec:
            try:
                parsed = json.loads(raw_spec) if isinstance(raw_spec, str) else raw_spec
                spec = {str(k): str(v) for k, v in dict(parsed).items()}
            except (ValueError, TypeError, AttributeError) as exc:
                logger.warning(
                    "pending preview: spec ilegível (%s) — caindo na spec legada. raw=%r",
                    exc, raw_spec,
                )

        preview: dict[str, str] = {}
        for key, mask in spec.items():
            value = context.get(key) or context.get(f"session.{key}")
            if value in (None, ""):
                continue
            masked = WebhookAdapter._apply_preview_mask(str(value), mask)
            if masked is None:
                if mask != "hidden":
                    logger.warning(
                        "pending preview: máscara desconhecida %r no campo %r — campo OMITIDO",
                        mask, key,
                    )
                continue
            preview[key] = masked
        return preview

    async def _ctx_flat(self, tenant_id: str, session_id: str) -> dict[str, Any]:
        """
        Achata o ContextStore de uma sessão em `{tag: valor}`, descartando entradas
        ilegíveis e os vazios normalizados (`_CTX_EMPTY`). Fail-soft → {}.

        Cada tag `caller.X` é ADITIVAMENTE espelhada como `X`, porque
        `_anchors_from_context` conhece as chaves nuas e o namespace `session.`, mas
        `caller.*` é justamente onde vive dado do cliente (CLAUDE.md § ContextStore).
        Espelhar é aditivo de propósito: descartar a âncora conhecida porque apareceu
        num namespace é o mesmo erro que o `contact_identifier` já cometeu uma vez.
        """
        try:
            raw = await self._redis.hgetall(f"{tenant_id}:ctx:{session_id}")
        except Exception:
            return {}
        flat: dict[str, Any] = {}
        for k, v in (raw or {}).items():
            tag = k.decode() if isinstance(k, bytes) else str(k)
            try:
                entry = json.loads(v)
                val   = entry.get("value") if isinstance(entry, dict) else entry
            except Exception:
                continue
            if val is None:
                continue
            text = str(val).strip()
            if text.lower() in self._CTX_EMPTY:
                continue
            flat[tag] = text
            if tag.startswith("caller."):
                flat.setdefault(tag[len("caller."):], text)
        return flat

    async def _resolve_pending_customer(
        self, tenant_id: str, session_id: str, customer_id: str,
    ) -> tuple[str, list[dict[str, str]]]:
        """
        Resolve sob QUAL cliente indexar a pendência de um `collect`, devolvendo
        `(customer_id, anchors)`. Âncoras vazias = nada a promover ao durável.

        O `collect` não tem o mapa `context` que o `delegate` declara, então a fonte
        de âncoras é o ContextStore do CHAMADOR — que é onde elas já estão.

        Ordem deliberada, do mais forte ao mais fraco:
          1. um `customer_id` NATIVO (`cus_…`) já é a resposta — resolver de novo só
             criaria a chance de provisionar um cliente novo e gravar a pendência sob
             um id que ninguém consulta;
          2. `caller.customer_id` do ctx (o nativo que o Slice 4 carimba);
          3. âncoras do ctx → `resolve_or_provision`.
        Nenhum dos três → ("", anchors), e o chamador LOGA. Devolver um id inventado
        seria trocar uma falha visível por uma pendência invisível.
        """
        ctx     = await self._ctx_flat(tenant_id, session_id)
        anchors = self._anchors_from_context(ctx)

        if customer_id.startswith("cus_"):
            return customer_id, anchors

        native = ctx.get("caller.customer_id") or ""
        if isinstance(native, str) and native.startswith("cus_"):
            return native, anchors

        if anchors:
            ref = await self._identity.resolve_or_provision(
                tenant_id, anchors, provision=True,
            )
            if ref.customer_id:
                return ref.customer_id, anchors

        return "", anchors

    @staticmethod
    def _anchors_from_context(context: dict[str, Any]) -> list[dict[str, str]]:
        """
        Extract identity anchors from a delegate context. Explicit typed hints
        (phone/email/cpf/princ) plus `contact_identifier`, tratado como telefone
        (o caso comum de intake).

        ⚠️ NAMESPACE COMPARTILHADO: o `delegate.context` serve a dois donos — é
        payload para a tela do aprovador E fonte de âncoras de identidade. Uma chave
        chamada `cpf` NÃO é um campo de exibição: é uma âncora.

        `contact_identifier` é ADITIVO, não fallback. Era fallback (`if not anchors`),
        e o modo de falha foi este: um delegate que passasse `cpf` como dado de tela
        perdia o telefone — a âncora mais forte e a única já conhecida — e o
        `resolve_or_provision` PROVISIONAVA um cliente novo (`matched_by=provisioned`)
        em vez de casar com o existente. A pendência era escrita sob um customer_id
        que ninguém consultava: gravada com sucesso, invisível para sempre.
        Descartar a âncora conhecida porque apareceu uma desconhecida é exatamente
        a troca errada.
        """
        anchors: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()

        def add(kind: str, value: Any) -> None:
            if not value:
                return
            key = (kind, str(value))
            if key in seen:
                return
            seen.add(key)
            anchors.append({"kind": kind, "value": str(value)})

        for kind in ("phone", "email", "cpf", "princ"):
            add(kind, context.get(kind) or context.get(f"session.{kind}"))
        add("phone", context.get("contact_identifier") or context.get("session.contact_identifier"))
        return anchors

    # ──────────────────────────────────────────────────────────────────────────
    # Identity Resolver — public methods for HTTP endpoints (Fase A · Slice 1)
    # ──────────────────────────────────────────────────────────────────────────

    async def resolve_customer(
        self, tenant_id: str, anchors: list[dict[str, str]], provision: bool = True,
    ) -> dict:
        """Lookup 1 — resolve/provision a customer_id from anchors."""
        ref = await self._identity.resolve_or_provision(tenant_id, anchors, provision=provision)
        return {
            "customer_id":        ref.customer_id,
            "status":             ref.status,
            "matched_by":         ref.matched_by,
            "confidence":         ref.confidence,
            "verification_class": ref.verification_class,
        }

    # ── OTP de posse de canal (Fase 2) ─────────────────────────────────────────

    async def otp_challenge(self, tenant_id: str, kind: str, value: str) -> dict:
        """Emite um desafio de posse para a âncora (kind, value)."""
        return await self._otp.challenge(tenant_id, kind, value)

    async def otp_verify(
        self, tenant_id: str, customer_id: str, kind: str, value: str, code: str,
    ) -> dict:
        """
        Confere o OTP. No sucesso, promove a âncora a `possessed` (única via de
        posse) e a torna durável no cadastro do `customer_id`. É o ponto onde
        "posse provada" vira "identidade confiável".
        """
        res = await self._otp.verify(tenant_id, kind, value, code)
        if res.get("verified") and customer_id:
            await self._identity.attach_anchor(
                tenant_id, customer_id, kind, value,
                verification_class="possessed", persist_durable=True,
            )
            res["verification_class"] = "possessed"
        return res

    async def attach_customer_key(
        self, tenant_id: str, customer_id: str, kind: str, value: str,
    ) -> dict:
        """
        Enriquecimento — anexa uma âncora ao cliente como `claimed` (não-verificada).
        `possessed` NUNCA sai daqui: exige OTP (invariante possessed ⟺ verificado).
        """
        ok = await self._identity.attach_anchor(
            tenant_id, customer_id, kind, value,
            verification_class="claimed", persist_durable=False,
        )
        return {"attached": ok, "verification_class": "claimed"}

    async def update_customer_attributes(
        self, tenant_id: str, customer_id: str, attributes: dict,
    ) -> dict:
        """Enriquecimento — merge de atributos mascarados/não-sensíveis no cadastro."""
        ok = await self._identity.update_attributes(tenant_id, customer_id, attributes)
        return {"updated": ok}

    async def search_customers(self, tenant_id: str, q: str, limit: int = 20) -> dict:
        """Busca manual de cadastro (C1a) — por customer_id exato ou nome."""
        results = await self._identity.search_customers(tenant_id, q, limit)
        return {"count": len(results), "results": results}

    async def get_customer(self, tenant_id: str, customer_id: str) -> dict | None:
        """Read puro de um cliente por id (cadastro §11). Usado pelo outbound (Fase 3b)
        para consultar `attributes.do_not_contact` (opt-out global)."""
        return await self._identity.get_customer(tenant_id, customer_id)

    async def find_pending_by_customer(self, tenant_id: str, customer_id: str) -> dict:
        """Lookup 2 — pending workflows for a resolved customer_id.

        Returns the full pendings[] plus, for reconnect ergonomics, a FLATTENED
        view of the first pending at the top level (found/resume_token/pool/
        context/policy) — shape-compatible with the legacy get_pending_workflow
        response so the intake flow reads `pendencia.resume_token` /
        `pendencia.context.*` / `pendencia.policy` without JSONPath array indexing.
        `context` is derived from the pending's context_preview (masked at write).
        """
        pendings = await self._identity.find_pending(tenant_id, customer_id)
        result: dict[str, Any] = {
            "found":       len(pendings) > 0,
            "count":       len(pendings),
            "customer_id": customer_id,
            "pendings": [
                {
                    "session_id":      p.session_id,
                    "resume_token":    p.resume_token,
                    "pool":            p.pool,
                    "skill_id":        p.skill_id,
                    "intent":          p.intent,
                    "policy":          p.policy,
                    "suspended_at":    p.suspended_at,
                    "context_preview": p.context_preview,
                    "root_session_id": p.root_session_id,   # Journey J3
                }
                for p in pendings
            ],
        }
        if pendings:
            first = pendings[0]
            result.update({
                "resume_token":    first.resume_token,
                "pool":            first.pool,
                "policy":          first.policy,
                "context":         first.context_preview,
                "root_session_id": first.root_session_id,   # Journey J3 (merge target)
            })
        return result

    # ──────────────────────────────────────────────────────────────────────────
    # Pending workflow lookup (customer reconnect)
    # ──────────────────────────────────────────────────────────────────────────

    async def get_pending_workflow(
        self,
        tenant_id:          str,
        contact_identifier: str,
    ) -> dict | None:
        """
        Look up whether a customer has an active pending workflow awaiting
        their confirmation.

        Returns a dict with resume_token, child_session_id, and context when
        a valid (unconsumed) pending workflow is found, or None otherwise.

        Validation: verifies that the resume_token still exists in the
        {tenant_id}:resume_tokens hash.  If the token was already consumed
        (workflow resumed by other means), cleans up the stale lookup key and
        returns None.
        """
        pending_key = f"{tenant_id}:pending_workflow:{contact_identifier}"
        raw = await self._redis.get(pending_key)
        if not raw:
            return None

        try:
            data = json.loads(raw)
        except Exception:
            await self._redis.delete(pending_key)
            return None

        resume_token = data.get("resume_token", "")
        if not resume_token:
            await self._redis.delete(pending_key)
            return None

        # Verify token is still in resume_tokens (not yet consumed)
        hash_key    = f"{tenant_id}:resume_tokens"
        token_entry = await self._redis.hget(hash_key, resume_token)
        if not token_entry:
            # Token was consumed — remove stale lookup key
            await self._redis.delete(pending_key)
            return None

        return {
            "resume_token":     resume_token,
            "child_session_id": data.get("child_session_id", ""),
            "pool":             data.get("pool", ""),
            "context":          data.get("context", {}),
        }

    # ──────────────────────────────────────────────────────────────────────────
    # Delegate-as-conference — specialist in an existing (agent) session
    # ──────────────────────────────────────────────────────────────────────────

    async def handle_delegate_conference(
        self,
        tenant_id:          str,
        pool_id:            str,
        session_id:         str,    # PARENT session — customer is connected here
        customer_id:        str,
        resume_token:       str,    # delegate step resume token (for parent session)
        step_id:            str = "",  # parent's delegate step id (for the resume_token value)
        context:            dict[str, str] = {},
        timeout_hours:      float = 1.0,
        customer_resumable: bool = False,
        resume_policy:      str  = "offer",
        assigned_to:              str = "",
        fallback_to_pool_after_s: int | None = None,
        auto_attend:              bool = False,
    ) -> str:
        """
        Create a conference specialist in an existing agent (webchat) session.

        Unlike handle_delegate (which creates an independent session), this
        routes an agent from pool_id INTO the existing parent session as a
        conference specialist. All messages from the specialist go directly
        to the parent session's stream — the customer stays on the same
        WebSocket connection.

        Used when the delegate step fires in a non-webhook (agent) session,
        e.g. the intake reconnect session (Session A-new). The specialist
        (agente_confirmacao) runs inline in Session A-new and sends its
        notify/menu messages there.

        Returns the parent session_id (conference runs inside it).
        """
        now_iso       = datetime.now(timezone.utc).isoformat()
        conference_id = str(uuid.uuid4())   # unique specialist invitation

        # ── Write context to parent ContextStore ─────────────────────────────
        # Specialist reads workflow_resume_token + context via @ctx.session.*
        ctx_key    = f"{tenant_id}:ctx:{session_id}"
        ctx_writes: dict[str, str] = {}

        # The delegate resume_token for the PARENT delegate step goes to
        # resume_tokens so the channel-gateway can resume Session A-new's
        # delegate step when the specialist finishes. Written with TTL matching
        # the timeout_hours budget.
        ttl_s   = int(timeout_hours * 3600) + 3600
        # expires_at é o DEADLINE real (now + timeout_hours), não a hora de criação.
        # O timeout scanner lê este campo; gravar now() fazia o token nascer "vencido"
        # e o scanner disparava o timeout no primeiro ciclo (~60s) em vez de honrar o
        # timeout_hours configurado.
        exp_at  = (datetime.now(timezone.utc) + timedelta(hours=timeout_hours)).isoformat()
        # The resume_token must carry the PARENT's REAL delegate step_id so that
        # handle_resume → engine resumeContext.step_id matches the suspended step.
        # (Using a literal "delegate_conference" here broke the resume — the engine
        # could not match the step and the parent never finalized.)
        try:
            hash_key    = f"{tenant_id}:resume_tokens"
            token_value = f"{session_id}:{step_id or 'delegate_conference'}:{exp_at}"
            await self._redis.hset(hash_key, resume_token, token_value)
            # Fase 1: só ESTENDE (ver `_extend_hash_ttl`) + registro por token.
            await self._extend_hash_ttl(hash_key, ttl_s)
            await self._write_resume_meta(
                tenant_id, resume_token, session_id,
                step_id or "delegate_conference", exp_at,
                suspend_reason="input",   # delegate = I/O delegada a um especialista
                ttl_s=ttl_s,
            )
        except Exception as _e:
            logger.warning("delegate_conference: could not write resume_token: %s", _e)

        # Write context entries so specialist reads @ctx.session.* correctly
        for key, value in context.items():
            store_key = key if key.startswith("session.") else f"session.{key}"
            ctx_writes[store_key] = json.dumps({
                "value":      str(value),
                "confidence": 1.0,
                "source":     "delegate_conference",
                "visibility": "agents_only",
                "updated_at": now_iso,
            })

        # Write Session A-new's delegate resume_token so the specialist can
        # call workflow_resume a second time to close Session A-new properly.
        ctx_writes["core.workflow.delegate_resume_token"] = json.dumps({
            "value":      resume_token,
            "confidence": 1.0,
            "source":     "delegate_conference",
            "visibility": "agents_only",
            "updated_at": now_iso,
        })

        if ctx_writes:
            await self._redis.hset(ctx_key, mapping=ctx_writes)
            await self._redis.expire(ctx_key, ttl_s)

        # ── Resolve the PARENT's real channel ─────────────────────────────────
        # A specialist invite must NOT redefine the parent's channel. A webhook
        # workflow (Session B) stays "webhook"; a webchat reconnect (Session A-new)
        # stays "webchat". Read it from the parent session meta (fallback webchat).
        parent_channel = "webchat"
        try:
            raw_meta = await self._redis.get(f"session:{session_id}:meta")
            if raw_meta:
                parent_channel = json.loads(raw_meta).get("channel", "webchat") or "webchat"
        except Exception:
            pass

        # ── Publish conversations.inbound as conference specialist ────────────
        # conference_id signals to routing engine + bridge that this is a
        # specialist joining an existing session (not a new contact).
        # Messages from the specialist go to session:{session_id}:stream.
        event = {
            "event_id":    str(uuid.uuid4()),
            "session_id":  session_id,       # PARENT — conference in this session
            "tenant_id":   tenant_id,
            "channel":     parent_channel,   # ← preserve parent channel (never flip)
            "pool_id":     pool_id,
            "conference_id": conference_id,  # ← specialist routing
            "customer_id": customer_id,
            "trigger_type": "delegate",
            "metadata":    {},
            "timestamp":   now_iso,
            "started_at":  now_iso,
        }
        # Camada B (pull direcionado / "ramal") — quando o pool-alvo é `dispatch_mode:
        # pull`, o routing parqueia este convite como work item; assigned_to/fallback
        # fluem ao contact_data (via ConversationInboundEvent) e o work_task_claim os
        # honra (dono OU idade ≥ fallback). Ausentes = fila compartilhada (retrocompat).
        if assigned_to:
            event["assigned_to"] = assigned_to
        if fallback_to_pool_after_s is not None:
            event["fallback_to_pool_after_s"] = int(fallback_to_pool_after_s)
        # Wrap-up unificado (Camada E2) — auto-atendimento: flui ao contact_data (via
        # ConversationInboundEvent) e chega ao item de pull; o Console o lê e
        # auto-reivindica (inline) em vez de esperar o claim manual da inbox.
        if auto_attend:
            event["auto_attend"] = True
        # I5 — prazo do item: o TTL do JSON do contato na fila passa a acompanhá-lo,
        # em vez do default de 4 h que expirava antes do deadline e apagava a reserva.
        event["work_item_deadline"] = exp_at
        await self._publish(event, topic="conversations.inbound")

        # I5 — ledger do item de trabalho. Em conferência o item vai ao ZSET sob o id
        # do PAI (o especialista roda dentro dele), então queue_session_id == session_id.
        await self._write_work_task(
            tenant_id        = tenant_id,
            session_id       = session_id,
            queue_session_id = session_id,
            pool_id          = pool_id,
            resume_token     = resume_token,
            step_id          = step_id or "delegate_conference",
            assigned_to      = assigned_to or "",
            deadline         = exp_at,
            ttl_s            = ttl_s,
        )

        # ── Pending workflow lookup key (customer reconnect detection) ─────────
        # When the delegating caller is a workflow that captured a contact_identifier
        # (Session B → inbound_only confirmation), the customer is NOT connected here.
        # Write the pending_workflow key so the customer's later reconnect (Session
        # A-new intake) finds the parent resume_token via pending_workflow_get.
        # Only written when contact_identifier is present (absent for the A-new→C
        # reconnect delegate, where the customer is already connected).
        contact_id = context.get("contact_identifier") or context.get("session.contact_identifier")
        if contact_id:
            pending_key   = f"{tenant_id}:pending_workflow:{contact_id}"
            pending_value = json.dumps({
                "resume_token":     resume_token,
                "child_session_id": session_id,   # parent session hosts the specialist
                "pool":             pool_id,
                "context":          dict(context),
            })
            try:
                await self._redis.set(pending_key, pending_value, ex=ttl_s)
                logger.debug(
                    "delegate_conference: pending_workflow key written contact=%s parent=%s",
                    contact_id, session_id,
                )
            except Exception as _e:
                logger.warning("delegate_conference: could not write pending_workflow key: %s", _e)

        # ── Identity Resolver dual-write (Slice 3 gate) ───────────────────────
        # Cross-channel pending indexing under the native customer_id, gated on
        # customer_resumable (spec §6). Mirrors handle_delegate; best-effort.
        if self._identity_enabled and customer_resumable:
            try:
                anchors = self._anchors_from_context(context)
                if anchors:
                    ref = await self._identity.resolve_or_provision(tenant_id, anchors, provision=True)
                    if ref.customer_id:
                        # Journey J3: raiz canônica do parent (o especialista roda dentro dele).
                        _conf_root = await self._read_ctx_root(tenant_id, session_id) or session_id
                        await self._identity.write_pending(
                            tenant_id, ref.customer_id,
                            PendingEntry(
                                session_id=session_id,   # parent hosts the specialist
                                customer_id=ref.customer_id,
                                resume_token=resume_token,
                                pool=pool_id,
                                skill_id=context.get("skill_id"),
                                intent=context.get("intent"),
                                policy=resume_policy,
                                context_preview=self._pending_context_preview(context),
                                root_session_id=_conf_root,   # Journey J3
                            ),
                            ttl_s=ttl_s,
                        )
                        await self._identity.promote_to_durable(tenant_id, ref.customer_id, anchors)
                        logger.info(
                            "identity: pending_by_customer written (conference) customer=%s parent=%s matched_by=%s",
                            ref.customer_id, session_id, ref.matched_by,
                        )
            except Exception as _e:
                logger.warning("identity: dual-write failed (conference, non-fatal): %s", _e)

        logger.info(
            "delegate_conference: specialist=%s pool=%s parent=%s channel=%s tenant=%s",
            conference_id, pool_id, session_id, parent_channel, tenant_id,
        )
        return session_id   # specialist runs inside the parent session

    # ──────────────────────────────────────────────────────────────────────────
    # Timeout scanner (Arc 19 Fase D) — expira suspends/delegates vencidos
    # ──────────────────────────────────────────────────────────────────────────

    async def run_timeout_scanner(self, interval_s: int = 60) -> None:
        """
        Background task: expira tokens de resume vencidos.

        Varre periodicamente os hashes {tenant}:resume_tokens. Para cada token
        cujo expires_at (3º campo do valor) já passou, dispara handle_resume com
        decision="timeout" — o engine roteia para o on_timeout do step suspenso
        (suspend da operadora OU delegate de confirmação). Sem isso, uma sessão
        webhook suspensa cujo sinal externo nunca chega (operadora não aprova,
        cliente não reconecta) ficaria suspensa para sempre.

        handle_resume consome o token (HDEL), então cada expiração dispara uma vez.
        """
        logger.info("webhook timeout scanner started (interval=%ds)", interval_s)
        while True:
            try:
                await asyncio.sleep(interval_s)
                await self._scan_expired_resume_tokens()
            except asyncio.CancelledError:
                logger.info("webhook timeout scanner stopped")
                raise
            except Exception as exc:
                logger.warning("webhook timeout scanner iteration error: %s", exc)

    async def _scan_expired_resume_tokens(self) -> None:
        now = datetime.now(timezone.utc)
        async for raw_key in self._redis.scan_iter(match="*:resume_tokens", count=100):
            key       = raw_key if isinstance(raw_key, str) else raw_key.decode()
            tenant_id = key.rsplit(":resume_tokens", 1)[0]
            try:
                entries = await self._redis.hgetall(key)
            except Exception:
                continue
            for raw_token, raw_value in entries.items():
                token = raw_token if isinstance(raw_token, str) else raw_token.decode()
                value = raw_value if isinstance(raw_value, str) else raw_value.decode()
                # value: {session_id}:{step_id}:{expires_at_iso}  (split com maxsplit=2
                # preserva os ':' do timestamp ISO no terceiro campo)
                parts = value.split(":", 2)
                if len(parts) < 3:
                    continue
                try:
                    expires_at = datetime.fromisoformat(parts[2].replace("Z", "+00:00"))
                except Exception:
                    continue
                if now <= expires_at:
                    continue
                logger.info(
                    "webhook timeout scanner: expiring token session=%s step=%s tenant=%s deadline=%s",
                    parts[0], parts[1], tenant_id, parts[2],
                )
                try:
                    await self.handle_resume(
                        resume_token=token,
                        tenant_id=tenant_id,
                        payload={"decision": "timeout", "source": "timeout_scanner"},
                    )
                except ResumeAlreadyTerminalError as exc:
                    # Fase F — o scanner PERDER para uma entrega viva é o
                    # comportamento correto, não uma falha: o agente estava
                    # submetendo quando o prazo bateu, e a entrega vence o prazo.
                    # Logar como warning aqui ensinaria a operação a ignorar
                    # warnings do scanner.
                    logger.info(
                        "webhook timeout scanner: token=%s já terminal (%s por %s) — "
                        "expiração descartada, e é o resultado certo",
                        token, exc.state, exc.by or "?",
                    )
                except Exception as exc:
                    logger.warning(
                        "webhook timeout scanner: handle_resume failed token=%s: %s", token, exc,
                    )

    # ──────────────────────────────────────────────────────────────────────────
    # Work task ledger (I5) — o item de trabalho que o delegate parqueou
    # ──────────────────────────────────────────────────────────────────────────

    @staticmethod
    def work_task_key(tenant_id: str, session_id: str) -> str:
        """`{tenant}:work_task:{session_id}` — ver `_write_work_task`."""
        return f"{tenant_id}:work_task:{session_id}"

    async def _write_work_task(
        self,
        tenant_id:        str,
        session_id:       str,
        queue_session_id: str,
        pool_id:          str,
        resume_token:     str,
        step_id:          str,
        assigned_to:      str,
        deadline:         str,
        ttl_s:            int,
    ) -> None:
        """
        I5 — registra o fato "esta delegação parqueou um item de trabalho no pool P".

        É o ÚNICO lugar onde esse fato é conhecido: quem despacha. Nem o scanner de
        timeout nem o supervisor têm como derivá-lo depois —
          · `session:{id}:meta` carrega o pool do WORKFLOW enquanto ninguém reivindica
            (só o claim o reescreve), então serve exatamente no caso em que não
            precisamos dele e mente no caso em que precisamos;
          · `{t}:queue_contact:{sid}` morre por TTL antes do deadline do delegate.

        Chaveado pela sessão que o RESUME resolve (o pai), porque os dois gatilhos da
        I5 entram por lá: o scanner traz o token e resolve o pai; o supervisor traz o
        pai e precisa do token. `queue_session_id` é o id que está DE FATO no ZSET —
        hoje igual ao pai (todo delegate roda como especialista de conferência), e
        diferente dele se o `/delegate` roteado (sessão-filha com uuid próprio) voltar
        a ser usado. Guardar os dois custa um campo e tira a topologia do caminho.

        Escrita incondicional: o pool-alvo pode ser `push` (sem item de fila nenhum) e
        o expire é no-op nesse caso — a lease do claim é a evidência de pull, e sem
        ela nada é devolvido. Não vale acoplar o gateway ao `dispatch_mode` do pool
        para evitar uma escrita de uma chave.

        Consumido (e apagado) por `handle_resume`; expira junto com o token.
        """
        try:
            await self._redis.set(
                self.work_task_key(tenant_id, session_id),
                json.dumps({
                    "pool_id":          pool_id,
                    "queue_session_id": queue_session_id,
                    "resume_token":     resume_token,
                    "step_id":          step_id,
                    "assigned_to":      assigned_to,
                    "deadline":         deadline,
                    "created_at":       datetime.now(timezone.utc).isoformat(),
                }),
                ex=max(int(ttl_s), 60),
            )
        except Exception as _e:
            # Degradação nunca silenciosa: sem esta chave o item fica sem quem o
            # encerre (é a lacuna que a I5 fecha) — o motivo tem de aparecer.
            logger.warning(
                "work_task: could not write ledger session=%s pool=%s — %s "
                "(o item de trabalho ficará sem caminho de expiração)",
                session_id, pool_id, _e,
            )

    async def read_work_task(self, tenant_id: str, session_id: str) -> dict | None:
        """Lê o ledger do item de trabalho desta sessão. None = nenhum parqueado."""
        try:
            raw = await self._redis.get(self.work_task_key(tenant_id, session_id))
        except Exception:
            return None
        if not raw:
            return None
        try:
            data = json.loads(raw if isinstance(raw, str) else raw.decode())
            return data if isinstance(data, dict) else None
        except Exception:
            return None

    # ──────────────────────────────────────────────────────────────────────────
    # Internal helpers
    # ──────────────────────────────────────────────────────────────────────────

    async def _publish(self, event: dict, topic: str) -> None:
        """Publish a JSON event to a Kafka topic."""
        await self._producer.send_and_wait(
            topic,
            value=json.dumps(event).encode(),
        )
