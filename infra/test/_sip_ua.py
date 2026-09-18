"""
_sip_ua.py — um cliente SIP MÍNIMO para os gates da perna SIP (VOZ-02).

É o "telefone" do teste: faz INVITE com autenticação digest, negocia G.711 µ-law (PCMU), manda
RTP a partir de PCM 8 kHz, recebe o RTP do outro lado medindo energia, responde BYE/OPTIONS do
servidor e desliga com BYE. Existe para o gate não depender de operadora, de imagem de terceiro
nem de rede pública — a pergunta que ele responde é sobre a PLATAFORMA, e um softphone de verdade
só acrescentaria variáveis.

Escopo deliberadamente curto: UDP, um diálogo por instância, sem re-INVITE com mudança de mídia,
sem SRTP. O que não entende, loga — nunca finge que entendeu.

Teclas (VOZ-31): por padrão oferece `telephone-event` e tecla FORA de banda (RFC 4733, `teclar`).
Com `dtmf_fora_de_banda=False` NÃO oferece — é o telefone velho, que só sabe mandar o tom dentro do
áudio (`tons_dtmf_pcm8k` + `falar_pcm8k`): o controle negativo do que o conversor faz sem negociação.
"""
from __future__ import annotations

import asyncio
import audioop  # noqa: DEP — a imagem é Python 3.11 (o gateway usa o mesmo módulo)
import hashlib
import math
import os
import random
import re
import socket
import struct
import time
import uuid
from dataclasses import dataclass, field

CRLF = "\r\n"


def _md5(s: str) -> str:
    return hashlib.md5(s.encode()).hexdigest()


@dataclass
class Resposta:
    status: int
    reason: str
    headers: dict[str, list[str]]
    body: str

    def h(self, nome: str) -> str:
        v = self.headers.get(nome.lower()) or [""]
        return v[0]


@dataclass
class Pedido:
    method: str
    uri: str
    headers: dict[str, list[str]]
    body: str

    def h(self, nome: str) -> str:
        v = self.headers.get(nome.lower()) or [""]
        return v[0]


def _parse(data: bytes):
    texto = data.decode(errors="replace")
    cab, _, corpo = texto.partition(CRLF + CRLF)
    linhas = cab.split(CRLF)
    headers: dict[str, list[str]] = {}
    compactos = {"v": "via", "f": "from", "t": "to", "i": "call-id", "m": "contact",
                 "l": "content-length", "c": "content-type"}
    for ln in linhas[1:]:
        if ":" not in ln:
            continue
        k, v = ln.split(":", 1)
        k = k.strip().lower()
        k = compactos.get(k, k)
        headers.setdefault(k, []).append(v.strip())
    primeira = linhas[0]
    if primeira.startswith("SIP/2.0"):
        partes = primeira.split(" ", 2)
        return Resposta(int(partes[1]), partes[2] if len(partes) > 2 else "", headers, corpo)
    metodo, uri, _ = primeira.split(" ", 2)
    return Pedido(metodo, uri, headers, corpo)


def _digest(www: str, metodo: str, uri: str, user: str, senha: str) -> str:
    campos = dict(re.findall(r'(\w+)="?([^",]+)"?', www))
    realm, nonce = campos.get("realm", ""), campos.get("nonce", "")
    ha1 = _md5(f"{user}:{realm}:{senha}")
    ha2 = _md5(f"{metodo}:{uri}")
    qop = campos.get("qop", "")
    if "auth" in qop.split(","):
        nc, cnonce = "00000001", uuid.uuid4().hex[:16]
        resp = _md5(f"{ha1}:{nonce}:{nc}:{cnonce}:auth:{ha2}")
        extra = f', qop=auth, nc={nc}, cnonce="{cnonce}"'
    else:
        resp = _md5(f"{ha1}:{nonce}:{ha2}")
        extra = ""
    opaque = f', opaque="{campos["opaque"]}"' if "opaque" in campos else ""
    return (f'Digest username="{user}", realm="{realm}", nonce="{nonce}", uri="{uri}", '
            f'response="{resp}", algorithm=MD5{extra}{opaque}')


class _Proto(asyncio.DatagramProtocol):
    def __init__(self, fila: asyncio.Queue):
        self.fila = fila

    def datagram_received(self, data, addr):
        self.fila.put_nowait((data, addr))


