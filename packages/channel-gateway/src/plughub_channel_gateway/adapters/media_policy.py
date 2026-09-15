"""
adapters/media_policy.py — mídia é fato do PARTICIPANTE, não da sessão (VOZ-09/VOZ-10).

O DEFEITO QUE ISTO SUBSTITUI
  O adapter WebRTC escolhia UM meio para a sessão (`video|voice|text`), guardado em
  `channel:webrtc:{sid}:medium`, e cada `routing.assigned` o SOBRESCREVIA. Medido ao
  vivo em 2026-09-14: um humano de vídeo atende, o cliente publica microfone, um
  especialista de IA de texto entra — e o cliente recebe `webrtc.renegotiate` mandando ir
  para `text`, enquanto o SFU continua permitindo publicar TUDO (`can_publish=True`,
  `can_publish_sources=[]`). Duas respostas para a mesma pergunta, e nenhuma certa.

TRÊS CAMADAS, COMO NUMA VIDEOCONFERÊNCIA — DENTRO DE UM TETO
  POLÍTICA    o que o POOL de cada atendente oferece, por direção
              (`media_policy.customer_publish` / `agent_publish`, no agent-registry,
              editável na tela). Viaja no `routing.assigned`, lida fresca pelo bridge.
              Até a VOZ-10 era uma tabela de plataforma neste arquivo.
  CAPACIDADE  o que o atendente consegue CONSUMIR, pelo framework. É por isso que o teto
              do cliente depende de quem está atendendo: publicar vídeo para um agente de
              IA que só lê texto não serve a ninguém e só expõe o cliente.
  ESCOLHA     microfone e câmera ligados ou não. É do participante, no cliente, e o SFU
              a limita pela permissão — o servidor não a decide nem precisa conhecê-la.

  Teto do cliente  = UNIÃO, sobre os atendentes presentes, de
                     (`customer_publish` do pool do atendente ∩ o que ele consome).
  Teto do atendente humano = UNIÃO do `agent_publish` dos pools dos atendentes humanos.
  União, não substituição: quem entra SOMA, quem sai retira — um especialista de texto
  entrando numa chamada de vídeo não muda nada.

AUSÊNCIA NUNCA VIRA PERMISSÃO
  Atendente cujo pool não declarou política, cuja política não pôde ser lida do registry,
  ou cujo evento não trouxe pool nenhum contribui NADA — e a procedência diz qual das três
  foi. Supervisor e bot não são config de pool: são papéis da PLATAFORMA (oculto e sem
  publicação é invariante do supervisor, não escolha de tenant).

Tudo aqui é puro (sem I/O). Quem aplica é o adapter: token com `can_publish_sources` e
`update_participant` no SFU quando o teto muda.
"""
from __future__ import annotations

from dataclasses import dataclass

# Tipos de mídia contínua. Texto (WS/DataChannel) é sempre permitido e não passa por aqui;
# arquivo e imagem são CONTEÚDO, outro eixo (VOZ-12). Gêmeo de `MediaKindSchema`
# (`@plughub/schemas/agent-registry.ts`).
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

# Chaves que o gateway LÊ da política do pool — tem de ser exatamente as do
# `PoolMediaPolicySchema`; o `probe_webrtc_pool_media_policy.sh` confere os dois lados.
POLICY_KEYS: tuple[str, ...] = ("customer_publish", "agent_publish")


@dataclass(frozen=True)
class RolePolicy:
    publish:   frozenset[str]
    subscribe: bool
    hidden:    bool


# Papéis que NÃO são config de pool.
PLATFORM_ROLES: dict[str, RolePolicy] = {
    SUPERVISOR: RolePolicy(publish=frozenset(),        subscribe=True, hidden=True),
    BOT:        RolePolicy(publish=frozenset({AUDIO}), subscribe=True, hidden=True),
}

# O que cada tipo de atendente CONSOME da mídia do cliente, SOZINHO.
#   human        — está num browser: recebe áudio e vídeo (receber não exige câmera).
#   native       — agente de IA do skill-flow: lê TEXTO. Consome ÁUDIO só por meio do bot
#   external-mcp   leg (VOZ-05, ver `AI_FRAMEWORKS`), e vídeo quando houver avatar (VOZ-14).
# Framework ausente ou desconhecido consome NADA: o restritivo vence, e o adapter loga.
CONSUMES_BY_FRAMEWORK: dict[str, frozenset[str]] = {
    "human":        frozenset({AUDIO, VIDEO}),
    "native":       frozenset(),
    "external-mcp": frozenset(),
}

# O bot leg tem DOIS usos, e cada um pede um provedor diferente (modelo do dono, 2026-09-15):
#   TRANSCREVER — toda chamada com áudio é transcrita, cliente e humano, cada um no seu canal:
#                 o módulo de qualidade avalia sobre a transcrição. Pede STT.
#   CONVERTER   — o agente de IA lê TEXTO (ADR V4): ouve o cliente pelo que o STT transcreve e
#                 fala pelo TTS. Pede STT **e** TTS.
AI_FRAMEWORKS:     frozenset[str] = frozenset({"native", "external-mcp"})
AUDIO_FRAMEWORKS:  frozenset[str] = frozenset({"human"}) | AI_FRAMEWORKS


