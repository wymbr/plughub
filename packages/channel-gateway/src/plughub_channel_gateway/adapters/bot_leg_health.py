"""
adapters/bot_leg_health.py — o bot leg que CAI no meio da chamada (VOZ-54, 2026-10-02).

A degradação da IA por áudio (`bot_leg_unavailable`, VOZ-11) é decidida pela CONFIG: STT e TTS
ligados e com serviço, lidos no início da chamada. Se o serviço de fala cai com a chamada de pé,
a IA para de ouvir e de falar, cada frase perdida vira um ERROR no log — e nada vira
`media.degraded`, nem aviso ao cliente, que segue falando para ninguém.

Isto conta, POR SESSÃO, o desfecho de cada pedido ao serviço de fala (uma transcrição, uma
síntese). Falhas CONSECUTIVAS de uma parte (`stt` ou `tts`) atingindo o limiar viram `lost`; o
primeiro sucesso da parte perdida, quando nenhuma outra segue perdida, vira `restored`. Uma falha
isolada não muda nada: a frase perdida já é dita no log e no `speech.metrics`, e uma volta por
frase acenderia e apagaria o aviso ao cliente a cada soluço da rede.

Puro, sem I/O: quem aplica a transição é o adapter (o mesmo `_media_changed` da VOZ-11).
"""
from __future__ import annotations

# Duas frases seguidas perdidas não são soluço: a pessoa já falou duas vezes para ninguém.
LOSS_THRESHOLD = 2

LOST     = "lost"
RESTORED = "restored"
PARTS    = ("stt", "tts")


class BotLegHealth:
    def __init__(self, threshold: int = LOSS_THRESHOLD) -> None:
        self._threshold = max(1, int(threshold))
        self._fails: dict[str, dict[str, int]] = {}
        self._lost:  dict[str, set[str]] = {}

    def record(self, session_id: str, part: str, ok: bool) -> str | None:
        """Registra o desfecho de um pedido. Devolve a TRANSIÇÃO da sessão (`lost`/`restored`) ou None."""
        if not session_id or part not in PARTS:
            return None
        perdidas = self._lost.get(session_id)
        if ok:
            falhas = self._fails.get(session_id)
            if falhas is not None:
                falhas.pop(part, None)
            if perdidas and part in perdidas:
                perdidas.discard(part)
                if not perdidas:
                    del self._lost[session_id]
                    return RESTORED
            return None
        falhas = self._fails.setdefault(session_id, {})
        falhas[part] = falhas.get(part, 0) + 1
        if falhas[part] < self._threshold or (perdidas and part in perdidas):
            return None
        ja_perdida = bool(perdidas)
        self._lost.setdefault(session_id, set()).add(part)
        return None if ja_perdida else LOST

    def is_lost(self, session_id: str) -> bool:
        return bool(self._lost.get(session_id))

    def lost_parts(self, session_id: str) -> list[str]:
        return sorted(self._lost.get(session_id) or ())

    def forget(self, session_id: str) -> None:
        self._fails.pop(session_id, None)
        self._lost.pop(session_id, None)