@dataclass
class Chamada:
    """O que se sabe de uma chamada depois do INVITE: como terminou o convite, e o que veio."""
    status_final: int = 0
    respostas: list[int] = field(default_factory=list)
    atendida_em: float | None = None
    bye_recebido_em: float | None = None
    rtp_recebidos: int = 0
    energia: list[tuple[float, float]] = field(default_factory=list)   # (instante, rms) por pacote
    payload_types: set[int] = field(default_factory=set)
    sdp_remoto: str = ""
    motivo: str = ""
    te_pt: int | None = None          # payload type de `telephone-event` que o outro lado ACEITOU
    teclas_enviadas: int = 0          # eventos RFC 4733 completos (com os três pacotes de fim)


class SipUA:
    def __init__(self, host: str, port: int = 5060, *, user: str, senha: str, ani: str,
                 log=print, dtmf_fora_de_banda: bool = True) -> None:
        self.host, self.port = socket.gethostbyname(host), port
        self.user, self.senha, self.ani = user, senha, ani
        self.log = log
        self.ip = self._ip_local()
        self._sip_fila: asyncio.Queue = asyncio.Queue()
        self._rtp_fila: asyncio.Queue = asyncio.Queue()
        self.chamada = Chamada()
        self._cseq = 0
        self._call_id = f"{uuid.uuid4().hex}@{self.ip}"
        self._tag = uuid.uuid4().hex[:10]
        self._to = ""
        self._remote_contact = ""
        self._rtp_dest: tuple[str, int] | None = None
        self._tasks: list[asyncio.Task] = []
        self._falando: asyncio.Queue[bytes] = asyncio.Queue()
        self._teclas: asyncio.Queue[str] = asyncio.Queue()
        self._encerrada = asyncio.Event()
        self.dtmf_fora_de_banda = dtmf_fora_de_banda

    def _ip_local(self) -> str:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect((self.host, self.port))
            return s.getsockname()[0]
        finally:
            s.close()

    async def abrir(self) -> None:
        loop = asyncio.get_running_loop()
        self._sip_t, _ = await loop.create_datagram_endpoint(
            lambda: _Proto(self._sip_fila), local_addr=(self.ip, 0))
        self._rtp_t, _ = await loop.create_datagram_endpoint(
            lambda: _Proto(self._rtp_fila), local_addr=(self.ip, 0))
        self.sip_port = self._sip_t.get_extra_info("sockname")[1]
        self.rtp_port = self._rtp_t.get_extra_info("sockname")[1]

    def _via(self, branch: str) -> str:
        return f"SIP/2.0/UDP {self.ip}:{self.sip_port};branch=z9hG4bK{branch};rport"

    def _envia(self, texto: str) -> None:
        self._sip_t.sendto(texto.encode(), (self.host, self.port))

    def _sdp(self) -> str:
        if self.dtmf_fora_de_banda:
            midia = [f"m=audio {self.rtp_port} RTP/AVP 0 101", "a=rtpmap:0 PCMU/8000",
                     "a=rtpmap:101 telephone-event/8000", "a=fmtp:101 0-16"]
        else:
            midia = [f"m=audio {self.rtp_port} RTP/AVP 0", "a=rtpmap:0 PCMU/8000"]
        return CRLF.join([
            "v=0", f"o=probe {random.randint(1, 10**9)} 1 IN IP4 {self.ip}", "s=probe-voz02",
            f"c=IN IP4 {self.ip}", "t=0 0", *midia, "a=ptime:20", "a=sendrecv", ""])

    def _invite(self, dnis: str, auth: str = "", auth_header: str = "Authorization") -> tuple[str, str]:
        self._cseq += 1
        branch = uuid.uuid4().hex[:12]
        uri = f"sip:{dnis}@{self.host}:{self.port}"
        corpo = self._sdp()
        linhas = [
            f"INVITE {uri} SIP/2.0", f"Via: {self._via(branch)}", "Max-Forwards: 70",
            f"From: <sip:{self.ani}@{self.ip}>;tag={self._tag}", f"To: <{uri}>",
            f"Call-ID: {self._call_id}", f"CSeq: {self._cseq} INVITE",
            f"Contact: <sip:{self.ani}@{self.ip}:{self.sip_port}>", "User-Agent: plughub-probe-voz02",
            "Allow: INVITE, ACK, BYE, CANCEL, OPTIONS, INFO",
        ]
        if auth:
            linhas.append(f"{auth_header}: {auth}")   # 401 → Authorization · 407 → Proxy-Authorization
        linhas += ["Content-Type: application/sdp", f"Content-Length: {len(corpo.encode())}", "", corpo]
        return CRLF.join(linhas), branch

    def _ack(self, uri: str, branch: str, to: str, cseq: int) -> str:
        return CRLF.join([
            f"ACK {uri} SIP/2.0", f"Via: {self._via(branch)}", "Max-Forwards: 70",
            f"From: <sip:{self.ani}@{self.ip}>;tag={self._tag}", f"To: {to}",
            f"Call-ID: {self._call_id}", f"CSeq: {cseq} ACK", "Content-Length: 0", "", ""])

    def _responde(self, p: Pedido, status: int, reason: str, corpo: str = "") -> None:
        linhas = [f"SIP/2.0 {status} {reason}"]
        for v in p.headers.get("via", []):
            linhas.append(f"Via: {v}")
        linhas += [f"From: {p.h('from')}", f"To: {p.h('to')}", f"Call-ID: {p.h('call-id')}",
                   f"CSeq: {p.h('cseq')}", f"Contact: <sip:{self.ani}@{self.ip}:{self.sip_port}>"]
        if corpo:
            linhas += ["Content-Type: application/sdp", f"Content-Length: {len(corpo.encode())}", "", corpo]
        else:
            linhas += ["Content-Length: 0", "", ""]
        self._sip_t.sendto(CRLF.join(linhas).encode(), (self.host, self.port))

    async def ligar(self, dnis: str, *, espera_atender_s: float = 60.0) -> Chamada:
        """INVITE até resposta FINAL. Atendida (200) ⇒ ACK, mídia no ar, e o laço de diálogo corre
        até alguém desligar. Recusa ⇒ a chamada acaba com o status final."""
        await self.abrir()
        txt, branch = self._invite(dnis)
        self._envia(txt)
        uri = f"sip:{dnis}@{self.host}:{self.port}"
        prazo = time.monotonic() + espera_atender_s
        autenticou = False
        while time.monotonic() < prazo:
            try:
                data, _ = await asyncio.wait_for(self._sip_fila.get(), timeout=prazo - time.monotonic())
            except asyncio.TimeoutError:
                break
            msg = _parse(data)
            if isinstance(msg, Pedido):
                self._trata_pedido(msg)
                continue
            self.chamada.respostas.append(msg.status)
            if msg.status < 200:
                continue
            if msg.status in (401, 407) and not autenticou:
                self._envia(self._ack(uri, branch, msg.h("to"), self._cseq))
                www = msg.h("www-authenticate") or msg.h("proxy-authenticate")
                txt, branch = self._invite(dnis, _digest(www, "INVITE", uri, self.user, self.senha),
                                           "Proxy-Authorization" if msg.status == 407 else "Authorization")
                self._envia(txt)
                autenticou = True
                continue
            self.chamada.status_final = msg.status
            if msg.status >= 300:
                self._envia(self._ack(uri, branch, msg.h("to"), self._cseq))
                self.chamada.motivo = f"{msg.status} {msg.reason}"
                self._encerrada.set()
                return self.chamada
            # 200 OK
            self.chamada.atendida_em = time.monotonic()
            self._to = msg.h("to")
            m = re.search(r"<([^>]+)>", msg.h("contact"))
            self._remote_contact = m.group(1) if m else uri
            self.chamada.sdp_remoto = msg.body
            c = re.search(r"c=IN IP4 (\S+)", msg.body)
            mp = re.search(r"m=audio (\d+)", msg.body)
            if c and mp:
                self._rtp_dest = (c.group(1), int(mp.group(1)))
            te = re.search(r"a=rtpmap:(\d+) telephone-event/8000", msg.body)
            self.chamada.te_pt = int(te.group(1)) if te else None
            self._envia(self._ack(self._remote_contact, uuid.uuid4().hex[:12], self._to, self._cseq))
            self._tasks = [asyncio.create_task(self._laco_sip()),
                           asyncio.create_task(self._laco_rtp_envio()),
                           asyncio.create_task(self._laco_rtp_recebe())]
            return self.chamada
        self.chamada.motivo = "sem resposta final no prazo"
        return self.chamada

    def _trata_pedido(self, p: Pedido) -> None:
        if p.method == "BYE":
            self._responde(p, 200, "OK")
            self.chamada.bye_recebido_em = time.monotonic()
            self._encerrada.set()
        elif p.method == "INVITE":            # re-INVITE: mantém a mídia como está
            self._responde(p, 200, "OK", self._sdp())
        elif p.method in ("OPTIONS", "INFO", "NOTIFY", "UPDATE"):
            self._responde(p, 200, "OK")
        elif p.method == "ACK":
            pass
        else:
            self.log(f"INFO sip-ua: pedido {p.method} nao tratado — 501")
            self._responde(p, 501, "Not Implemented")

    async def _laco_sip(self) -> None:
        while not self._encerrada.is_set():
            data, _ = await self._sip_fila.get()
            msg = _parse(data)
            if isinstance(msg, Pedido):
                self._trata_pedido(msg)

    async def _laco_rtp_envio(self) -> None:
        """20 ms de PCMU por vez, sempre: fala quando há, silêncio quando não — o outro lado espera
        fluxo contínuo, como um telefone de verdade manda."""
        if not self._rtp_dest:
            return
        seq, ts, ssrc = random.randint(0, 65535), random.randint(0, 2**31), random.randint(0, 2**31)
        pendente = b""
        prox = time.monotonic()
        silencio = b"\xff" * 160
        evento: list = []        # pacotes RFC 4733 da tecla em curso (o áudio cala enquanto ela sai)
        while not self._encerrada.is_set():
            if not evento and not self._teclas.empty():
                evento = self._pacotes_tecla(self._teclas.get_nowait(), ts)
            item = evento.pop(0) if evento else None
            if item is not None:
                marcador, carga_ev, fim = item
                cab = struct.pack("!BBHII", 0x80, (0x80 if marcador else 0) | self.chamada.te_pt,
                                  seq & 0xFFFF, carga_ev[0] & 0xFFFFFFFF, ssrc)
                self._rtp_t.sendto(cab + carga_ev[1], self._rtp_dest)
                if fim:
                    self.chamada.teclas_enviadas += 1
                seq, ts = seq + 1, ts + 160
                prox += 0.02
                await asyncio.sleep(max(0.0, prox - time.monotonic()))
                continue
            # `None` na lista = pausa entre teclas: este tick sai como áudio
            if len(pendente) < 160 and not self._falando.empty():
                pendente += await self._falando.get()
            if len(pendente) >= 160:
                carga, pendente = pendente[:160], pendente[160:]
            else:
                carga = silencio
            cab = struct.pack("!BBHII", 0x80, 0, seq & 0xFFFF, ts & 0xFFFFFFFF, ssrc)
            self._rtp_t.sendto(cab + carga, self._rtp_dest)
            seq, ts = seq + 1, ts + 160
            prox += 0.02
            await asyncio.sleep(max(0.0, prox - time.monotonic()))

    async def _laco_rtp_recebe(self) -> None:
        while not self._encerrada.is_set():
            data, _ = await self._rtp_fila.get()
            if len(data) < 12:
                continue
            pt = data[1] & 0x7F
            self.chamada.payload_types.add(pt)
            self.chamada.rtp_recebidos += 1
            if pt == 0:
                pcm = audioop.ulaw2lin(data[12:], 2)
                self.chamada.energia.append((time.monotonic(), float(audioop.rms(pcm, 2))))

    # RFC 4733: a tecla é UM evento com timestamp fixo (o do início); a duração cresce a cada 20 ms;
    # o fim vai três vezes com o bit E, que é o que o receptor usa para não contar a tecla em dobro.
    _EVENTO = {**{str(d): d for d in range(10)}, "*": 10, "#": 11}

    def _pacotes_tecla(self, tecla: str, ts0: int, duracao_ms: int = 120, pausa_ms: int = 100) -> list:
        ev = self._EVENTO[tecla]
        n = max(1, duracao_ms // 20)
        pac = [(i == 0, (ts0, struct.pack("!BBH", ev, 10, (i + 1) * 160)), False) for i in range(n)]
        fim = struct.pack("!BBH", ev, 0x80 | 10, n * 160)
        pac += [(False, (ts0, fim), k == 2) for k in range(3)]
        # pausa entre teclas: ticks sem evento, em que o laço manda áudio (silêncio)
        return pac + [None] * max(0, pausa_ms // 20)

    def teclar(self, digitos: str) -> float:
        """Teclas FORA de banda (RFC 4733), no payload type que o outro lado aceitou. Devolve a
        duração aproximada. Sem `telephone-event` negociado RECUSA — mandar mesmo assim seria testar
        um telefone que não existe."""
        if self.chamada.te_pt is None:
            raise RuntimeError("telephone-event NAO negociado nesta chamada — use tons_dtmf_pcm8k")
        for d in digitos:
            self._teclas.put_nowait(d)
        return len(digitos) * 0.24

    def falar_pcm8k(self, pcm16: bytes) -> float:
        """Enfileira PCM 16-bit mono a 8 kHz para sair como PCMU; devolve a duração em s."""
        self._falando.put_nowait(audioop.lin2ulaw(pcm16, 2))
        return len(pcm16) / 2 / 8000

    def energia_desde(self, t0: float, limiar: float = 300.0) -> float:
        """Segundos de áudio ACIMA do limiar recebidos desde `t0` (20 ms por pacote)."""
        return 0.02 * sum(1 for t, r in self.chamada.energia if t >= t0 and r >= limiar)

    async def silencio_do_outro_lado(self, *, depois_de: float, calmo_s: float = 1.2,
                                     limite_s: float = 30.0, limiar: float = 300.0) -> bool:
        """Espera o outro lado ter falado algo depois de `depois_de` e então calar por `calmo_s`."""
        prazo = time.monotonic() + limite_s
        ouviu = False
        while time.monotonic() < prazo:
            agora = time.monotonic()
            recentes = [r for t, r in self.chamada.energia if t >= agora - calmo_s]
            ouviu = ouviu or any(r >= limiar for t, r in self.chamada.energia if t >= depois_de)
            if ouviu and recentes and max(recentes) < limiar:
                return True
            await asyncio.sleep(0.1)
        return False

    async def desligar(self) -> None:
        if self._encerrada.is_set() or not self._remote_contact:
            await self._fecha()
            return
        self._cseq += 1
        self._envia(CRLF.join([
            f"BYE {self._remote_contact} SIP/2.0", f"Via: {self._via(uuid.uuid4().hex[:12])}",
            "Max-Forwards: 70", f"From: <sip:{self.ani}@{self.ip}>;tag={self._tag}", f"To: {self._to}",
            f"Call-ID: {self._call_id}", f"CSeq: {self._cseq} BYE", "Content-Length: 0", "", ""]))
        self._encerrada.set()
        await asyncio.sleep(0.3)
        await self._fecha()

    async def esperar_bye(self, limite_s: float) -> bool:
        try:
            await asyncio.wait_for(self._encerrada.wait(), timeout=limite_s)
        except asyncio.TimeoutError:
            return False
        return self.chamada.bye_recebido_em is not None

    async def _fecha(self) -> None:
        self._encerrada.set()
        for t in self._tasks:
            t.cancel()
        for t in (getattr(self, "_sip_t", None), getattr(self, "_rtp_t", None)):
            if t is not None:
                t.close()


_DTMF_HZ = {"1": (697, 1209), "2": (697, 1336), "3": (697, 1477), "4": (770, 1209), "5": (770, 1336),
            "6": (770, 1477), "7": (852, 1209), "8": (852, 1336), "9": (852, 1477), "*": (941, 1209),
            "0": (941, 1336), "#": (941, 1477)}


def tons_dtmf_pcm8k(digitos: str, tom_s: float = 0.15, pausa_s: float = 0.1, amp: int = 6000) -> bytes:
    """As teclas DENTRO do áudio (dois senos por tecla) — o que um telefone sem `telephone-event`
    manda, e o que o PCMU carrega sem distorção relevante."""
    out = []
    for d in digitos:
        f1, f2 = _DTMF_HZ[d]
        out += [struct.pack("<h", int(amp * (math.sin(2 * math.pi * f1 * i / 8000)
                                             + math.sin(2 * math.pi * f2 * i / 8000)) / 2))
                for i in range(int(8000 * tom_s))]
        out.append(b"\x00\x00" * int(8000 * pausa_s))
    return b"".join(out)


def tom_pcm8k(segundos: float = 1.0, hz: float = 440.0, amp: int = 8000) -> bytes:
    n = int(8000 * segundos)
    return b"".join(struct.pack("<h", int(amp * math.sin(2 * math.pi * hz * i / 8000))) for i in range(n))


if __name__ == "__main__":   # diagnóstico manual: python _sip_ua.py <dnis>
    import sys

    async def _main():
        ua = SipUA(os.environ.get("SIP_HOST", "livekit-sip"), user=os.environ.get("SIP_USER", "plughub_demo"),
                   senha=os.environ.get("SIP_PASS", ""), ani=os.environ.get("SIP_ANI", "+5511999990000"))
        ch = await ua.ligar(sys.argv[1], espera_atender_s=float(os.environ.get("ESPERA", "20")))
        print("respostas", ch.respostas, "final", ch.status_final, ch.motivo)
        if ch.status_final == 200:
            await asyncio.sleep(float(os.environ.get("DURACAO", "5")))
            print("rtp recebidos", ch.rtp_recebidos, "pts", ch.payload_types)
            await ua.desligar()
    asyncio.run(_main())