def attendant_consumes(framework: str, bot_leg_audio: bool = False) -> frozenset[str]:
    """
    O que um atendente deste framework consome. Desconhecido → nada.

    `bot_leg_audio` é fato do GATEWAY (há STT e TTS de verdade para converter): com ele, o
    agente de IA passa a consumir áudio. Sem ele, nunca — ausência não vira permissão.
    """
    base = CONSUMES_BY_FRAMEWORK.get(framework, frozenset())
    if bot_leg_audio and framework in AI_FRAMEWORKS:
        return base | {AUDIO}
    return base


def bot_leg_needs(attendants: dict[str, dict]) -> dict[str, bool]:
    """
    O que a chamada PEDE ao bot leg, pelo pool de quem atende — independe de o bot leg existir:
      transcribe  algum atendente de áudio (humano ou IA) cujo pool oferece áudio ao cliente;
      convert     algum agente de IA nessa condição (precisa ouvir por STT e falar por TTS).
    """
    com_audio = [a for a in attendants.values() if AUDIO in (a.get("customer_publish") or [])]
    return {
        "transcribe": any(a.get("framework") in AUDIO_FRAMEWORKS for a in com_audio),
        "convert":    any(a.get("framework") in AI_FRAMEWORKS for a in com_audio),
    }


def _kinds(value: object) -> tuple[frozenset[str], list[str]]:
    """Lista declarada → tipos conhecidos, e os desconhecidos para o aviso."""
    if not isinstance(value, list):
        return frozenset(), [] if value is None else [repr(value)]
    good = frozenset(v for v in value if v in KINDS)
    bad = [repr(v) for v in value if v not in KINDS]
    return good, bad


def attendant_from_pool_field(framework: str, pool_field: dict) -> tuple[dict, str | None]:
    """
    Monta o registro do atendente a partir do campo `pool` do `routing.assigned`.

    Devolve (registro, aviso). O aviso não é None quando a política foi RECUSADA — e o
    registro, nesse caso, contribui nada. Nunca se supõe o que o pool oferece.
    """
    pool_id = str(pool_field.get("pool_id") or "")
    source = pool_field.get("media_policy_source", "")
    record = {"framework": framework, "pool_id": pool_id,
              "customer_publish": [], "agent_publish": [], "policy_source": ""}

    if not pool_id:
        record["policy_source"] = "evento_sem_pool"
        return record, "routing.assigned sem pool_id — atendente sem politica de midia"
    if source == "registry_unavailable":
        record["policy_source"] = f"registry_indisponivel:{pool_id}"
        return record, f"politica do pool {pool_id} nao foi lida do registry pelo bridge"
    if source != "registry":
        record["policy_source"] = f"sem_leitura:{pool_id}"
        return record, (f"routing.assigned do pool {pool_id} sem `media_policy_source=registry` "
                        f"(veio {source!r}) — politica desconhecida")
    policy = pool_field.get("media_policy")
    if not isinstance(policy, dict):
        record["policy_source"] = f"pool_sem_politica:{pool_id}"
        return record, f"pool {pool_id} nao declara media_policy — atendente nao oferece midia"

    cust, bad_c = _kinds(policy.get("customer_publish"))
    agent, bad_a = _kinds(policy.get("agent_publish"))
    record.update(customer_publish=kinds_list(cust), agent_publish=kinds_list(agent),
                  policy_source=f"pool:{pool_id}")
    if bad_c or bad_a:
        return record, f"pool {pool_id} declara tipo de midia desconhecido {bad_c + bad_a} — ignorado"
    return record, None


def customer_ceiling(attendants: dict[str, dict], bot_leg_audio: bool = False) -> frozenset[str]:
    """
    Teto do cliente: UNIÃO, sobre os atendentes, de (`customer_publish` ∩ consumo).

    `attendants` é `{instance_id: registro}`, com o registro de `attendant_from_pool_field`.
    `bot_leg_audio`: ver `attendant_consumes`.
    """
    out: set[str] = set()
    for a in attendants.values():
        out |= frozenset(a.get("customer_publish") or []) & attendant_consumes(
            a.get("framework", ""), bot_leg_audio)
    return frozenset(k for k in out if k in KINDS)


def agent_ceiling(attendants: dict[str, dict]) -> frozenset[str]:
    """Teto de publicação de um atendente HUMANO: UNIÃO do `agent_publish` dos pools humanos."""
    out: set[str] = set()
    for a in attendants.values():
        if a.get("framework") == "human":
            out |= frozenset(a.get("agent_publish") or [])
    return frozenset(k for k in out if k in KINDS)


def policy_sources(attendants: dict[str, dict]) -> list[str]:
    """Procedência de cada atendente, ordenada — para o estado e para a mensagem."""
    return sorted({a.get("policy_source", "") for a in attendants.values() if a.get("policy_source")})


def role_policy(role: str) -> RolePolicy:
    """Política de um papel de PLATAFORMA. Papel desconhecido é erro de programação."""
    if role not in PLATFORM_ROLES:
        raise ValueError(f"papel de plataforma desconhecido: {role!r} — esperado um de {sorted(PLATFORM_ROLES)}")
    return PLATFORM_ROLES[role]


def publish_sources(kinds: frozenset[str] | set[str]) -> list[str]:
    """Tipos → fontes do LiveKit, em ordem estável (`microphone` antes de `camera`)."""
    return [_SOURCE_BY_KIND[k] for k in KINDS if k in kinds]


def kinds_list(kinds: frozenset[str] | set[str]) -> list[str]:
    """Tipos em ordem estável, para mensagem e estado."""
    return [k for k in KINDS if k in kinds]
