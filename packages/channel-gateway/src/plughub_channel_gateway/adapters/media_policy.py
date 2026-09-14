"""
adapters/media_policy.py — mídia é fato do PARTICIPANTE, não da sessão (VOZ-09).

O DEFEITO QUE ISTO SUBSTITUI
  O adapter WebRTC escolhia UM meio para a sessão (`video|voice|text`), guardado em
  `channel:webrtc:{sid}:medium`, e cada `routing.assigned` o SOBRESCREVIA. Medido ao
  vivo em 2026-09-14: um humano de vídeo atende, o cliente publica microfone, um
  especialista de IA de texto entra — e o cliente recebe `webrtc.renegotiate` mandando ir
  para `text`, enquanto o SFU continua permitindo publicar TUDO (`can_publish=True`,
  `can_publish_sources=[]`). Duas respostas para a mesma pergunta, e nenhuma certa.

TRÊS CAMADAS, COMO NUMA VIDEOCONFERÊNCIA — DENTRO DE UM TETO
  POLÍTICA    o que cada PAPEL pode publicar e assinar. Mora aqui como tabela de
              plataforma (`PLATFORM_DEFAULT`) até a VOZ-10 trazê-la do POOL; a procedência
              viaja no estado (`policy_source`) para a troca não ser silenciosa.
  CAPACIDADE  o que o lado que ATENDE consegue CONSUMIR. É por isso que o teto do
              cliente depende de quem está atendendo: publicar vídeo para um agente de IA
              que só lê texto não serve a ninguém e só expõe o cliente.
  ESCOLHA     microfone e câmera ligados ou não. É do participante, no cliente, e o SFU
              a limita pela permissão — o servidor não a decide nem precisa conhecê-la.

O teto do cliente é a POLÍTICA do papel `customer` ∩ a UNIÃO do que os atendentes
presentes consomem. União, não substituição: quem entra SOMA consumo, quem sai o retira —
um especialista de texto entrando numa chamada de vídeo não muda nada.

Tudo aqui é puro (sem I/O). Quem aplica é o adapter: token com `can_publish_sources` e
`update_participant` no SFU quando o teto muda.
"""
from __future__ import annotations

from dataclasses import dataclass

# Tipos de mídia contínua. Texto (WS/DataChannel) é sempre permitido e não passa por aqui;
# arquivo e imagem são CONTEÚDO, outro eixo (VOZ-12).
AUDIO = "audio"
VIDEO = "video"
KINDS: tuple[str, ...] = (AUDIO, VIDEO)

# Fonte do LiveKit por tipo — o que vai em `can_publish_sources`.
_SOURCE_BY_KIND = {AUDIO: "microphone", VIDEO: "camera"}

# Papéis na SALA. Não são os papéis de participação da sessão (`primary`/`specialist`):
# um humano atendendo é `agent` nos dois casos, porque o que decide aqui é mídia.
CUSTOMER   = "customer"
AGENT      = "agent"
SUPERVISOR = "supervisor"
BOT        = "bot"


@dataclass(frozen=True)
class RolePolicy:
    publish:   frozenset[str]
    subscribe: bool
    hidden:    bool


# ⚠️ Tabela de PLATAFORMA, e só até a VOZ-10: a política é config de negócio e o destino
# dela é o POOL (agent-registry, editável na tela). Enquanto morar aqui, todo estado
# gravado carrega `policy_source=platform_default`.
PLATFORM_DEFAULT: dict[str, RolePolicy] = {
    CUSTOMER:   RolePolicy(publish=frozenset({AUDIO, VIDEO}), subscribe=True, hidden=False),
    AGENT:      RolePolicy(publish=frozenset({AUDIO, VIDEO}), subscribe=True, hidden=False),
    SUPERVISOR: RolePolicy(publish=frozenset(),               subscribe=True, hidden=True),
    BOT:        RolePolicy(publish=frozenset({AUDIO}),        subscribe=True, hidden=True),
}
PLATFORM_DEFAULT_SOURCE = "platform_default"

# O que cada tipo de atendente CONSOME da mídia do cliente.
#   human        — está num browser: recebe áudio e vídeo (receber não exige câmera).
#   native       — agente de IA do skill-flow: lê TEXTO. Passa a consumir áudio quando o
#   external-mcp   bot leg existir (VOZ-05) e vídeo quando houver avatar (VOZ-14) — e
#                  essa capacidade vem do DEPLOY do pool (VOZ-10), não desta tabela.
# Framework ausente ou desconhecido consome NADA: o restritivo vence, e o adapter loga.
CONSUMES_BY_FRAMEWORK: dict[str, frozenset[str]] = {
    "human":        frozenset({AUDIO, VIDEO}),
    "native":       frozenset(),
    "external-mcp": frozenset(),
}


def attendant_consumes(framework: str) -> frozenset[str]:
    """O que um atendente deste framework consome. Desconhecido → nada."""
    return CONSUMES_BY_FRAMEWORK.get(framework, frozenset())


def customer_ceiling(
    attendants: dict[str, str],
    policy: dict[str, RolePolicy] = PLATFORM_DEFAULT,
) -> frozenset[str]:
    """
    Teto do cliente: política do papel `customer` ∩ UNIÃO do consumo dos atendentes.

    `attendants` é `{instance_id: framework}` dos atendentes PRESENTES.
    """
    consumed: set[str] = set()
    for framework in attendants.values():
        consumed |= attendant_consumes(framework)
    return frozenset(policy[CUSTOMER].publish & consumed)


def role_policy(role: str, policy: dict[str, RolePolicy] = PLATFORM_DEFAULT) -> RolePolicy:
    """Política de um papel da sala. Papel desconhecido é erro de programação."""
    if role not in policy:
        raise ValueError(f"papel de sala desconhecido: {role!r} — esperado um de {sorted(policy)}")
    return policy[role]


def publish_sources(kinds: frozenset[str] | set[str]) -> list[str]:
    """Tipos → fontes do LiveKit, em ordem estável (`microphone` antes de `camera`)."""
    return [_SOURCE_BY_KIND[k] for k in KINDS if k in kinds]


def kinds_list(kinds: frozenset[str] | set[str]) -> list[str]:
    """Tipos em ordem estável, para mensagem e estado."""
    return [k for k in KINDS if k in kinds]
