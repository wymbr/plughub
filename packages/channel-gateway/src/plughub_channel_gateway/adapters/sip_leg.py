"""
sip_leg.py — a perna SIP: como uma chamada de telefone vira CONTATO (VOZ-02, fatia 1).

O conversor é o serviço SIP do próprio SFU (`livekit/sip`): o tronco entrega a chamada a ele, a
dispatch rule põe o chamador numa sala nova e ele vira PARTICIPANTE dela (`kind == SIP`). A partir
daí a mídia é a mesma da chamada de browser — uma sala por sessão, bot leg, agente, supervisor
(ADR `adr-voice-media-plane.md` V3). O que muda é a ORDEM de nascimento, e é o fato que este
módulo existe para nomear:

  * no WebRTC o contato nasce primeiro (widget autentica → sessão → roteamento) e a sala depois,
    criada pelo gateway com nome derivado da sessão (`plughub-{session_id}`);
  * no SIP a chamada chega primeiro e a SALA nasce antes de haver sessão — quem a cria é o
    serviço SIP, com o prefixo da dispatch rule. A sessão nasce do webhook `participant_joined`
    e ADOTA a sala que já existe. Por isso a sala é fato da SESSÃO (gravado), nunca derivado do
    `session_id` onde a sessão pode ser SIP.

O prefixo é contrato entre o seed da dispatch rule (`infra/seed/seed_sip.py`, que o importa DAQUI)
e o gateway, que só adota sala com ele — webhook de sala alheia (a de browser, a da verificação de
fala) não vira contato telefônico por acidente.
"""
from __future__ import annotations

from dataclasses import dataclass

# Uma casa: o seed importa esta constante, o gateway filtra por ela.
SIP_ROOM_PREFIX = "plughub-sip-"

# `ParticipantInfo.Kind.SIP` no protocolo do LiveKit (livekit.protocol.models).
SIP_KIND = "SIP"


@dataclass(frozen=True)
class SipCall:
    """O que o participante SIP diz sobre a chamada, lido dos atributos que o serviço SIP grava.

    `dnis` é o número DISCADO (`sip.trunkPhoneNumber`) — é ele que endereça o pool, pelo endpoint
    `voice` cadastrado. `ani` é quem ligou (`sip.phoneNumber`), e some quando a dispatch rule
    esconde o número (`hide_phone_number`): aí é vazio, e o contato não inventa um."""
    room:     str
    identity: str
    call_id:  str
    dnis:     str
    ani:      str


def is_sip_room(room: str) -> bool:
    return bool(room) and room.startswith(SIP_ROOM_PREFIX)


def parse_sip_participant(room: str, participant: dict) -> SipCall | None:
    """A chamada, a partir do participante de um webhook — ou None se não for chamada NOSSA.

    None tem dois sentidos, e o chamador não precisa distinguir: participante que não é SIP (o
    bot leg, o agente, o supervisor entrando na mesma sala) ou sala sem o prefixo da nossa
    dispatch rule. Os dois NÃO são contato telefônico."""
    if not is_sip_room(room):
        return None
    kind = participant.get("kind")
    if kind not in (SIP_KIND, 3):          # 3 = Kind.SIP no enum numérico, quando vier sem nome
        return None
    attrs = participant.get("attributes") or {}
    return SipCall(
        room     = room,
        identity = str(participant.get("identity") or ""),
        call_id  = str(attrs.get("sip.callID") or ""),
        dnis     = normalize_number(attrs.get("sip.trunkPhoneNumber")),
        ani      = normalize_number(attrs.get("sip.phoneNumber")),
    )


def normalize_number(raw: object) -> str:
    """Número como o cadastro de endpoint o guarda (`+5511…`): só dígitos, com `+` na frente.

    Sem heurística de país: número que chega sem código de país continua sem — completar com
    `55` seria adivinhar, e endpoint casado por adivinhação é o fallback de endereço que a casa
    proíbe (`CLAUDE.md` § Invariants, corolário do `queue_config`)."""
    s = str(raw or "").strip()
    digitos = "".join(c for c in s if c.isdigit())
    if not digitos:
        return ""
    return "+" + digitos
