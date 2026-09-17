"""
server.py — o processo `speech-check` (VOZ-23): recebe o pedido, roda UMA verificação por vez por tenant e
publica o resultado em `speech.metrics`.

  POST /v1/speech-checks        {tenant_id, speech_profile_id?, requested_by} → 202 {check_id}
  GET  /v1/speech-checks/{id}   estado em memória (running|completed|failed + resultado)
  GET  /health

Credencial: `x-service-token` igual a `PLUGHUB_SPEECH_CHECK_SERVICE_TOKEN`. Token não configurado RECUSA
tudo (503) — nunca libera. O estado em memória some num restart; o fato durável é o evento no Kafka.

Recusas síncronas (antes de abrir chamada): tenant que não é o desta instalação (422), id de perfil
malformado (422), perfil que não existe (422, o mesmo `profile_not_found` do runner), verificação já em
curso no tenant (409).

⚠️ Das quatro, só a de 409 vira EVENTO (VOZ-27), e a assimetria é deliberada: as três primeiras são
pedido malformado, cujo autor recebe o erro na hora e o corrige; a de 409 é pedido CORRETO que a
plataforma escolheu não executar, e quem a recebe pode ser uma Agenda de madrugada. Registrar as
outras seria arquivar erro de digitação; não registrar esta é deixar a verificação pulada invisível.
"""
from __future__ import annotations

import asyncio
import hmac
import json
import logging
import re
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from plughub_tasks import disparar

from ..config import Settings
from .. import speech_metrics
from .live import LiveDeps
from .runner import CheckRequest, refusal_event, run_check

logger = logging.getLogger("plughub.speech-check")

PROFILE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class CheckBody(BaseModel):
    tenant_id:         str | None = None
    speech_profile_id: str | None = None
    requested_by:      str = Field(min_length=1, max_length=200)


class State:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.checks: dict[str, dict] = {}
        self.running: dict[str, str] = {}      # tenant → check_id
        self.http: httpx.AsyncClient | None = None
        self.producer = None


def build_app(settings: Settings | None = None, *, deps_factory=None, producer=None) -> FastAPI:
    s = settings or Settings()
    st = State(s)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        st.http = httpx.AsyncClient(timeout=60)
        if producer is not None:
            st.producer = producer
        else:
            from aiokafka import AIOKafkaProducer
            st.producer = AIOKafkaProducer(bootstrap_servers=s.kafka_brokers)
            await st.producer.start()
        if not s.speech_check_service_token:
            logger.error("speech-check: PLUGHUB_SPEECH_CHECK_SERVICE_TOKEN vazio — toda requisicao sera RECUSADA (503)")
        yield
        if producer is None:
            await st.producer.stop()
        await st.http.aclose()

    app = FastAPI(title="speech-check", lifespan=lifespan)
    app.state.st = st
    make_deps = deps_factory or (lambda: LiveDeps(s, st.http))

    def _auth(token: str | None) -> None:
        if not s.speech_check_service_token:
            raise HTTPException(503, "speech-check sem credencial configurada")
        if not token or not hmac.compare_digest(token, s.speech_check_service_token):
            raise HTTPException(401, "x-service-token ausente ou invalido")

    async def _executa(req: CheckRequest) -> None:
        try:
            ev = await run_check(req, make_deps(), pool_id=s.speech_check_pool_id,
                                 default_language=s.voice_stt_language)
            st.checks[req.check_id] = {"check_id": req.check_id, "status": ev["status"], "result": ev}
            ok = await speech_metrics.publish(st.producer, ev, key=req.check_id)
            if not ok:
                st.checks[req.check_id]["publish_failed"] = True
        finally:
            st.running.pop(req.tenant_id, None)

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    @app.post("/v1/speech-checks", status_code=202)
    async def criar(body: CheckBody, x_service_token: str | None = Header(default=None)) -> dict:
        _auth(x_service_token)
        tenant = body.tenant_id or s.tenant_id
        if tenant != s.tenant_id:
            raise HTTPException(422, f"tenant {tenant!r} nao e o desta instalacao ({s.tenant_id!r})")
        perfil = body.speech_profile_id or None
        if perfil is not None:
            if not PROFILE_ID_RE.fullmatch(perfil):
                raise HTTPException(422, f"speech_profile_id {perfil!r} malformado")
            try:
                perfis = await make_deps().get_profiles(tenant)
            except Exception as exc:  # noqa: BLE001 — dito ao chamador
                raise HTTPException(503, f"config-api indisponivel para conferir o perfil: {exc}") from exc
            if not isinstance(perfis.get(perfil), dict):
                raise HTTPException(422, {"reason": "profile_not_found", "speech_profile_id": perfil})
        if tenant in st.running:
            # VOZ-27 — a recusa VIRA LINHA no histórico, além do 409. Quem pede periodicamente é a
            # Agenda, e o corpo da resposta dela não é lido por ninguém: sem este registro, a
            # verificação pulada some, e "não mediu" fica com a cara de "mediu e está bom".
            em_curso = st.running[tenant]
            recusado = CheckRequest(tenant_id=tenant, speech_profile_id=perfil,
                                    requested_by=body.requested_by)
            ev = refusal_event(recusado, pool_id=s.speech_check_pool_id, reason="check_running")
            st.checks[recusado.check_id] = {"check_id": recusado.check_id, "status": "failed",
                                            "failure_reason": "check_running", "result": ev}
            if not await speech_metrics.publish(st.producer, ev, key=recusado.check_id):
                st.checks[recusado.check_id]["publish_failed"] = True
            logger.info("speech-check RECUSADA a %s: %s ja corre (registrada como %s)",
                        body.requested_by, em_curso, recusado.check_id)
            raise HTTPException(409, {"reason": "check_running", "check_id": em_curso,
                                      "recorded_as": recusado.check_id})
        req = CheckRequest(tenant_id=tenant, speech_profile_id=perfil, requested_by=body.requested_by)
        st.running[tenant] = req.check_id
        st.checks[req.check_id] = {"check_id": req.check_id, "status": "running"}
        logger.info("speech-check %s pedida por %s (perfil %r)", req.check_id, body.requested_by, perfil)
        disparar(_executa(req), nome=f"speech-check-{req.check_id[:8]}")
        return {"check_id": req.check_id, "status": "running", "speech_profile_id": perfil}

    @app.get("/v1/speech-checks")
    async def listar(x_service_token: str | None = Header(default=None)) -> dict:
        """O que está em curso AGORA. Sem isto, "ocupado" só aparecia como 409 no pedido seguinte — e
        quem dispara periodicamente não distingue "recusado porque já corre" de "recusado por defeito"."""
        _auth(x_service_token)
        return {"running": st.running.get(s.tenant_id), "checks": list(st.checks.values())}

    @app.get("/v1/speech-checks/{check_id}")
    async def ler(check_id: str, x_service_token: str | None = Header(default=None)) -> dict:
        _auth(x_service_token)
        if check_id not in st.checks:
            raise HTTPException(404, "verificacao desconhecida nesta execucao do processo")
        return st.checks[check_id]

    return app


def main() -> None:
    import uvicorn
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    s = Settings()
    uvicorn.run(build_app(s), host="0.0.0.0", port=s.speech_check_port)


if __name__ == "__main__":
    main()
