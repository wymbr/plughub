"""
a2a_tasks.py — o adapter JSON-RPC do canal `a2a` (AAS-06; adr-a2a-server-binding D4, D5, D8, D14, D15).

Quem chega aqui já passou pela porta (`POST /a2a/{slug}`, AAS-04): credencial conferida, tenant
DA CREDENCIAL, pool em `allowed_pools`. Este módulo é a execução atrás dela.

**Task É a sessão (D4).** `taskId = session_id`, `contextId = root_session_id`. Não há tabela,
ledger nem ciclo de vida de task: o estado é DEDUZIDO, a cada leitura, dos fatos que a sessão já
deixa — e quando nenhum fato responde, a resposta diz que não sabe (`TASK_STATE_UNSPECIFIED`),
nunca um estado plausível:

    {t}:session:{sid}:result          → COMPLETED (contrato válido, artefato) · FAILED (inválido)
    session:{sid}:closed_recorded     → CANCELED (caller_cancel) · REJECTED (no_resource) · FAILED
    menu:waiting:{sid} (voltado ao cliente) + prompt do agente → INPUT_REQUIRED
      … e {t}:proof:session:{sid} (link de prova pendente)      → AUTH_REQUIRED (AAS-19)
    session:{sid}:meta com instance_id → WORKING ; sem → SUBMITTED

O que guardamos é só o que NÃO é fato da sessão: de quem é a task (principal, pool) para recusar
leitura alheia, o `contextId` emitido para conferir continuação (D15.4), e as mensagens que o
chamador mandou (o stream canônico só tem a fala do agente — o pedido inicial vai ao ContextStore).

**O pedido inicial é ENTRADA DE CONTRATO** (decisão do dono, 2026-10-01): o `DataPart` é validado
contra o `input_schema` do descritor do pool e semeado, com o texto, em `core.a2a.request` ANTES do
roteamento — o fluxo o lê do primeiro passo. As continuações (`INPUT_REQUIRED`) entram pelo caminho
de sempre: `conversations.inbound`, que o bridge entrega ao `menu` que espera.

**Prova do titular fora de banda** (AAS-19, D12): o fluxo que exige prova cria um link
(`identity_proof_link`) e espera num `menu`; enquanto o link está pendente a task é
`AUTH_REQUIRED`, com o link no status. A pessoa prova no navegador (`identity_proof.py`), e a task
segue SEM o chamador mandar nada — por isso o stream NÃO fecha nesse estado (spec § 7.6.1). O
chamador pode mandar mensagem (negociar ou recusar): ela chega ao menu como resposta, e quem decide
é o `identity_proof_status` do fluxo, nunca a mensagem.

**Bloco mascarado não atravessa** (D8): o canal não declara `masked_input`, e o `notification_send`
RECUSA menu mascarado antes de publicar — o fluxo segue o `on_failure`. O link fora de banda é
outra ficha.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Awaitable, Callable, Optional

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, UnknownType

from .collect_core import options_from_menu
from . import text_menu

logger = logging.getLogger("plughub.channel-gateway.a2a")

# ── Erros JSON-RPC (A2A v1.0 § erros) ─────────────────────────────────────────
PARSE_ERROR          = -32700
INVALID_REQUEST      = -32600
METHOD_NOT_FOUND     = -32601
INVALID_PARAMS       = -32602
INTERNAL_ERROR       = -32603
TASK_NOT_FOUND       = -32001
TASK_NOT_CANCELABLE  = -32002
UNSUPPORTED          = -32004
CONTENT_TYPE         = -32005
VERSION_UNSUPPORTED  = -32009

# ── Estados (ProtoJSON do `TaskState`) ────────────────────────────────────────
SUBMITTED      = "TASK_STATE_SUBMITTED"
WORKING        = "TASK_STATE_WORKING"
INPUT_REQUIRED = "TASK_STATE_INPUT_REQUIRED"
AUTH_REQUIRED  = "TASK_STATE_AUTH_REQUIRED"
COMPLETED      = "TASK_STATE_COMPLETED"
FAILED         = "TASK_STATE_FAILED"
CANCELED       = "TASK_STATE_CANCELED"
REJECTED       = "TASK_STATE_REJECTED"
UNSPECIFIED    = "TASK_STATE_UNSPECIFIED"
TERMINAL       = frozenset({COMPLETED, FAILED, CANCELED, REJECTED})
INTERRUPTED    = frozenset({INPUT_REQUIRED, AUTH_REQUIRED})
# interrompida em que a task SEGUE sozinha (a credencial chega fora de banda): o stream fica aberto
OUT_OF_BAND    = frozenset({AUTH_REQUIRED})

# ── Prazos PUBLICADOS no card (extensão task-lifetime; D15.5/D15.6) ─────────────
BLOCKING_CEILING_S = 25.0               # teto do `SendMessage` bloqueante
CONTEXT_TTL_S      = 30 * 86_400        # validade do contextId = TTL da journey
TASK_RECORD_TTL_S  = 86_400             # leitura da task depois de nascer (o resultado vive 4 h)
SESSION_META_TTL_S = 86_400
POLL_S             = 0.25
STREAM_CEILING_S   = 600.0              # um stream aberto fecha aqui; o chamador reassina (publicado)
HEARTBEAT_S        = 15.0               # comentário SSE quando nada acontece
STREAM_BLOCK_MS    = 1000               # o XREAD acorda nisto, para reler os fatos fora do stream
STREAMING_METHODS  = frozenset({"SendStreamingMessage", "SubscribeToTask"})
QUOTA_SCAN_MAX     = 200                # tasks mais recentes do principal olhadas para contar ativas
QUOTA_ACTIVE_RETRY_S = 30               # Retry-After quando o teto é de tasks ATIVAS
TEXT, JSON_MT      = "text/plain", "application/json"
OUR_MODES          = (TEXT, JSON_MT)
LIST_PAGE_MAX      = 100

CLOSE_TO_STATE = {"caller_cancel": CANCELED, "no_resource": REJECTED, "flow_complete": COMPLETED,
                  "agent_done": COMPLETED}
# fim que É o do fluxo: quem decide o estado é o resultado do `complete`, se houver
CLOSE_DEFERS_TO_RESULT = frozenset({"flow_complete", "agent_done"})


TASK_LIFETIME_EXTENSION_URI = "urn:plughub:a2a:extension:task-lifetime:v1"


def task_lifetime_extension() -> dict:
    """Os prazos que este adapter aplica, PUBLICADOS no card (D15.5/D15.6). Moram aqui, e não no
    registry que projeta o card, porque é aqui que eles valem: uma cópia lá envelheceria calada."""
    return {
        "uri": TASK_LIFETIME_EXTENSION_URI,
        "description": ("Prazos da tarefa: teto do SendMessage bloqueante, teto de um stream aberto "
                        "(depois dele, SubscribeToTask), validade do contextId, e onde ler o prazo de "
                        "resposta de uma tarefa em INPUT_REQUIRED e o do link de prova em AUTH_REQUIRED."),
        "required": False,
        "params": {
            "blocking_ceiling_s":   BLOCKING_CEILING_S,
            "stream_ceiling_s":     STREAM_CEILING_S,
            "context_validity_s":   CONTEXT_TTL_S,
            "task_readable_s":      TASK_RECORD_TTL_S,
            "input_deadline_field": "metadata.plughub.input_deadline",
            # AAS-19: até quando vale o link de prova de uma tarefa em AUTH_REQUIRED
            "auth_deadline_field":  "metadata.plughub.auth_deadline",
        },
    }


class A2AError(Exception):
    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(message)
        self.code, self.message, self.data = code, message, data

    def as_json(self) -> dict:
        err: dict = {"code": self.code, "message": self.message}
        if self.data is not None:
            err["data"] = self.data
        return err


SUPPORTED_VERSIONS = ("1.0",)            # Major.Minor que esta interface fala (o card publica o mesmo)


def version_error(header: str | None, query: str | None) -> "A2AError | None":
    """`A2A-Version` (spec v1.0 § 3.6): header ou parâmetro de query, comparado por Major.Minor.

    AUSENTE significa 0.3, não "a mais nova" — o texto é explícito (*"0.3 will be assumed for
    empty header"*), e é assim que os dois SDKs oficiais julgam do lado servidor. Servir 1.0 a
    quem não declarou seria responder com a semântica que o cliente pode não ler. Os SDKs
    oficiais mandam o header em toda chamada (medido: a2a-sdk 1.2.1, @a2a-js/sdk 1.3.0).
    """
    raw = (header or query or "").strip()
    pedida = raw or "0.3"
    partes = pedida.split(".")
    major_minor = ".".join(partes[:2]) if len(partes) >= 2 else pedida
    if major_minor in SUPPORTED_VERSIONS:
        return None
    return A2AError(VERSION_UNSUPPORTED, "VersionNotSupportedError",
                    {"requested": pedida, "assumed": not raw, "supported": list(SUPPORTED_VERSIONS)})


class A2AUnavailable(Exception):
    """Não deu para conferir o contrato do pool — a porta responde 503 + Retry-After (§9)."""


class A2AQuota(Exception):
    """AAS-09 — cota do principal esgotada (D9). A porta responde 429 + Retry-After: é back-pressure
    para chamador de máquina, como o 503 — a spec não tem erro JSON-RPC de cota."""

    def __init__(self, which: str, limit: int, retry_after_s: int) -> None:
        super().__init__(f"cota {which} ({limit}) esgotada")
        self.which, self.limit, self.retry_after_s = which, limit, retry_after_s


@dataclass(frozen=True)
class Holder:
    """AAS-09 — o TITULAR que o token `customer_agent` carrega (D6: vem do token, nunca da Part)."""
    customer_id:       str
    proof_mechanism:   str
    proof_verified_at: str
    mandate:           tuple[str, ...] = ()


@dataclass(frozen=True)
class Caller:
    sub:       str
    kind:      str
    tenant_id: str
    pool_id:   str
    slug:      str
    holder:    Optional[Holder] = None
    # AAS-09 — (tasks ativas, tasks por dia UTC); None = sem cota (o `partner`, D9)
    quota:     Optional[tuple[int, int]] = None


PoolFetcher = Callable[[str, str], Awaitable[tuple[str, Optional[dict], str]]]
Publisher   = Callable[[str, dict, str], Awaitable[None]]      # (topic, payload, key)
CtxWriter   = Callable[..., Awaitable[Any]]


def _s(v: Any) -> str:
    return v.decode() if isinstance(v, bytes) else ("" if v is None else str(v))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ts(v: str) -> float:
    try:
        return datetime.fromisoformat(v.replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError):
        return 0.0


# ── Leitura das partes do chamador (D14.1: v1 = text + data) ──────────────────

def read_parts(message: Any) -> tuple[str, Any, bool]:
    """(texto, data, tem_data). Arquivo (`raw`/`url`) é recusado: só depois do arco ATT (AAS-12)."""
    if not isinstance(message, dict):
        raise A2AError(INVALID_PARAMS, "message ausente ou malformada")
    role = message.get("role")
    if role not in ("ROLE_USER", None):
        raise A2AError(INVALID_PARAMS, f"role '{role}' não aceito: quem chama fala como ROLE_USER")
    parts = message.get("parts")
    if not isinstance(parts, list) or not parts:
        raise A2AError(INVALID_PARAMS, "message.parts vazia")
    textos: list[str] = []
    data: Any = None
    tem_data = False
    for p in parts:
        if not isinstance(p, dict):
            raise A2AError(INVALID_PARAMS, "part malformada")
        if "raw" in p or "url" in p or "file" in p:
            raise A2AError(CONTENT_TYPE, "ContentTypeNotSupportedError",
                           {"detail": "arquivo em Part ainda não é aceito (só text e data)"})
        if "text" in p:
            textos.append(str(p["text"]))
        elif "data" in p:
            if tem_data:
                raise A2AError(INVALID_PARAMS, "mais de um DataPart numa mensagem")
            data, tem_data = p["data"], True
        else:
            raise A2AError(INVALID_PARAMS, "part sem text nem data")
    return "\n".join(t for t in textos if t), data, tem_data


def accepts_json(configuration: Any) -> bool:
    modes = (configuration or {}).get("acceptedOutputModes") if isinstance(configuration, dict) else None
    if not modes:
        return True
    if not any(m in OUR_MODES for m in modes):
        raise A2AError(CONTENT_TYPE, "ContentTypeNotSupportedError",
                       {"detail": f"este agente produz {list(OUR_MODES)}"})
    return JSON_MT in modes


def validate_input(schema: Any, instance: Any) -> list[str]:
    """Erros do pedido contra o `input_schema` do contrato. Sem DataPart, valida-se `{}` — então
    contrato com campo obrigatório recusa pedido só de texto, NOMEANDO o campo."""
    try:
        Draft202012Validator.check_schema(schema or {})
        errs = sorted(Draft202012Validator(schema or {}).iter_errors(instance), key=lambda e: list(e.path))
    except (SchemaError, UnknownType) as exc:
        raise A2AUnavailable(f"input_schema do pool ilegível: {getattr(exc, 'message', exc)}") from exc
    return [f"{'/'.join(str(p) for p in e.path) or '(raiz)'}: {e.message}"[:300] for e in errs[:5]]


# ── O formato de resposta de um menu (D14.2: DataPart com JSON Schema + texto) ─

def response_schema(payload: dict) -> Optional[dict]:
    interaction = payload.get("interaction") or "text"
    opts = options_from_menu(payload.get("options"))
    if interaction in ("button", "list") and opts:
        return {"type": "object", "required": ["choice"],
                "properties": {"choice": {"enum": [o.id for o in opts]}}}
    if interaction == "checklist" and opts:
        return {"type": "object", "required": ["choices"],
                "properties": {"choices": {"type": "array", "uniqueItems": True,
                                           "items": {"enum": [o.id for o in opts]}}}}
    fields = payload.get("fields")
    if interaction == "form" and isinstance(fields, list) and fields:
        tipo = {"number": "number", "boolean": "boolean"}
        return {"type": "object",
                "required": [f["id"] for f in fields if isinstance(f, dict) and f.get("required")],
                "properties": {f["id"]: {"type": tipo.get(str(f.get("type")), "string"),
                                         "title": str(f.get("label") or f["id"])}
                               for f in fields if isinstance(f, dict) and f.get("id")}}
    return None


def render_prompt(payload: dict) -> str:
    opts = options_from_menu(payload.get("options"))
    interaction = payload.get("interaction") or "text"
    prompt = str(payload.get("prompt") or "")
    if interaction in text_menu.CHOICE_INTERACTIONS and opts:
        return text_menu.render(prompt, opts, interaction)
    fields = payload.get("fields")
    if interaction == "form" and isinstance(fields, list) and fields:
        linhas = [prompt.strip(), ""] if prompt.strip() else []
        linhas += [f"- {f.get('label') or f.get('id')}{' (obrigatório)' if f.get('required') else ''}"
                   for f in fields if isinstance(f, dict)]
        return "\n".join(linhas)
    return prompt


def menu_answer(payload: dict, text: str, data: Any, has_data: bool) -> Optional[dict]:
    """O `menu_result` que a resposta do chamador nomeia, ou None (segue como TEXTO cru: é o motor
    quem recusa e reenvia — um segundo juiz aqui discordaria dele, NIV-13)."""
    interaction = payload.get("interaction") or "text"
    menu_id = str(payload.get("menu_id") or "")
    opts = options_from_menu(payload.get("options"))
    valor: Any = None
    if has_data and isinstance(data, dict):
        if interaction in ("button", "list") and "choice" in data:
            valor = str(data["choice"])
        elif interaction == "checklist" and isinstance(data.get("choices"), list):
            valor = [str(c) for c in data["choices"]]
        elif interaction == "form":
            valor = data
    if valor is None and text and interaction in text_menu.CHOICE_INTERACTIONS and opts:
        valor = text_menu.resolve(text, opts, interaction)
    if valor is None:
        return None
    return {"menu_id": menu_id, "interaction": interaction, "result": valor}


# ── O serviço ─────────────────────────────────────────────────────────────────

class A2ATaskService:
    def __init__(self, *, redis: Any, publish: Publisher, fetch_pool: PoolFetcher,
                 write_ctx: CtxWriter, ceiling_s: float = BLOCKING_CEILING_S,
                 poll_s: float = POLL_S, stream_ceiling_s: float = STREAM_CEILING_S,
                 heartbeat_s: float = HEARTBEAT_S, block_ms: int = STREAM_BLOCK_MS) -> None:
        self._r = redis
        self._publish = publish
        self._fetch_pool = fetch_pool
        self._write_ctx = write_ctx
        self._ceiling = ceiling_s
        self._poll = poll_s
        self._stream_ceiling = stream_ceiling_s
        self._heartbeat = heartbeat_s
        self._block_ms = block_ms

    # chaves do que NÃO é fato da sessão
    @staticmethod
    def _k_task(t: str, sid: str) -> str:        return f"{t}:a2a:task:{sid}"
    @staticmethod
    def _k_inbox(t: str, sid: str) -> str:       return f"{t}:a2a:task:{sid}:inbox"
    @staticmethod
    def _k_answered(t: str, sid: str) -> str:    return f"{t}:a2a:task:{sid}:answered"
    @staticmethod
    def _k_index(t: str, sub: str) -> str:       return f"{t}:a2a:principal:{sub}:tasks"
    @staticmethod
    def _k_context(t: str, ctx: str) -> str:     return f"{t}:a2a:context:{ctx}"

    # ── despacho ─────────────────────────────────────────────────────────────
    async def handle(self, caller: Caller, req: Any) -> dict:
        rid = req.get("id") if isinstance(req, dict) else None
        try:
            if not isinstance(req, dict) or req.get("jsonrpc") != "2.0" or not isinstance(req.get("method"), str):
                raise A2AError(INVALID_REQUEST, "Invalid Request")
            params = req.get("params") if isinstance(req.get("params"), dict) else {}
            method = req["method"]
            if method == "SendMessage":
                result = await self.send_message(caller, params)
            elif method == "GetTask":
                result = await self.get_task(caller, params)
            elif method == "CancelTask":
                result = await self.cancel_task(caller, params)
            elif method == "ListTasks":
                result = await self.list_tasks(caller, params)
            else:
                raise A2AError(METHOD_NOT_FOUND, "Method not found", {"method": method})
            return {"jsonrpc": "2.0", "id": rid, "result": result}
        except A2AError as e:
            return {"jsonrpc": "2.0", "id": rid, "error": e.as_json()}

    # ── SendMessage ──────────────────────────────────────────────────────────
    async def send_message(self, caller: Caller, params: dict) -> dict:
        message = params.get("message")
        text, data, has_data = read_parts(message)
        configuration = params.get("configuration") if isinstance(params.get("configuration"), dict) else {}
        want_json = accepts_json(configuration)
        history_length = configuration.get("historyLength")
        return_now = bool(configuration.get("returnImmediately"))
        task_id = message.get("taskId") if isinstance(message, dict) else None

        if task_id:
            sid, mark = await self._continue(caller, str(task_id), message, text, data, has_data)
        else:
            sid = await self._create(caller, message, text, data, has_data)
            mark = "-"
        task = await self._settle(caller, sid, mark, return_now, want_json, history_length)
        return {"task": task}

    # ── Streaming (AAS-07): SendStreamingMessage e SubscribeToTask, SSE ─────────
    #
    # Spec v1.0: a primeira mensagem é o `Task`; depois `statusUpdate`/`artifactUpdate`; o stream
    # FECHA em estado terminal ou interrompido (§ 3.1.2 e § HTTP "terminal or interrupted"). A
    # `SubscribeToTask` em task terminal é UnsupportedOperation. Não há campo `final` na v1.0:
    # quem diz "acabou" é o fechamento.
    #
    # O que acorda o laço é o STREAM CANÔNICO (XREAD BLOCK no `session:{sid}:stream`): fala nova do
    # agente chega na hora. Os fatos que NÃO estão no stream (resultado, `menu:waiting`, meta) são
    # relidos a cada volta, então aparecem em até um bloqueio (1 s). O estado nunca vem do evento —
    # é sempre o `_facts`, a mesma dedução do `GetTask`.

    async def open_stream(self, caller: Caller, req: Any) -> AsyncIterator[Optional[dict]]:
        """Faz o trabalho de ANTES do stream (validar, criar/continuar a task) e devolve o gerador
        de eventos. Erro aqui levanta `A2AError` — a porta responde JSON-RPC comum, sem SSE."""
        if not isinstance(req, dict) or req.get("jsonrpc") != "2.0" or not isinstance(req.get("method"), str):
            raise A2AError(INVALID_REQUEST, "Invalid Request")
        rid = req.get("id")
        params = req.get("params") if isinstance(req.get("params"), dict) else {}
        if req["method"] == "SendStreamingMessage":
            message = params.get("message")
            text, data, has_data = read_parts(message)
            configuration = params.get("configuration") if isinstance(params.get("configuration"), dict) else {}
            want_json = accepts_json(configuration)
            history_length = configuration.get("historyLength")
            task_id = message.get("taskId") if isinstance(message, dict) else None
            if task_id:
                sid, mark = await self._continue(caller, str(task_id), message, text, data, has_data)
            else:
                sid, mark = await self._create(caller, message, text, data, has_data), "-"
        elif req["method"] == "SubscribeToTask":
            sid = str(params.get("id") or "")
            if not sid:
                raise A2AError(INVALID_PARAMS, "id ausente")
            rec = await self._record(caller, sid)
            view = await self._facts(caller.tenant_id, sid, rec)
            if view["state"] in TERMINAL:
                raise A2AError(UNSUPPORTED, "UnsupportedOperationError",
                               {"detail": f"a task já terminou ({view['state']}); use GetTask"})
            # já interrompida: o Task é o primeiro evento e o stream fecha logo — é o estado que
            # pede ação do chamador, não há o que esperar do lado de cá
            mark, want_json, history_length = "-", True, params.get("historyLength")
        else:
            raise A2AError(METHOD_NOT_FOUND, "Method not found", {"method": req["method"]})
        rec = await self._record(caller, sid)
        return self._events(caller, rid, sid, rec, mark, want_json, history_length)

    async def _events(self, caller: Caller, rid: Any, sid: str, rec: dict, mark: str, want_json: bool,
                      history_length: Any) -> AsyncIterator[Optional[dict]]:
        t = caller.tenant_id

        def wrap(result: dict) -> dict:
            return {"jsonrpc": "2.0", "id": rid, "result": result}

        def fecha(v: dict) -> bool:
            novo = mark == "-" or v["last_stream_id"] != mark
            # AUTH_REQUIRED não fecha: a prova chega fora de banda e a task continua sem o chamador
            # mandar nada — a spec pede manter o stream (§ 7.6.1)
            return v["state"] in TERMINAL or (v["state"] in INTERRUPTED - OUT_OF_BAND and novo)

        cursor = await self._stream_cursor(sid)
        view = await self._facts(t, sid, rec)
        yield wrap({"task": self._task_json(sid, rec, view, want_json, history_length)})
        enviados = {m["id"] for m in view["agent_msgs"]}
        vistos: set[str] = set()                   # fala nova vista num ciclo, à espera de sair
        estado = view["state"]
        if fecha(view):
            return
        inicio = ultimo = time.monotonic()
        while True:
            if time.monotonic() - inicio >= self._stream_ceiling:
                # o chamador reassina (`SubscribeToTask`); fechar no teto é publicado no card
                logger.info("a2a: stream da task %s fechado no teto de %.0fs (estado %s)",
                            sid, self._stream_ceiling, estado)
                return
            cursor = await self._wait(sid, cursor)
            view = await self._facts(t, sid, rec)
            task = self._task_json(sid, rec, view, want_json, None)
            final = fecha(view)
            emitiu = False
            novos = [m for m in view["agent_msgs"] if m["id"] not in enviados]
            if view["state"] in INTERRUPTED and novos:
                # a última fala de uma task INTERROMPIDA é o prompt, que vai NO status — não duas vezes
                novos = novos[:-1]
            elif not final:
                # Fala nova só sai como progresso depois de SOBREVIVER a um ciclo sem a task
                # interromper. Medido ao vivo: o `notification_send` grava o prompt no stream e o
                # engine só marca `menu:waiting` logo depois; acordado pelo prompt, o laço via a task
                # WORKING com fala nova e o mandava como progresso — e de novo no INPUT_REQUIRED.
                # Custo: até um bloqueio (1 s) de atraso na fala que NÃO é prompt.
                prontos = [m for m in novos if m["id"] in vistos]
                vistos.update(m["id"] for m in novos)
                novos = prontos
            for m in novos:
                yield wrap({"statusUpdate": {
                    "taskId": sid, "contextId": rec["context_id"],
                    "status": {"state": WORKING, "timestamp": _now(),
                               "message": self._agent_message(sid, rec, m, want_json)}}})
                enviados.add(m["id"])
                emitiu = True
            if final and view["state"] == COMPLETED and task.get("artifacts"):
                for art in task["artifacts"]:
                    yield wrap({"artifactUpdate": {"taskId": sid, "contextId": rec["context_id"],
                                                   "artifact": art, "append": False, "lastChunk": True}})
                emitiu = True
            if final or view["state"] != estado:
                yield wrap({"statusUpdate": {"taskId": sid, "contextId": rec["context_id"],
                                             "status": task["status"], "metadata": task["metadata"]}})
                enviados.update(m["id"] for m in view["agent_msgs"])
                estado = view["state"]
                emitiu = True
            if final:
                return
            if emitiu:
                ultimo = time.monotonic()
            elif time.monotonic() - ultimo >= self._heartbeat:
                yield None                                  # comentário SSE: mantém proxy acordado
                ultimo = time.monotonic()

    async def _stream_cursor(self, sid: str) -> str:
        try:
            ult = await self._r.xrevrange(f"session:{sid}:stream", "+", "-", count=1)
        except Exception:                                   # noqa: BLE001 — sem cursor, lê do começo
            ult = []
        return _s(ult[0][0]) if ult else "0-0"

    async def _wait(self, sid: str, cursor: str) -> str:
        """Bloqueia até entrada nova no stream canônico ou o fim do bloqueio. Devolve o cursor."""
        try:
            res = await self._r.xread({f"session:{sid}:stream": cursor}, block=self._block_ms, count=100)
        except Exception as exc:                            # noqa: BLE001 — degrada para sondagem, dito
            logger.warning("a2a: XREAD do stream da task %s falhou (%s) — sondando", sid, exc)
            await asyncio.sleep(self._poll)
            return cursor
        if res:
            entries = res[0][1]
            if entries:
                return _s(entries[-1][0])
        return cursor

    def _agent_message(self, sid: str, rec: dict, m: dict, want_json: bool) -> dict:
        parts: list[dict] = [{"text": m["text"]}] if m["kind"] == "text" else [{"text": render_prompt(m["payload"])}]
        if m["kind"] == "menu":
            schema = response_schema(m["payload"])
            if want_json and schema is not None:
                parts.append({"data": {"menu_id": m["payload"].get("menu_id"),
                                       "interaction": m["payload"].get("interaction"),
                                       "response_schema": schema}, "mediaType": JSON_MT})
        return {"messageId": m["id"], "contextId": rec["context_id"], "taskId": sid,
                "role": "ROLE_AGENT", "parts": parts}

    async def _create(self, caller: Caller, message: dict, text: str, data: Any, has_data: bool) -> str:
        t = caller.tenant_id
        context_id = message.get("contextId")
        if context_id:
            dono = _s(await self._r.get(self._k_context(t, str(context_id))))
            if dono != caller.sub:
                # D15.4 — emitido por nós, para ESTE principal; fora disso recusa, nunca adota.
                # Mesma resposta para "não existe" e "é de outro": a recusa não é oráculo.
                logger.warning("a2a: contextId %s recusado para o principal %s (%s)",
                               context_id, caller.sub, "alheio" if dono else "desconhecido")
                raise A2AError(INVALID_PARAMS, "contextId não reconhecido para este chamador")

        verdict, pool, reason = await self._fetch_pool(t, caller.pool_id)
        if verdict == "unavailable":
            raise A2AUnavailable(reason)
        if verdict != "exists" or pool is None:
            raise A2AError(UNSUPPORTED, "UnsupportedOperationError", {"detail": "pool indisponível"})
        descriptor = pool.get("a2a") if isinstance(pool.get("a2a"), dict) else None
        # Herdado da AAS-04: a régua de quem pode chamar vale também AGORA, não só ao conceder.
        if ("a2a" not in (pool.get("channel_types") or []) or descriptor is None
                or caller.kind not in (descriptor.get("principal_kinds") or [])):
            logger.warning("a2a: pool %s não expõe mais A2A ao tipo %s — task recusada (principal %s)",
                           caller.pool_id, caller.kind, caller.sub)
            raise A2AError(UNSUPPORTED, "UnsupportedOperationError",
                           {"detail": "este agente não atende mais este tipo de chamador"})
        erros = validate_input(descriptor.get("input_schema"), data if has_data else {})
        if erros:
            raise A2AError(INVALID_PARAMS, "o pedido não cumpre o input_schema do agente", {"errors": erros})
        # Depois de toda validação: um pedido recusado não gasta cota.
        if caller.quota is not None:
            await self._charge_quota(caller)

        sid = str(uuid.uuid4())
        root = str(context_id) if context_id else sid
        cust_pid = f"cust_{uuid.uuid4().hex[:12]}"
        # O TITULAR não é o principal (D6). No `customer_agent` ele vem do token — é a pessoa que
        # provou a posse e gerou a credencial; no `partner` não há titular, e a sessão nasce com um
        # cliente de sistema até o fluxo identificar alguém (dito numa Part, é sempre `claimed`).
        customer_id = caller.holder.customer_id if caller.holder else f"sys:a2a:{uuid.uuid4().hex[:8]}"
        now = _now()

        tags: dict[str, Any] = {
            "core.contact.root_session_id": root,
            "core.a2a.request": {"text": text, **({"data": data} if has_data else {})},
            "core.a2a.principal_id": caller.sub,
            "core.a2a.principal_kind": caller.kind,
        }
        if caller.holder:
            # A prova é a da EMISSÃO, e vai com a idade dela: não é evidência DESTA sessão
            # (`core.journey.identity.*` é do escritor único e exige prova aqui). Skill que pedir
            # prova fresca não a encontra, e isso é o certo — `AUTH_REQUIRED` é ficha própria.
            tags["core.a2a.holder"] = {
                "customer_id":       caller.holder.customer_id,
                "proof_mechanism":   caller.holder.proof_mechanism,
                "proof_verified_at": caller.holder.proof_verified_at,
            }
            tags["core.a2a.mandate"] = list(caller.holder.mandate)
        await self._write_ctx(t, sid, tags)
        meta = {"tenant_id": t, "channel": "a2a", "contact_id": sid, "customer_id": customer_id,
                "session_id": sid, "started_at": now, "customer_participant_id": cust_pid,
                "a2a_principal_id": caller.sub, "a2a_principal_kind": caller.kind}
        await self._r.setex(f"session:{sid}:meta", SESSION_META_TTL_S, json.dumps(meta))
        await self._r.setex(f"session:{sid}:contact_id", SESSION_META_TTL_S, sid)
        await self._r.setex(f"session:{sid}:customer_participant_id", SESSION_META_TTL_S, cust_pid)

        rec = {"sub": caller.sub, "kind": caller.kind, "pool_id": caller.pool_id, "slug": caller.slug,
               "context_id": root, "created_at": now, "customer_participant_id": cust_pid,
               "customer_id": customer_id}
        await self._r.setex(self._k_task(t, sid), TASK_RECORD_TTL_S, json.dumps(rec))
        await self._r.setex(self._k_context(t, root), CONTEXT_TTL_S, caller.sub)
        await self._r.zadd(self._k_index(t, caller.sub), {sid: time.time() * 1000})
        await self._r.expire(self._k_index(t, caller.sub), CONTEXT_TTL_S)
        await self._append_inbox(t, sid, message, text, data, has_data, root)

        await self._publish("conversations.events", {
            "event_type": "contact_open", "contact_id": sid, "session_id": sid, "tenant_id": t,
            "channel": "a2a", "started_at": now, "channel_session_id": None,
        }, sid)
        await self._publish("conversations.inbound", {
            "event_id": str(uuid.uuid4()), "session_id": sid, "tenant_id": t, "customer_id": customer_id,
            "channel": "a2a", "pool_id": caller.pool_id, "started_at": now, "elapsed_ms": 0,
            "customer_participant_id": cust_pid, "root_session_id": root, "timestamp": now,
        }, sid)
        logger.info("a2a: task %s criada no pool %s pelo principal %s (contexto %s)",
                    sid, caller.pool_id, caller.sub, root)
        return sid

    async def _charge_quota(self, caller: Caller) -> None:
        """AAS-09 — a cota do principal (D9): tasks NÃO terminadas ao mesmo tempo, e tasks novas por
        dia (UTC). Conta por PRINCIPAL, em todos os pools do token. Ativa é deduzida dos FATOS de
        cada task (o mesmo `_facts` do GetTask), nunca de um contador que alguém tenha de
        decrementar — um decremento perdido prenderia o token para sempre."""
        assert caller.quota is not None
        max_ativas, max_dia = caller.quota
        t = caller.tenant_id
        ativas = 0
        ids = [_s(x) for x in await self._r.zrevrange(self._k_index(t, caller.sub), 0, QUOTA_SCAN_MAX - 1)]
        for sid in ids:
            raw = await self._r.get(self._k_task(t, sid))
            if not raw:
                continue
            view = await self._facts(t, sid, json.loads(_s(raw)))
            if view["state"] not in TERMINAL:
                ativas += 1
                if ativas >= max_ativas:
                    logger.warning("a2a: principal %s no teto de tasks ativas (%d) — recusada", caller.sub, max_ativas)
                    raise A2AQuota("active", max_ativas, QUOTA_ACTIVE_RETRY_S)
        hoje = datetime.now(timezone.utc)
        k = f"{t}:a2a:quota:{caller.sub}:{hoje:%Y%m%d}"
        n = await self._r.incr(k)
        if n == 1:
            await self._r.expire(k, 2 * 86_400)
        if n > max_dia:
            amanha = (hoje.replace(hour=0, minute=0, second=0, microsecond=0).timestamp() + 86_400)
            logger.warning("a2a: principal %s no teto diário (%d) — recusada", caller.sub, max_dia)
            raise A2AQuota("daily", max_dia, max(1, int(amanha - hoje.timestamp())))

    async def _continue(self, caller: Caller, sid: str, message: dict, text: str, data: Any,
                        has_data: bool) -> tuple[str, str]:
        t = caller.tenant_id
        rec = await self._record(caller, sid)
        ctx = message.get("contextId")
        if ctx and str(ctx) != rec["context_id"]:
            raise A2AError(INVALID_PARAMS, "contextId não corresponde à task")
        view = await self._facts(t, sid, rec)
        state = view["state"]
        if state in TERMINAL:
            # D15.3 — continuar depois do fim é task NOVA no mesmo contextId.
            raise A2AError(UNSUPPORTED, "UnsupportedOperationError",
                           {"detail": "a task terminou; mande uma mensagem nova com o mesmo contextId"})
        if state not in INTERRUPTED:
            raise A2AError(UNSUPPORTED, "UnsupportedOperationError",
                           {"detail": f"a task não espera resposta (estado {state})"})
        mark = view["last_stream_id"] or "-"
        pending = view["pending_menu"]
        answer = menu_answer(pending, text, data, has_data) if pending else None
        if answer is not None:
            content = {"type": "menu_result", "payload": answer}
            await self._r.sadd(self._k_answered(t, sid), answer["menu_id"])
            await self._r.expire(self._k_answered(t, sid), TASK_RECORD_TTL_S)
        else:
            content = {"type": "text", "text": text if text else (json.dumps(data) if has_data else "")}
        await self._append_inbox(t, sid, message, text, data, has_data, rec["context_id"])
        await self._publish("conversations.inbound", {
            "message_id": str(uuid.uuid4()), "contact_id": sid, "session_id": sid, "timestamp": _now(),
            "direction": "inbound", "channel": "a2a", "content_type": content["type"],
            "author": {"type": "customer"}, "content": content, "context_snapshot": {},
        }, sid)
        return sid, mark

    async def _append_inbox(self, t: str, sid: str, message: dict, text: str, data: Any,
                            has_data: bool, context_id: str) -> None:
        parts: list[dict] = []
        if text:
            parts.append({"text": text})
        if has_data:
            parts.append({"data": data, "mediaType": JSON_MT})
        entry = {"messageId": str(message.get("messageId") or uuid.uuid4()), "role": "ROLE_USER",
                 "parts": parts, "contextId": context_id, "taskId": sid, "_ts": _now()}
        await self._r.rpush(self._k_inbox(t, sid), json.dumps(entry))
        await self._r.expire(self._k_inbox(t, sid), TASK_RECORD_TTL_S)

    async def _settle(self, caller: Caller, sid: str, mark: str, return_now: bool, want_json: bool,
                      history_length: Any) -> dict:
        """Bloqueia até a task ASSENTAR (terminal, ou interrompida com fala NOVA do agente) ou o teto.
        No teto devolve o estado medido — WORKING/SUBMITTED —, nunca um estado inventado."""
        t = caller.tenant_id
        rec = await self._record(caller, sid)
        deadline = time.monotonic() + (0 if return_now else self._ceiling)
        while True:
            view = await self._facts(t, sid, rec)
            # interrompida só ASSENTA com fala NOVA do agente: logo após a resposta do chamador o
            # menu antigo ainda está em `menu:waiting`, e devolvê-lo seria pedir de novo o respondido
            novo = mark == "-" or view["last_stream_id"] != mark
            if view["state"] in TERMINAL or (view["state"] in INTERRUPTED and novo):
                break
            if time.monotonic() >= deadline:
                break
            await asyncio.sleep(self._poll)
        return self._task_json(sid, rec, view, want_json, history_length)

    # ── GetTask / CancelTask / ListTasks ─────────────────────────────────────
    async def get_task(self, caller: Caller, params: dict) -> dict:
        sid = str(params.get("id") or "")
        if not sid:
            raise A2AError(INVALID_PARAMS, "id ausente")
        rec = await self._record(caller, sid)
        view = await self._facts(caller.tenant_id, sid, rec)
        return self._task_json(sid, rec, view, True, params.get("historyLength"))

    async def cancel_task(self, caller: Caller, params: dict) -> dict:
        sid = str(params.get("id") or "")
        if not sid:
            raise A2AError(INVALID_PARAMS, "id ausente")
        t = caller.tenant_id
        rec = await self._record(caller, sid)
        view = await self._facts(t, sid, rec)
        if view["state"] in TERMINAL:
            raise A2AError(TASK_NOT_CANCELABLE, "TaskNotCancelableError", {"state": view["state"]})
        # D15.7 — `caller_cancel`, NUNCA `customer_abandon`: desistência programática não é
        # abandono, e a taxa de abandono do pool subiria plausível.
        await self._publish("conversations.events", {
            "event_type": "contact_closed", "contact_id": sid, "session_id": sid, "tenant_id": t,
            "channel": "a2a", "reason": "caller_cancel", "started_at": rec["created_at"],
            "ended_at": _now(), "pool_id": rec["pool_id"], "customer_id": rec.get("customer_id", ""),
            "close_reason": "caller_cancel", "source": "channel_gateway",
        }, sid)
        logger.info("a2a: task %s cancelada pelo principal %s", sid, caller.sub)
        deadline = time.monotonic() + min(self._ceiling, 5.0)
        while time.monotonic() < deadline:
            view = await self._facts(t, sid, rec)
            if view["state"] in TERMINAL:
                break
            await asyncio.sleep(self._poll)
        return self._task_json(sid, rec, view, True, params.get("historyLength"))

    async def list_tasks(self, caller: Caller, params: dict) -> dict:
        t = caller.tenant_id
        ctx = params.get("contextId")
        status = params.get("status")
        try:
            size = max(1, min(int(params.get("pageSize") or 50), LIST_PAGE_MAX))
            offset = int(params.get("pageToken") or 0)
        except (TypeError, ValueError):
            raise A2AError(INVALID_PARAMS, "pageSize/pageToken inválidos")
        ids = [_s(x) for x in await self._r.zrevrange(self._k_index(t, caller.sub), 0, -1)]
        tasks: list[dict] = []
        for sid in ids:
            raw = await self._r.get(self._k_task(t, sid))
            if not raw:
                await self._r.zrem(self._k_index(t, caller.sub), sid)   # expirou: sai do índice
                continue
            rec = json.loads(_s(raw))
            if rec.get("pool_id") != caller.pool_id:
                continue
            if ctx and rec.get("context_id") != ctx:
                continue
            view = await self._facts(t, sid, rec)
            if status and view["state"] != status:
                continue
            tasks.append(self._task_json(sid, rec, view, True, params.get("historyLength") or 0))
        page = tasks[offset:offset + size]
        nxt = str(offset + size) if offset + size < len(tasks) else ""
        return {"tasks": page, "nextPageToken": nxt, "pageSize": size, "totalSize": len(tasks)}

    # ── leitura ──────────────────────────────────────────────────────────────
    async def _record(self, caller: Caller, sid: str) -> dict:
        raw = await self._r.get(self._k_task(caller.tenant_id, sid))
        rec = json.loads(_s(raw)) if raw else None
        # task alheia e task inexistente têm a MESMA resposta
        if not rec or rec.get("sub") != caller.sub or rec.get("pool_id") != caller.pool_id:
            raise A2AError(TASK_NOT_FOUND, "TaskNotFoundError")
        return rec

    async def _facts(self, t: str, sid: str, rec: dict) -> dict:
        result_raw = await self._r.get(f"{t}:session:{sid}:result")
        closed = _s(await self._r.get(f"session:{sid}:closed_recorded"))
        meta_raw = await self._r.get(f"session:{sid}:meta")
        waiting = await self._r.hgetall(f"menu:waiting:{sid}") or {}
        entries = await self._r.xrange(f"session:{sid}:stream", "-", "+", count=500) or []
        answered = {_s(x) for x in (await self._r.smembers(self._k_answered(t, sid)) or set())}
        inbox = [json.loads(_s(x)) for x in (await self._r.lrange(self._k_inbox(t, sid), 0, -1) or [])]
        deadline = await self._r.zscore(f"{t}:menu:deadlines", sid)
        proof_raw = await self._r.get(f"{t}:proof:session:{sid}")
        try:
            proof = json.loads(_s(proof_raw)) if proof_raw else None
        except ValueError:
            proof = None
        meta = {}
        if meta_raw:
            try:
                meta = json.loads(_s(meta_raw)) or {}
            except ValueError:
                meta = {}
        cust_pid = rec.get("customer_participant_id") or ""

        agent_msgs: list[dict] = []
        last_menu: Optional[dict] = None
        last_id = ""
        for eid, fields in entries:
            d = {}
            for k, v in (fields or {}).items():
                ks, vs = _s(k), _s(v)
                try:
                    d[ks] = json.loads(vs)
                except (ValueError, TypeError):
                    d[ks] = vs
            vis = d.get("visibility", "all")
            if vis == "agents_only" or (isinstance(vis, list) and cust_pid not in vis):
                continue
            typ = d.get("type")
            author = d.get("author")
            role = author.get("role") if isinstance(author, dict) else d.get("author_role")
            if typ == "interaction_request":
                payload = d.get("payload") if isinstance(d.get("payload"), dict) else {}
                last_menu = payload
                agent_msgs.append({"kind": "menu", "payload": payload, "ts": str(d.get("timestamp") or ""),
                                   "id": _s(eid)})
                last_id = _s(eid)
            elif typ == "message" and role != "customer":
                payload = d.get("payload") if isinstance(d.get("payload"), dict) else {}
                content = payload.get("content") if isinstance(payload.get("content"), dict) else d.get("content")
                if isinstance(content, str):
                    try:
                        content = json.loads(content)
                    except ValueError:
                        content = {"type": "text", "text": content}
                if isinstance(content, dict) and content.get("type") == "text" and content.get("text"):
                    agent_msgs.append({"kind": "text", "text": str(content["text"]),
                                       "ts": str(d.get("timestamp") or ""), "id": _s(eid)})
                    last_id = _s(eid)

        result = None
        if result_raw:
            try:
                result = json.loads(_s(result_raw))
            except ValueError:
                result = None

        state, reason = self._state(result, closed, meta, waiting, cust_pid, agent_msgs, proof)
        pending_menu = None
        if state in INTERRUPTED and last_menu and str(last_menu.get("menu_id") or "") not in answered:
            pending_menu = last_menu
        return {"state": state, "reason": reason, "result": result, "closed": closed,
                "agent_msgs": agent_msgs, "inbox": inbox, "pending_menu": pending_menu,
                "last_stream_id": last_id, "input_deadline_ms": deadline,
                "proof": proof if state == AUTH_REQUIRED else None}

    @staticmethod
    def _state(result: Optional[dict], closed: str, meta: dict, waiting: dict, cust_pid: str,
               agent_msgs: list, proof: Optional[dict] = None) -> tuple[str, str]:
        # O PRIMEIRO motivo de fim (`closed_recorded`, "a primeira causa vence") manda sobre um
        # resultado: se a sessão fechou por cancelamento/recurso/prazo, o `complete` que o fluxo
        # ainda executou DEPOIS (o menu acordado pelo fechamento sai por on_timeout/on_failure)
        # não é o desfecho da task. Medido no CancelTask: sem esta ordem, a task cancelada virava
        # FAILED quando o menu estacionado vencia o prazo.
        if closed and closed not in CLOSE_DEFERS_TO_RESULT:
            return CLOSE_TO_STATE.get(closed, FAILED), closed
        if result is not None:
            c = result.get("contract") or {}
            if c.get("checked") and not c.get("valid"):
                return FAILED, "contract_invalid"
            if c.get("reason") in ("result_missing", "schema_invalid"):
                return FAILED, str(c.get("reason"))
            if result.get("outcome") != "resolved":
                return FAILED, f"outcome:{result.get('outcome')}"
            return COMPLETED, "complete"
        if closed:
            return CLOSE_TO_STATE.get(closed, FAILED), closed
        for _field, raw in (waiting or {}).items():
            try:
                vis = json.loads(_s(raw)).get("visibility", "all")
            except (ValueError, AttributeError):
                vis = "all"
            if (vis == "all" or (isinstance(vis, list) and cust_pid in vis)) and agent_msgs:
                # o menu que espera com um link de prova pendente espera a PESSOA, fora de banda
                if proof:
                    return AUTH_REQUIRED, "identity_proof"
                return INPUT_REQUIRED, "menu"
        if meta.get("instance_id"):
            return WORKING, "allocated"
        if meta:
            return SUBMITTED, "queued"
        return UNSPECIFIED, "no_fact"

    def _task_json(self, sid: str, rec: dict, view: dict, want_json: bool, history_length: Any) -> dict:
        ctx = rec["context_id"]

        def msg(parts: list[dict], role: str = "ROLE_AGENT", mid: str = "") -> dict:
            return {"messageId": mid or str(uuid.uuid4()), "contextId": ctx, "taskId": sid,
                    "role": role, "parts": parts}

        def agent_parts(m: dict) -> list[dict]:
            if m["kind"] == "text":
                return [{"text": m["text"]}]
            parts = [{"text": render_prompt(m["payload"])}]
            schema = response_schema(m["payload"])
            if want_json and schema is not None:
                parts.append({"data": {"menu_id": m["payload"].get("menu_id"),
                                       "interaction": m["payload"].get("interaction"),
                                       "response_schema": schema}, "mediaType": JSON_MT})
            return parts

        state, reason = view["state"], view["reason"]
        status: dict = {"state": state, "timestamp": _now()}
        meta: dict = {"plughub": {"reason": reason}}
        if state in INTERRUPTED and view["agent_msgs"]:
            last = view["agent_msgs"][-1]
            status["message"] = msg(agent_parts(last), mid=last["id"])
            proof = view.get("proof")
            if state == AUTH_REQUIRED and proof:
                # spec § 7.6.1: o status EXPLICA a autorização pedida. O link é o do fato, não o
                # que o autor do fluxo lembrou de pôr no prompt — se ele não pôs, a plataforma põe
                url = str(proof.get("url") or "")
                if url and not any(url in str(p.get("text") or "") for p in status["message"]["parts"]):
                    status["message"]["parts"].append({"text": (
                        "Para continuar, a pessoa precisa confirmar a identidade neste link, no "
                        f"navegador dela: {url}")})
                if want_json:
                    status["message"]["parts"].append({"data": {"authorization": {
                        "type": "identity_proof", "url": url, "mechanism": proof.get("mechanism"),
                        "anchor_hint": proof.get("anchor_hint"), "expires_at": proof.get("expires_at"),
                    }}, "mediaType": JSON_MT})
                meta["plughub"]["auth_deadline"] = proof.get("expires_at")
            if view["input_deadline_ms"]:
                meta["plughub"]["input_deadline"] = datetime.fromtimestamp(
                    float(view["input_deadline_ms"]) / 1000, tz=timezone.utc).isoformat()
        elif state in (FAILED, REJECTED, CANCELED):
            texto = {"contract_invalid": "o resultado não cumpre o output_schema do agente",
                     "caller_cancel": "cancelada pelo chamador",
                     "no_resource": "sem recurso para atender"}.get(reason, f"encerrada: {reason}")
            status["message"] = msg([{"text": texto}])
        task: dict = {"id": sid, "contextId": ctx, "status": status, "metadata": meta}

        result = view["result"]
        if result is not None:
            meta["plughub"]["contract"] = result.get("contract")
            if result.get("issue_status"):
                meta["plughub"]["issue_status"] = result["issue_status"]
            if state == FAILED and (result.get("contract") or {}).get("errors"):
                status["message"]["parts"].append(
                    {"data": {"errors": result["contract"]["errors"]}, "mediaType": JSON_MT})
            res = result.get("result")
            if state == COMPLETED and isinstance(res, dict) and "value" in res and want_json:
                task["artifacts"] = [{"artifactId": f"{sid}:result", "name": str(res.get("from") or "result"),
                                      "parts": [{"data": res["value"], "mediaType": JSON_MT}]}]

        pares: list[tuple[float, dict]] = []
        for x in view["inbox"]:
            m = {k: v for k, v in x.items() if k != "_ts"}
            pares.append((_ts(str(x.get("_ts") or "")), m))
        for m in view["agent_msgs"]:
            pares.append((_ts(m["ts"]), msg(agent_parts(m), mid=m["id"])))
        pares.sort(key=lambda p: p[0])     # sort estável: empate mantém pedido antes da resposta
        hist = [m for _, m in pares]
        try:
            n = None if history_length is None else max(0, int(history_length))
        except (TypeError, ValueError):
            n = None
        if n is None:
            task["history"] = hist
        elif n > 0:
            task["history"] = hist[-n:]
        return task
