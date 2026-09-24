"""
webrtc_recording.py — gravação da chamada, por PARTES (VOZ-06).

O que grava e quando — decisões do dono (2026-09-18):

  • SÓ ÁUDIO, a sala MISTURADA (cliente, atendente e a voz do bot) — um arquivo OGG por parte;
  • liga pelo POOL: `media_policy.recording` do pool de quem atende. Grava enquanto houver na
    chamada ao menos um atendente de pool que grava;
  • o FLUXO pergunta e a plataforma HONRA: a tag `core.contact.recording_opt_out` no ContextStore.

Uma sessão tem UMA gravação por vez (o egress grava a sala inteira; duas seriam o mesmo áudio em
dobro), cortada em PARTES quando:

  • o conjunto de pools que gravam muda (transferência, especialista) — cada parte diz de quais
    pools ela é, e é assim que a gravação continua sendo "por segmento" sem duplicar áudio;
  • começa um bloco de coleta MASCARADA (NIV-07): a parte fecha ANTES do prompt, e a próxima só
    começa quando o bloco termina. O bloco nunca é gravado;
  • a chamada termina.

Invariantes, cada um com o mecanismo aqui:

  • SEM AVISO ENTREGUE, NÃO SE GRAVA. O aviso sai por texto (se há WebSocket do cliente) e por voz
    (se há voz na chamada); nenhum dos dois ⇒ `recording.skipped` (notice_undeliverable). Falado,
    a gravação só começa depois de ele TOCAR. Uma vez por sessão — repetir a cada transferência é
    ruído para quem já foi avisado.
  • RECUSA HONRADA ATÉ O FIM. A tag é conferida antes de cada parte e a cada 2 s durante ela;
    recusa no meio DESCARTA a parte em curso (o que foi gravado antes da decisão não fica) e
    nenhuma parte nova começa. Tag ilegível antes de começar ⇒ não começa.
  • NUNCA FINGE GRAVAR. Egress que não começa, que não termina ou arquivo que não se guarda viram
    `recording.failed` com o motivo, no stream e no log. E a FAIXA do widget só acende com o egress
    CONFIRMADO pelo SFU (`EGRESS_ACTIVE`), não com o egress PEDIDO (VOZ-44 — medido: sem ninguém
    publicando áudio, o egress fica em `STARTING` a parte inteira e só vira `ABORTED` no `stop`;
    a faixa dizia "Gravando" enquanto nada gravava). Parte que não confirma em
    `EGRESS_STARTING_WARN_S` apaga a faixa e é dita; egress que termina sozinho depois de ativo
    fecha a parte como `recording.failed`.
  • O CLIENTE VÊ O ESTADO, NÃO SÓ O AVISO (VOZ-39). O aviso é mensagem de chat, que rola; o
    estado (`recording` · `paused` · `stopped`) vai ao widget por `on_state` a cada MUDANÇA,
    calculado DEPOIS de cada entrada — a troca de atendentes que corta uma parte e começa a
    próxima não pisca, e `paused` só existe se já houve gravação (o bloco mascarado).
  • A GRAVAÇÃO NÃO SAI PELA PORTA PÚBLICA DE ANEXOS: é guardada como `call_recording`, com a
    retenção da classe (`storage.call_recording_retention_days`), e `upload_router` a recusa.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from plughub_tasks import disparar

from .. import recording_config
from ..recording_config import RecordingPolicy

if TYPE_CHECKING:
    from ..attachment_store import AttachmentStore

logger = logging.getLogger("plughub.channel-gateway.webrtc.recording")

OPT_OUT_TAG = "core.contact.recording_opt_out"
# Valores que NÃO são recusa. Qualquer outro valor presente É — na dúvida sobre a vontade do
# cliente, não se grava (e o log diz qual valor foi lido).
_NOT_OPT_OUT = {"", "false", "0", "nao", "não", "no", "n", "null", "none", "undefined"}
_EXPLICIT_OPT_OUT = {"true", "1", "sim", "yes", "s", "y"}

ARTIFACT_CLASS = "call_recording"
MIME = "audio/ogg"
OPT_OUT_POLL_S = 2.0
NOTICE_PLAY_TIMEOUT_S = 30.0
EGRESS_END_TIMEOUT_S = 60.0
# VOZ-44: de quanto em quanto o gravador pergunta ao SFU pelo egress da parte — rápido até ele
# CONFIRMAR que grava (a faixa espera por isso), depois só para ver se ainda vive
EGRESS_POLL_FAST_S = 1.0
EGRESS_POLL_S = 5.0
# parte que não confirmou nisso apaga a faixa (medido: com áudio na sala, o egress fica ativo em
# ~2-3 s; sem ninguém publicando, fica em STARTING até o stop)
EGRESS_STARTING_WARN_S = 30.0
_EGRESS_FINAL = {"EGRESS_COMPLETE", "EGRESS_FAILED", "EGRESS_ABORTED", "EGRESS_LIMIT_REACHED"}
# O egress roda com outro usuário (uid 1001, gid 0 — medido): o diretório da sessão tem de ser
# gravável pelo GRUPO, e só por ele.
_DIR_MODE = 0o770


@dataclass
class _Part:
    index:      int
    egress_id:  str
    filepath:   str
    pools:      tuple[str, ...]
    policy:     RecordingPolicy
    started_at: datetime
    watcher:    asyncio.Task | None = None
    egress_watch: asyncio.Task | None = None
    sfu_ended:  str = ""        # VOZ-44: o egress terminou SOZINHO durante a parte (status: motivo)
    active:     bool = False    # VOZ-44: o SFU confirmou EGRESS_ACTIVE — só então a faixa diz "Gravando"
    stale:      bool = False    # VOZ-44: não confirmou em EGRESS_STARTING_WARN_S


@dataclass
class _Rec:
    room:      str
    demand:    tuple[str, ...] = ()
    part:      _Part | None = None
    parts:     int = 0
    noticed:   bool = False
    opted_out: bool = False
    skipped:   set[str] = field(default_factory=set)
    held:      bool = False
    closed:    bool = False
    shown:     str = "stopped"      # VOZ-39: o último estado anunciado ao cliente
    lock:      asyncio.Lock = field(default_factory=asyncio.Lock)


class CallRecorder:
    def __init__(
        self,
        *,
        redis:          Any,
        tenant_id:      str,
        output_dir:     str,
        config_api_url: str,
        default_notice: str,
        provider:       Callable[[], Any],
        store:          Callable[[], Any],
        speak:          Callable[[str, str, asyncio.Event | None], None],
        can_speak:      Callable[[str], bool],
        send_text:      Callable[[str, str], Awaitable[bool]],
        policy:         Callable[[str], Awaitable[RecordingPolicy]] | None = None,
        on_state:       Callable[[str, str], Awaitable[None]] | None = None,
    ) -> None:
        self._redis = redis
        self._tenant = tenant_id
        self._dir = Path(output_dir)
        self._provider = provider
        self._store = store
        self._speak = speak
        self._can_speak = can_speak
        self._send_text = send_text
        self._policy = policy or (lambda t: recording_config.resolve(config_api_url, t, default_notice))
        self._on_state = on_state
        self._recs: dict[str, _Rec] = {}
        self._finalizers: set[asyncio.Task] = set()

    # ── Entradas ────────────────────────────────────────────────────────────────

    async def update(self, session_id: str, room: str, pools: set[str]) -> None:
        """O conjunto de pools QUE GRAVAM entre os atendentes mudou (entrou, saiu, transferiu)."""
        demand = tuple(sorted(p for p in pools if p))
        rec = self._recs.get(session_id)
        if rec is None:
            if not demand:
                return
            rec = self._recs[session_id] = _Rec(room=room)
        async with rec.lock:
            if rec.closed:
                return
            rec.room = room or rec.room
            antes, rec.demand = rec.demand, demand
            if rec.part is not None and rec.part.pools != demand:
                motivo = "atendentes que gravam mudaram" if demand else "nenhum atendente que grava na chamada"
                await self._stop(session_id, rec, motivo)
            if demand and rec.part is None:
                if demand != antes:
                    logger.info("webrtc gravacao: pools que gravam %s (session=%s)", list(demand), session_id)
                await self._start(session_id, rec)
            await self._announce(session_id, rec)

    async def hold(self, session_id: str) -> None:
        """Começa um bloco que NÃO pode ser gravado (coleta mascarada, NIV-07). Volta quando a
        parte em curso JÁ FOI pedida para parar — é antes do prompt que isto tem de valer."""
        rec = self._recs.get(session_id)
        if rec is None:
            return
        async with rec.lock:
            rec.held = True
            if rec.part is not None:
                await self._stop(session_id, rec, "bloco mascarado (NIV-07) — nao e gravado")
            await self._announce(session_id, rec)

    async def release(self, session_id: str) -> None:
        rec = self._recs.get(session_id)
        if rec is None:
            return
        async with rec.lock:
            rec.held = False
            if rec.demand and rec.part is None and not rec.closed:
                await self._start(session_id, rec)
            await self._announce(session_id, rec)

    async def close(self, session_id: str) -> None:
        rec = self._recs.pop(session_id, None)
        if rec is None:
            return
        async with rec.lock:
            rec.closed = True
            if rec.part is not None:
                await self._stop(session_id, rec, "chamada encerrada")
            await self._announce(session_id, rec)

    async def drain(self) -> None:
        """Espera as partes em finalização (teste e desligamento)."""
        while self._finalizers:
            await asyncio.gather(*list(self._finalizers), return_exceptions=True)

    def active(self, session_id: str) -> bool:
        rec = self._recs.get(session_id)
        return bool(rec and rec.part)

    # ── Começar ─────────────────────────────────────────────────────────────────

    async def _start(self, session_id: str, rec: _Rec) -> None:
        """Chamado com o lock. Nada aqui começa a gravar sem aviso entregue e sem consentimento lido."""
        if rec.held:
            logger.info("webrtc gravacao: em bloco mascarado — a proxima parte comeca quando ele terminar "
                        "(session=%s)", session_id)
            return
        if rec.opted_out:
            return
        recusa = await self._opt_out(session_id)
        if recusa is None:
            await self._skip(session_id, rec, "opt_out_unreadable",
                             f"a recusa do cliente ({OPT_OUT_TAG}) nao pode ser lida — sem saber se ele "
                             "aceitou, NAO se grava")
            return
        if recusa:
            rec.opted_out = True
            await self._skip(session_id, rec, "opt_out", f"o cliente recusou a gravacao ({OPT_OUT_TAG})")
            return
        provider = self._provider()
        if provider is None:
            await self._fail(session_id, None, "start", "sem plano de midia — a gravacao NAO comecou")
            return
        policy = await self._policy(self._tenant)
        if not rec.noticed:
            entregue = await self._notice(session_id, policy.notice)
            if entregue is None:
                await self._skip(session_id, rec, "notice_undeliverable",
                                 "o aviso de gravacao nao chegou ao cliente (sem texto nem voz na chamada) "
                                 "— sem aviso, NAO se grava")
                return
            rec.noticed = True
            logger.info("webrtc gravacao: aviso entregue por %s (procedencia do texto: %s) (session=%s)",
                        entregue, policy.provenance.get("notice", "?"), session_id)
            # a recusa pode ter sido gravada enquanto o aviso tocava — e o estado pode ter mudado
            recusa = await self._opt_out(session_id)
            if recusa or recusa is None:
                if recusa:
                    rec.opted_out = True
                await self._skip(session_id, rec, "opt_out" if recusa else "opt_out_unreadable",
                                 "recusa lida depois do aviso — NAO se grava")
                return
            if rec.held or rec.closed or not rec.demand:
                return
        n = rec.parts + 1
        sdir = self._dir / session_id
        try:
            sdir.mkdir(parents=True, exist_ok=True)
            os.chmod(sdir, _DIR_MODE)
        except OSError as exc:
            await self._fail(session_id, n, "start", f"diretorio de rascunho {sdir} indisponivel ({exc})")
            return
        filepath = str(sdir / f"p{n:03d}-{uuid.uuid4().hex[:8]}.ogg")
        try:
            egress_id = await provider.start_egress(rec.room, filepath)
        except Exception as exc:  # noqa: BLE001 — dito como falha, com o motivo
            await self._fail(session_id, n, "start", f"o egress NAO comecou: {type(exc).__name__}: {exc}")
            return
        rec.parts = n
        part = _Part(index=n, egress_id=egress_id, filepath=filepath, pools=rec.demand,
                     policy=policy, started_at=datetime.now(timezone.utc))
        rec.part = part
        part.watcher = disparar(self._watch_opt_out(session_id, rec, part),
                                nome=f"webrtc-gravacao-recusa-{session_id[:8]}")
        part.egress_watch = disparar(self._watch_egress(session_id, rec, part),
                                     nome=f"webrtc-gravacao-egress-{session_id[:8]}")
        await self._state(session_id, part)
        logger.info("webrtc gravacao: parte %d INICIADA egress=%s pools=%s retencao=%d dias (%s) "
                    "(session=%s)", n, egress_id, list(part.pools), policy.retention_days,
                    policy.provenance.get("retention_days", "?"), session_id)

    async def _notice(self, session_id: str, text: str) -> str | None:
        """Entrega o aviso. Devolve por onde chegou ('texto', 'voz', 'texto+voz') ou None."""
        canais = []
        if await self._send_text(session_id, text):
            canais.append("texto")
        if self._can_speak(session_id):
            tocou = asyncio.Event()
            self._speak(session_id, text, tocou)
            try:
                await asyncio.wait_for(tocou.wait(), NOTICE_PLAY_TIMEOUT_S)
                canais.append("voz")
            except asyncio.TimeoutError:
                logger.error("webrtc gravacao: o aviso falado nao terminou em %.0f s (session=%s)",
                             NOTICE_PLAY_TIMEOUT_S, session_id)
        return "+".join(canais) or None

    # ── Parar e guardar ─────────────────────────────────────────────────────────

    async def _stop(self, session_id: str, rec: _Rec, motivo: str, discard: bool = False) -> None:
        """Com o lock. Pede o fim ao SFU AGORA e guarda o arquivo em segundo plano."""
        part, rec.part = rec.part, None
        if part is None:
            return
        for t in (part.watcher, part.egress_watch):
            if t is not None and t is not asyncio.current_task():
                t.cancel()
        provider = self._provider()
        if provider is not None and not part.sfu_ended:     # o que já terminou não se pede para parar
            try:
                await provider.stop_egress(part.egress_id)
            except Exception as exc:  # noqa: BLE001 — a finalização descobre como terminou
                logger.warning("webrtc gravacao: stop_egress de %s falhou (%s) (session=%s)",
                               part.egress_id, exc, session_id)
        await self._state(session_id, None)
        logger.info("webrtc gravacao: parte %d PARADA — %s%s (session=%s)", part.index, motivo,
                    " — sera DESCARTADA" if discard else "", session_id)
        t = disparar(self._finalize(session_id, part, discard, motivo),
                     nome=f"webrtc-gravacao-fim-{session_id[:8]}-{part.index}")
        self._finalizers.add(t)
        t.add_done_callback(self._finalizers.discard)

    async def _finalize(self, session_id: str, part: _Part, discard: bool, motivo: str) -> None:
        provider = self._provider()
        ended = datetime.now(timezone.utc)
        if provider is None:
            await self._fail(session_id, part.index, "finalize", "sem plano de midia para ler o fim do egress")
            return
        try:
            res = await provider.wait_egress(part.egress_id, EGRESS_END_TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001
            await self._fail(session_id, part.index, "finalize", f"fim do egress ilegivel: {exc}")
            self._cleanup(part.filepath)
            return
        if part.sfu_ended and not res.complete:
            # VOZ-44: o cliente viu "Gravando" e o SFU parou sozinho — é falha, nunca "parte vazia"
            await self._fail(session_id, part.index, "egress",
                             f"o egress {part.egress_id} terminou DURANTE a parte ({part.sfu_ended}) — "
                             "nada guardado")
            self._cleanup(res.filename or part.filepath)
            return
        if discard:
            self._cleanup(res.filename or part.filepath)
            await self._event(session_id, "recording.discarded", part=part.index, reason=motivo)
            logger.info("webrtc gravacao: parte %d DESCARTADA — %s (session=%s)", part.index, motivo, session_id)
            return
        if res.status == "EGRESS_ABORTED" and not res.filename:
            # parte curta: parada antes de o egress começar a gravar (ele leva ~2-3 s, medido).
            # Nada foi gravado — não é falha, e dizer "falhou" aqui treinaria a ignorar o alarme.
            await self._event(session_id, "recording.discarded", part=part.index, reason="empty")
            logger.info("webrtc gravacao: parte %d VAZIA — parada (%s) antes de o egress gravar "
                        "(session=%s)", part.index, motivo, session_id)
            return
        if not res.complete:
            await self._fail(session_id, part.index, "finalize",
                             f"egress {part.egress_id} terminou {res.status}: {res.error or 'sem arquivo'}")
            self._cleanup(res.filename or part.filepath)
            return
        # o SFU diz onde gravou; só se lê dentro do diretório de rascunho
        caminho = Path(res.filename).resolve()
        if self._dir.resolve() not in caminho.parents:
            await self._fail(session_id, part.index, "finalize",
                             f"o egress diz ter gravado FORA do rascunho ({caminho}) — nao lido")
            return
        try:
            dados = caminho.read_bytes()
        except OSError as exc:
            await self._fail(session_id, part.index, "finalize", f"arquivo {caminho} ilegivel ({exc})")
            return
        # anotado: é a forma que o censo `probe_adapter_self_calls` liga ao contrato do store
        store: AttachmentStore | None = self._store()
        if store is None:
            await self._fail(session_id, part.index, "store",
                             "gravacao feita e NAO guardada — sem AttachmentStore; o rascunho foi apagado")
            self._cleanup(str(caminho))
            return
        expira = ended + timedelta(days=part.policy.retention_days)
        try:
            fid, _ = await store.reserve(
                tenant_id=self._tenant, session_id=session_id,
                file_name=f"recording-{session_id[:8]}-p{part.index:03d}.ogg", mime_type=MIME,
                size_bytes=len(dados), expires_at=expira, artifact_class=ARTIFACT_CLASS,
                # VOZ-36: quem pode OUVIR é decidido pelos pools que atenderam ESTA parte
                attrs={"part": part.index, "pools": list(part.pools), "duration_ms": res.duration_ms,
                       "started_at": part.started_at.isoformat(), "ended_at": ended.isoformat()},
            )
            meta = await store.commit(file_id=fid, tenant_id=self._tenant, data=dados)
        except Exception as exc:  # noqa: BLE001
            await self._fail(session_id, part.index, "store", f"AttachmentStore recusou: {exc}")
            return
        self._cleanup(str(caminho))
        await self._event(
            session_id, "recording.completed",
            part=part.index, file_id=getattr(meta, "file_id", fid), artifact_class=ARTIFACT_CLASS,
            mime_type=MIME, size_bytes=len(dados), duration_ms=res.duration_ms,
            pools=",".join(part.pools), started_at=part.started_at.isoformat(), ended_at=ended.isoformat(),
            expires_at=expira.isoformat(), retention_source=part.policy.provenance.get("retention_days", "?"),
        )
        logger.info("webrtc gravacao: parte %d GUARDADA file_id=%s %d bytes %d ms, expira %s (session=%s)",
                    part.index, getattr(meta, "file_id", fid), len(dados), res.duration_ms,
                    expira.date().isoformat(), session_id)

    def _cleanup(self, filepath: str) -> None:
        """Apaga o rascunho e o manifesto que o egress deixa ao lado (`EG_*.json`)."""
        if not filepath:
            return
        p = Path(filepath)
        try:
            p.unlink(missing_ok=True)
            for m in p.parent.glob("EG_*.json"):
                m.unlink(missing_ok=True)
            p.parent.rmdir()
        except OSError:
            pass   # diretório com outra parte em andamento: fica para ela

    # ── Recusa (opt-out) ────────────────────────────────────────────────────────

    async def _opt_out(self, session_id: str) -> bool | None:
        """True = recusou · False = não recusou · None = ilegível."""
        try:
            raw = await self._redis.hget(f"{self._tenant}:ctx:{session_id}", OPT_OUT_TAG)
        except Exception as exc:  # noqa: BLE001
            logger.error("webrtc gravacao: %s ilegivel (%s) (session=%s)", OPT_OUT_TAG, exc, session_id)
            return None
        if not raw:
            return False
        try:
            entry = json.loads(raw)
            val = entry.get("value") if isinstance(entry, dict) else entry
        except (TypeError, ValueError):
            val = raw
        texto = str(val if val is not None else "").strip().lower()
        if texto in _NOT_OPT_OUT:
            return False
        if texto not in _EXPLICIT_OPT_OUT:
            logger.warning("webrtc gravacao: %s com valor %r — nao e 'false', vale como RECUSA (session=%s)",
                           OPT_OUT_TAG, val, session_id)
        return True

    async def _watch_opt_out(self, session_id: str, rec: _Rec, part: _Part) -> None:
        while True:
            await asyncio.sleep(OPT_OUT_POLL_S)
            recusa = await self._opt_out(session_id)
            if not recusa:
                continue   # None: leitura falhou agora; já gravando, não se inventa recusa — próxima volta
            async with rec.lock:
                if rec.part is not part:
                    return
                rec.opted_out = True
                await self._stop(session_id, rec, "o cliente recusou a gravacao durante a parte", discard=True)
                await self._announce(session_id, rec)
            return

    async def _watch_egress(self, session_id: str, rec: _Rec, part: _Part) -> None:
        """VOZ-44 — o egress da parte GRAVA? Até o SFU confirmar (`EGRESS_ACTIVE`) a faixa não acende;
        sem confirmação em `EGRESS_STARTING_WARN_S` ela apaga e isso é dito. Terminou sozinho: a
        parte fecha AGORA e o fim é falha. Não se reabre parte aqui: a próxima nasce do próximo
        fato (atendente, fim de bloco)."""
        t0 = asyncio.get_running_loop().time()
        sem_leitura = False
        while True:
            await asyncio.sleep(EGRESS_POLL_S if part.active else EGRESS_POLL_FAST_S)
            if (not part.active and not part.stale
                    and asyncio.get_running_loop().time() - t0 >= EGRESS_STARTING_WARN_S):
                async with rec.lock:
                    if rec.part is not part:
                        return
                    part.stale = True
                    logger.warning("webrtc gravacao: o egress %s da parte %d NAO confirmou que grava em "
                                   "%.0f s — a faixa do widget apaga (motivo usual: ninguem publicando "
                                   "audio na sala) (session=%s)", part.egress_id, part.index,
                                   EGRESS_STARTING_WARN_S, session_id)
                    await self._announce(session_id, rec)
            provider = self._provider()
            if provider is None:
                continue
            try:
                st = await provider.egress_status(part.egress_id)
            except Exception as exc:  # noqa: BLE001 — "não sei" não é "terminou" nem "grava"
                if not sem_leitura:
                    logger.warning("webrtc gravacao: estado do egress %s ILEGIVEL (%s) — sem confirmacao, a "
                                   "faixa nao muda ate a leitura voltar (session=%s)",
                                   part.egress_id, exc, session_id)
                sem_leitura = True
                continue
            if sem_leitura:
                logger.info("webrtc gravacao: estado do egress %s legivel de novo (session=%s)",
                            part.egress_id, session_id)
                sem_leitura = False
            if st.status == "EGRESS_ACTIVE" and not part.active:
                async with rec.lock:
                    if rec.part is not part:
                        return
                    part.active = True
                    logger.info("webrtc gravacao: parte %d GRAVANDO — o SFU confirmou o egress %s em "
                                "%.1f s (session=%s)", part.index, part.egress_id,
                                asyncio.get_running_loop().time() - t0, session_id)
                    await self._announce(session_id, rec)
                continue
            if st.status not in _EGRESS_FINAL:
                continue
            async with rec.lock:
                if rec.part is not part:
                    return
                part.sfu_ended = f"{st.status}: {st.error or 'sem motivo dado'}"
                logger.error("webrtc gravacao: o egress %s da parte %d terminou SOZINHO (%s) — a parte "
                             "fecha e a faixa apaga (session=%s)", part.egress_id, part.index,
                             part.sfu_ended, session_id)
                await self._stop(session_id, rec, f"o egress terminou sozinho ({part.sfu_ended})")
                await self._announce(session_id, rec)
            return

    # ── Estado e eventos ────────────────────────────────────────────────────────

    async def _announce(self, session_id: str, rec: _Rec) -> None:
        """VOZ-39 — o estado que o CLIENTE vê, anunciado só quando muda. Com o lock.

        `paused` é o bloco mascarado DEPOIS de já ter havido aviso (o cliente sabe que era gravado e
        vê que parou); fora disso, parte parada é `stopped`. Falha de entrega não para a gravação —
        o aviso de chat, a condição de gravar, já foi entregue — mas é dita."""
        if rec.part is not None:
            if rec.part.active:
                estado = "recording"
            elif not rec.part.stale:
                return          # parte começando: a faixa fica como está até o SFU confirmar
            else:
                estado = "stopped"
        elif rec.held and rec.noticed and rec.demand and not rec.closed and not rec.opted_out:
            estado = "paused"
        else:
            estado = "stopped"
        if estado == rec.shown:
            return
        rec.shown = estado
        if self._on_state is None:
            return
        try:
            await self._on_state(session_id, estado)
        except Exception as exc:  # noqa: BLE001
            logger.warning("webrtc gravacao: estado %s NAO chegou ao cliente (%s) (session=%s)",
                           estado, exc, session_id)

    async def _skip(self, session_id: str, rec: _Rec, reason: str, frase: str) -> None:
        if reason in rec.skipped:
            return   # uma vez por motivo e sessão — a próxima troca de atendente não repete o evento
        rec.skipped.add(reason)
        logger.warning("webrtc gravacao: NAO gravada — %s (session=%s)", frase, session_id)
        await self._event(session_id, "recording.skipped", reason=reason)

    async def _fail(self, session_id: str, part: int | None, stage: str, frase: str) -> None:
        logger.error("webrtc gravacao: FALHOU (%s%s) — %s (session=%s)", stage,
                     f", parte {part}" if part else "", frase, session_id)
        await self._event(session_id, "recording.failed", stage=stage, reason=frase,
                          **({"part": part} if part else {}))

    async def _state(self, session_id: str, part: _Part | None) -> None:
        """`channel:webrtc:{sid}:recording` — o que está gravando AGORA, para quem observa."""
        chave = f"channel:webrtc:{session_id}:recording"
        try:
            if part is None:
                await self._redis.delete(chave)
            else:
                await self._redis.setex(chave, 4 * 3600, json.dumps({
                    "part": part.index, "egress_id": part.egress_id, "pools": list(part.pools),
                    "started_at": part.started_at.isoformat()}))
        except Exception as exc:  # noqa: BLE001
            logger.warning("webrtc gravacao: estado %s nao gravado (%s)", chave, exc)

    async def _event(self, session_id: str, kind: str, **campos: Any) -> None:
        fields = {"type": kind, "event_id": str(uuid.uuid4()), "session_id": session_id,
                  "timestamp": datetime.now(timezone.utc).isoformat(), "visibility": "agents_only"}
        fields.update({k: str(v) for k, v in campos.items()})
        try:
            await self._redis.xadd(f"session:{session_id}:stream", fields)
        except Exception as exc:  # noqa: BLE001
            logger.error("webrtc gravacao: evento %s NAO foi ao stream (%s) (session=%s)", kind, exc, session_id)
