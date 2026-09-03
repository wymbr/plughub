/**
 * channel-capabilities.ts — o mapa canal → capacidades, e o predicado que o lê.
 *
 * NIV-03 (ADR `adr-agent-flow-single-authored-level.md` § F3).
 *
 * ── POR QUE ESTE ARQUIVO EXISTE, um dia depois da NIV-01 ────────────────────
 *
 * A NIV-01 fechou **duas casas em uma** e escolheu a de Python
 * (`channel_capability_registry.CHANNEL_CAPABILITIES`), com o argumento — que
 * continua de pé — de que capacidade é fato do **PROTOCOLO**, não config de
 * tenant. Este arquivo **não reverte** aquela decisão; ele conserta o que ela
 * não tinha como ver: *a casa única precisa ser LEGÍVEL por toda linguagem que
 * decide sobre capacidade*, e o mapa estava numa que dois decisores não falam.
 *
 * Os decisores que apareceram na NIV-03 são TypeScript:
 *   · `mcp-server-plughub` — `notification_send` é o **único** ponto por onde um
 *     menu mascarado sai para o canal do cliente. Recusar ali devolve `isError`,
 *     que o `menu` step já converte em `on_failure` — sem protocolo novo, sem
 *     escritor novo em `menu:result:{sid}` (chave com histórico documentado de
 *     bug silencioso) e, sobretudo, **sem publicar nada em Kafka**.
 *   · `agent-registry` — a metade de DEPLOY da NIV-03 decide sobre
 *     `Pool.channel_types` no `set-next`/`promote`.
 *
 * As alternativas foram pesadas e descartadas:
 *   (a) **copiar o dicionário para TS** — é literalmente o defeito que a NIV-01
 *       removeu, e sem gate seria pior que o original (que ao menos tinha
 *       vocabulários distintos, o que denunciava a duplicação);
 *   (b) **endpoint HTTP no channel-gateway** — põe dependência de rede dentro do
 *       `notification_send`, que é caminho quente, e obriga a decidir a política
 *       de indisponibilidade em cada consumidor, uma vez por consumidor.
 *
 * ── A regra desta casa ──────────────────────────────────────────────────────
 *
 * **O CANÔNICO é este arquivo.** `channel_capability_registry.py` continua
 * existindo — o channel-gateway é Python e não vai importar TS — mas passa a ser
 * **gêmeo DECLARADO**, com paridade imposta por
 * `infra/test/probe_channel_capability_single_house.sh` (ramo F). É o mesmo
 * arranjo de `py-contextstore`: um canônico, um gêmeo, e um gate que CONTA a
 * duplicação em vez de deixá-la envelhecer em silêncio.
 *
 * O mapa mora aqui, e não em `skill.ts`, porque `ChannelCapabilitySchema` (o
 * vocabulário) e `ChannelSchema` (o domínio) vivem em arquivos diferentes; um
 * terceiro arquivo que importa os dois é o único lugar que não cria ciclo.
 */

import type { Channel } from "./common"
import type { ChannelCapability } from "./skill"

/**
 * Capacidade é fato do PROTOCOLO. Ninguém faz o SMS suportar campo de senha
 * marcando um booleano — e um campo configurável convida exatamente isso.
 *
 * ⚠️ **A tabela é EXAUSTIVA sobre `ChannelSchema`**, e o gate impõe isso nos dois
 * lados. Antes da NIV-01 ela tinha 6 chaves para 9 canais: `instagram` e
 * `telegram` caíam num default vazio, satisfaziam requisito nenhum e **nunca eram
 * eleitos, em silêncio**. Restritivo é o default certo; mudo não é.
 */
export const CHANNEL_CAPABILITIES: Readonly<Record<Channel, readonly ChannelCapability[]>> = {
  whatsapp:  ["text", "file_upload", "rich_menu"],
  sms:       ["text"],
  email:     ["text", "file_upload"],
  // ⚠️ `voice` NÃO declara `masked_input`, e é decisão com impedimentos EMPILHADOS
  // (canal não provisionado · tratamento de eco inexistente no adapter · negociação
  // out-of-band não asserida · `input_mode: voice` não recusado · a própria
  // definição da capacidade, que descreve o MECANISMO e nomeia o webchat).
  // O detalhe e os ids das fatias (NIV-05..08) estão no gêmeo Python, que é onde a
  // discussão nasceu — repetir aqui criaria duas versões do mesmo raciocínio.
  voice:     ["audio"],
  webchat:   ["text", "file_upload", "rich_menu", "masked_input"],
  webrtc:    ["text", "audio", "video", "file_upload"],
  instagram: ["text", "file_upload"],
  telegram:  ["text", "file_upload", "rich_menu"],
  // `webhook` é o canal de WORKFLOW (Arc 19): não há cliente do outro lado, logo
  // não tem capacidade de interação nenhuma. Declarado VAZIO de propósito —
  // omiti-lo devolveria a ausência silenciosa que esta tabela existe para fechar.
  webhook:   [],
} as const

/**
 * `true` se *channel* suporta TODAS as capacidades de *requires*.
 *
 * Canal desconhecido satisfaz apenas a lista vazia — restritivo por construção, e
 * é o comportamento que a tabela exaustiva torna inalcançável na prática.
 */
export function channelSatisfies(
  channel:  string,
  requires: readonly string[],
): boolean {
  if (requires.length === 0) return true
  const caps = (CHANNEL_CAPABILITIES as Record<string, readonly string[]>)[channel]
  if (!caps) return false
  return requires.every((r) => caps.includes(r))
}

/** Capacidade exigida por qualquer coleta de valor mascarado. */
export const MASKED_INPUT: ChannelCapability = "masked_input"

/** Canais que sabem coletar valor mascarado — hoje, só o webchat. */
export function maskingChannels(): Channel[] {
  return (Object.keys(CHANNEL_CAPABILITIES) as Channel[])
    .filter((c) => CHANNEL_CAPABILITIES[c].includes(MASKED_INPUT))
}
