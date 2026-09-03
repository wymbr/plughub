"""
channel_capability_registry.py
Static registry of channel capabilities and capability-based channel selection.

Arc 16 Phase D — Channel Capability Negotiation.
Arc 19 Fase F — Journey entity eliminated; capability selection now operates
                directly on registered adapters (no journey ContextStore I/O).

The registry maps each channel name to the set of ChannelCapability values it
supports.  When a collect step omits an explicit channel and supplies a
`requires[]` list instead, the Channel Gateway calls select_channel() against
the full list of registered adapter channels.

Spec: docs/arcos/arc19-unified-session-model.md
"""

from __future__ import annotations

import logging

logger = logging.getLogger("plughub.channel-gateway.capability")


# ── Capacidade de canal — CASA ÚNICA (NIV-01, 2026-09-03) ────────────────────
#
# Capacidade é fato do PROTOCOLO, não config de tenant. Ninguém faz o SMS suportar
# campo de senha marcando um booleano — e um campo configurável convida exatamente
# isso: alguém marca `true` para whatsapp e a plataforma acredita.
#
# Por isso a casa é esta, em código, chaveada pelo vocabulário do
# `ChannelCapabilitySchema` (@plughub/schemas/src/skill.ts) — que é o vocabulário
# que os consumidores realmente usam (`collect.requires[]`).
#
# ⚠️ Havia uma SEGUNDA casa, e ela nem falava o mesmo vocabulário:
# `ChannelCapabilitiesSchema` (plural, `channel-events.ts`) declarava os mesmos fatos
# como booleanos (`supports_masked_input`, `supports_buttons`, …), como config POR
# TENANT, com **zero consumidores** — e **discordava desta** em `voice`. Foi removida;
# o que era política (`masked_fallback`) sobreviveu em `MaskedFallbackPolicySchema`,
# porque *o que fazer quando não dá* é decisão de tenant, ao contrário de *se dá*.
#
# ⚠️ **A tabela é EXAUSTIVA sobre `ChannelSchema`**, e o gate impõe isso. Antes ela
# tinha 6 chaves para 9 canais do domínio: `instagram` e `telegram` caíam no
# `.get(ch, frozenset())`, satisfaziam requisito nenhum e **nunca eram eleitos, em
# silêncio**. Restritivo é o default certo; mudo não é. Canal novo agora obriga uma
# linha aqui — ou o gate reprova.

CHANNEL_CAPABILITIES: dict[str, frozenset[str]] = {
    "whatsapp":  frozenset({"text", "file_upload", "rich_menu"}),
    "sms":       frozenset({"text"}),
    "email":     frozenset({"text", "file_upload"}),
    # ⚠️ `voice` NÃO declara `masked_input`, e isto é decisão, não esquecimento — mas
    # a decisão tem TRÊS impedimentos empilhados, e confundi-los foi erro meu na
    # primeira redação (corrigido 2026-09-03, a pedido do dono):
    #
    #   (a) **o canal não funciona** — sem SFU, sem env `LIVEKIT_*`, provider em
    #       `_dev_mode` devolvendo token placebo (`CLAUDE.md` § Arc 15). Temporário,
    #       e resolve-se por DEPLOY (arco V-F0..V-F5).
    #   (b) **a garantia não está construída** — DTMF é mascarado por natureza *se* o
    #       dígito não vazar; medido: **não existe supressão de DTMF** neste adapter,
    #       nem em transcript nem em gravação. E DTMF é decodificável a partir do
    #       áudio gravado. É TRABALHO, não deploy → **NIV-06**.
    #   (c) **a definição da capacidade exclui voz por construção** — o enum diz
    #       `masked_input // password-overlay masked field (webchat)`, ou seja,
    #       descreve o MECANISMO e nomeia o canal. Enquanto for assim, nenhum avanço
    #       em (a) ou (b) torna voz elegível: o gatilho não fecha → **NIV-05**.
    #
    # ⚠️ Nada disto restringe o TRATAMENTO de eco em voz, que é outro eixo e está
    # intacto: `EchoMode` (`plain` verbaliza o dígito · `masked` bipa · `none` cala)
    # é traduzido pelo adapter, e o eco existe justamente para dar feedback de tecla.
    # O que a linha abaixo nega é ELEIÇÃO — voz não é escolhida para COLETAR um campo
    # mascarado —, não a capacidade de tratar o eco.
    "voice":     frozenset({"audio"}),
    "webchat":   frozenset({"text", "file_upload", "rich_menu", "masked_input"}),
    "webrtc":    frozenset({"text", "audio", "video", "file_upload"}),
    # Entraram na NIV-01 para que a ausência deixasse de ser silenciosa. As duas são
    # canais de mensagem com mídia; nenhuma tem superfície de entrada mascarada.
    "instagram": frozenset({"text", "file_upload"}),
    "telegram":  frozenset({"text", "file_upload", "rich_menu"}),
    # `webhook` é o canal de WORKFLOW (Arc 19): não há cliente do outro lado, e por
    # isso ele não tem capacidade de interação nenhuma. Declarado VAZIO de propósito —
    # omiti-lo devolveria a ausência silenciosa que esta tabela existe para fechar.
    "webhook":   frozenset(),
}

# Priority ordering when no preference is set (most capable → least).
# Channels not listed fall to the end.
# ⚠️ Canal FORA desta lista cai no fim (`priority.get(ch, len(...))`) — o que é um
# desempate por acidente, não por decisão. `instagram`/`telegram` entram aqui pelo
# mesmo motivo que entraram na tabela acima. `webhook` fica fora de propósito: ele não
# tem capacidade nenhuma, logo nunca é eleito, e listá-lo sugeriria que poderia ser.
_CHANNEL_PRIORITY: list[str] = [
    "webrtc", "whatsapp", "webchat", "telegram", "instagram", "email", "voice", "sms",
]


# ── Pure selection logic ──────────────────────────────────────────────────────

def channel_satisfies(channel: str, requires: list[str]) -> bool:
    """Return True if *channel* supports every capability in *requires*."""
    if not requires:
        return True
    caps = CHANNEL_CAPABILITIES.get(channel, frozenset())
    return all(req in caps for req in requires)


def select_channel(
    available_channels: list[str],
    requires:           list[str],
    preferred_channel:  str | None,
) -> str | None:
    """
    Select the best outbound channel for a collect step.

    Algorithm:
      1. If *preferred_channel* is in *available_channels* and satisfies
         all *requires*, return it immediately.
      2. Otherwise sort *available_channels* by _CHANNEL_PRIORITY and return
         the first that satisfies *requires*.
      3. Return None if no channel satisfies the requirements.

    Args:
        available_channels: Channels the customer has been reached on in this journey
                            (read from journey.available_channels in ContextStore).
        requires:           Capability strings from the collect step's `requires[]` field.
        preferred_channel:  journey.canal_preferido — the most recently active channel.
    """
    if not available_channels:
        return None

    # Step 1 — honour preference when it works
    if preferred_channel and preferred_channel in available_channels:
        if channel_satisfies(preferred_channel, requires):
            return preferred_channel

    # Step 2 — pick highest-priority qualifying channel
    priority = {ch: i for i, ch in enumerate(_CHANNEL_PRIORITY)}
    ordered  = sorted(
        available_channels,
        key=lambda ch: priority.get(ch, len(_CHANNEL_PRIORITY)),
    )
    for ch in ordered:
        if channel_satisfies(ch, requires):
            return ch

    return None


# REMOVED (Arc 19 Fase F) — Journey entity eliminated
# read_journey_channel_context, write_journey_channel_context,
# get_journey_contact_id, write_journey_pending_collect
