"""
channel_capability_registry.py
Static registry of channel capabilities and capability-based channel selection.

Arc 16 Phase D — Channel Capability Negotiation.
Arc 19 Fase F — Journey entity eliminated; capability selection now operates
                directly on registered adapters (no journey ContextStore I/O).

The registry maps each channel name to the set of ChannelCapability values it
supports. `channel_satisfies` is the only reader: the live collect election
(`WebhookAdapter._negotiate_channel`, NIV-02) asks it whether each candidate channel
can do what the interaction collects. (`select_channel`, fed by the `collect.events`
topic that never carried an event, was removed in WFL-02 on 2026-09-30.)

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
# Por isso a casa é em CÓDIGO, chaveada pelo vocabulário do
# `ChannelCapabilitySchema` (@plughub/schemas/src/skill.ts) — que é o vocabulário
# que os consumidores realmente usam (`collect.requires[]`).
#
# ⚠️ **MUDOU NA NIV-03 (2026-09-03): esta tabela é o GÊMEO, não o canônico.**
# O canônico é `@plughub/schemas/src/channel-capabilities.ts`. Não é reversão da
# NIV-01 — é o conserto do que ela não tinha como ver: *a casa única precisa ser
# LEGÍVEL por toda linguagem que decide sobre capacidade*, e dois decisores que
# apareceram na NIV-03 são TypeScript — o `notification_send` do mcp-server (único
# ponto por onde um menu mascarado sai para o canal do cliente) e, na metade de
# deploy, o `set-next`/`promote` do agent-registry sobre `Pool.channel_types`.
#
# O gêmeo continua existindo porque o channel-gateway é Python e não importa TS.
# A paridade é IMPOSTA pelo ramo F de
# `infra/test/probe_channel_capability_single_house.sh` — mesmo arranjo de
# `py-contextstore`: um canônico, um gêmeo, e um gate que CONTA a duplicação em
# vez de deixá-la envelhecer calada. **Editar aqui sem editar lá reprova.**
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
    # `voice` DECLARA `masked_input` desde 2026-09-18 (NIV-07, decisão do dono), pela GARANTIA da
    # NIV-05, na perna SIP (a ÚNICA desde a VOZ-03): o valor entra só por TECLA fora de banda (RFC 4733 —
    # fala nunca coleta dado protegido, NIV-08); a fala transcrita é descartada no bloco; o
    # histórico recebe a linha REDIGIDA; e a sala fica só com o cliente e os bots durante o
    # bloco (PAUSA DE MÍDIA), porque a tecla SIP chega a TODOS os participantes (medido). Quem
    # não sai da sala desfaz a coleta (`aborted` → `on_failure`). A perna Twilio (legado), que não
    # tinha nada disso e recusava menu mascarado, foi APOSENTADA na VOZ-03 (2026-09-28).
    # ⚠️ Sem `telephone-event` negociado a tecla não chega (medido, VOZ-31): a coleta expira,
    # nada vaza — e o tom dentro do áudio não passa porque ninguém além dos bots está na sala.
    #
    # Histórico — por que `voice` NÃO declarava, com os impedimentos como estavam:
    # ⚠️ `voice` NÃO declara `masked_input`, e isto é decisão, não esquecimento — mas
    # a decisão tem TRÊS impedimentos empilhados, e confundi-los foi erro meu na
    # primeira redação (corrigido 2026-09-03, a pedido do dono):
    #
    #   (a) **o canal não funciona** — até 2026-09-14 sem SFU, sem env `LIVEKIT_*` e
    #       com o provider em `_dev_mode` devolvendo token placebo. A VOZ-01 (V-F0)
    #       subiu SFU + TURN no compose e trocou o placebo por RECUSA; o que falta
    #       para ESTE canal (`voice`, PSTN) é a perna SIP — VOZ-02/VOZ-03.
    #   (b) **o tratamento não está construído** — medido: `voice.py` tem ZERO
    #       ocorrências de "masked". Não há eco a mascarar porque não há eco: o
    #       adapter não verbaliza, não bipa e não cala por política. É LACUNA, não
    #       vazamento → **NIV-06**. Ao lado dela, duas fronteiras: a negociação
    #       out-of-band precisa ser ASSERIDA (**NIV-07**) e `masked` +
    #       `input_mode: voice` precisa ser RECUSADO (**NIV-08**).
    #
    #       ⚠️ Correção de 2026-09-03, apontada pelo dono: a primeira redação disto
    #       dizia que **DTMF é decodificável do áudio gravado**, e usava isso como
    #       impedimento. **Está errado para os transportes que a plataforma usa.** Em
    #       WebRTC (RFC 4733 `telephone-event`) e em SIP (RFC 2833 / SIP INFO) o
    #       dígito viaja FORA do fluxo de áudio; o beep audível é gerado local ou pela
    #       rede — tom uniforme, sem o par dual-tone que codifica a tecla. E em modo
    #       CTI a gravação é do PABX: **não é superfície da plataforma**. O que
    #       sobrevive do argumento é só a NIV-07 — out-of-band é NEGOCIADO no SDP, e
    #       garantia sem mecanismo que a imponha é promessa.
    #   (c) ~~**a definição da capacidade exclui voz por construção**~~ — o enum dizia
    #       `password-overlay masked field (webchat)`. **Resolvido em 2026-09-15**
    #       (NIV-05): a definição passou a ser a GARANTIA, aplicada quando o webrtc a
    #       reivindicou. Para `voice` sobram (a) e (b).
    #
    # ⚠️ Nada disto restringe o TRATAMENTO de eco em voz, que é outro eixo e está
    # intacto: `EchoMode` (`plain` verbaliza o dígito · `masked` bipa · `none` cala)
    # é traduzido pelo adapter, e o eco existe justamente para dar feedback de tecla.
    # O que a linha abaixo nega é ELEIÇÃO — voz não é escolhida para COLETAR um campo
    # mascarado —, não a capacidade de tratar o eco.
    "voice":     frozenset({"audio", "masked_input"}),
    "webchat":   frozenset({"text", "file_upload", "rich_menu", "masked_input"}),
    # `masked_input` desde 2026-09-15 (VOZ-05, fatia A), pela GARANTIA da NIV-05 — o valor
    # não aparece em superfície de leitura controlada pela plataforma:
    #   · o valor entra por campo protegido do widget (`webrtc.menu_submit`), e o histórico
    #     do Console é projeção do stream, onde o bridge grava a resposta REDIGIDA
    #     (`masked_field_echo`; ALW-18 — antes era uma lista própria do gateway);
    #   · durante a coleta, a fala transcrita e o texto livre NÃO são publicados — o bridge
    #     os entregaria ao menu como o valor, em claro (`WebRTCAdapter._masked_capture_active`);
    #   · gravação: a plataforma ainda não grava WebRTC (egress é a VOZ-06). O dia em que
    #     gravar, a gravação PAUSA no bloco mascarado, ou esta linha deixa de ser verdade.
    # ⚠️ O que a garantia NÃO cobre, por não ser superfície de leitura da plataforma: o
    # áudio ao vivo que um humano em conferência ouve se o cliente DISSER o valor. Isolar a
    # perna no bloco mascarado é o controle (2) da NIV-07, e chega com a coleta falada.
    # ⚠️ `file_upload` SAIU em 2026-09-17 (VOZ-12): estava declarado e o adapter não tem
    # caminho de upload nenhum (zero ocorrências de `upload` em `adapters/webrtc.py`) — um
    # `collect` com `requires: ["file_upload"]` podia ELEGER o webrtc e não entregar, que é a
    # V8 (capacidade declarada é capacidade verificada) pelo avesso. Sair custou ZERO eleição:
    # nenhum skill vivo declara esse requisito hoje. Volta quando existir o fluxo de dois
    # estágios do webchat aqui — ficha `VOZ-28`, com o `AttachmentStore` inteiro a reusar.
    "webrtc":    frozenset({"text", "audio", "video", "masked_input"}),
    # Entraram na NIV-01 para que a ausência deixasse de ser silenciosa. As duas são
    # canais de mensagem com mídia; nenhuma tem superfície de entrada mascarada.
    "instagram": frozenset({"text", "file_upload"}),
    "telegram":  frozenset({"text", "file_upload", "rich_menu"}),
    # `webhook` é o canal de WORKFLOW (Arc 19): não há cliente do outro lado, e por
    # isso ele não tem capacidade de interação nenhuma. Declarado VAZIO de propósito —
    # omiti-lo devolveria a ausência silenciosa que esta tabela existe para fechar.
    "webhook":   frozenset(),
    # `a2a` (AAS-01 canal, AAS-06 adapter — `a2a_tasks.py`): `text` (TextPart nos dois sentidos) e
    # `rich_menu` (menu → INPUT_REQUIRED com o prompt numerado E um DataPart com o JSON Schema da
    # resposta). `masked_input` NÃO: agente de terceiro não é portador de dado de cartão ou senha
    # (ADR D8), e sem a capacidade o `notification_send` recusa o menu mascarado antes de publicar —
    # o fluxo segue o `on_failure`. O link fora de banda é ficha própria. `file_upload` também não:
    # arquivo em Part espera o arco de anexos (AAS-12).
    "a2a":       frozenset({"text", "rich_menu"}),
}

# Priority ordering when no preference is set (most capable → least).
# Channels not listed fall to the end.
# ⚠️ Canal FORA desta lista cai no fim (`priority.get(ch, len(...))`) — o que é um
# desempate por acidente, não por decisão. `instagram`/`telegram` entram aqui pelo
# mesmo motivo que entraram na tabela acima. `webhook` fica fora de propósito: ele não
# tem capacidade nenhuma, logo nunca é eleito, e listá-lo sugeriria que poderia ser.
# Canais SÓ DE ENTRADA (AAS-06): têm capacidade de interação, mas a plataforma não tem como
# ALCANÇAR alguém por eles — no `a2a` quem abre a conversa é o agente de fora, e não existe cliente
# A2A de saída. Por isso nunca entram na eleição do `collect` (`_negotiate_channel` os recusa
# nomeando) nem precisam de prioridade. DECLARADO, e não deduzido da ausência na lista abaixo: a
# omissão silenciosa é o "desempate por acidente" que o ramo D do gate existe para pegar.
INBOUND_ONLY_CHANNELS: frozenset[str] = frozenset({"a2a"})

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


# `select_channel` SAIU em 2026-09-30 (WFL-02): o único chamador era o consumidor de
# `collect.events`, que nunca recebeu evento. A eleição viva é
# `WebhookAdapter._negotiate_channel`, que usa `channel_satisfies` acima.


# REMOVED (Arc 19 Fase F) — Journey entity eliminated
# read_journey_channel_context, write_journey_channel_context,
# get_journey_contact_id, write_journey_pending_collect
