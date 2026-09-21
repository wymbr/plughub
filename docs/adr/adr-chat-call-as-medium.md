# ADR: Chamada é MEIO de um contato de chat — o webchat fica, e o caminho de mensagens é um só

**Status:** Aceito (decisão do dono, 2026-09-21)
**Data:** 2026-09-21
**Componentes:** `packages/channel-gateway` (`adapters/webchat.py`, `adapters/webrtc.py`,
`stream_subscriber.py`, `outbound_consumer.py`), widgets de demo em `infra/demo/web/`,
`packages/agent-registry` (`pool.media_policy`, `channel_types`).
**Relacionado:** [`adr-webchat-channel.md`](adr-webchat-channel.md) (modelo de stream híbrido do webchat),
[`adr-voice-media-plane.md`](adr-voice-media-plane.md) (Arc 15, plano de mídia),
`docs/arcos/arc15-webrtc.md`.

---

## Contexto — dois canais de texto no mesmo site, com dois caminhos

A pergunta do dono foi se o widget WebRTC compartilha o tratamento de mensagens do webchat. Medido no
código em 2026-09-21, **não compartilha** — só a redação do histórico de campo mascarado
(`menu_result_history_text`, que o webrtc importa do webchat) é a mesma casa:

| | webchat | webrtc |
|---|---|---|
| entrega ao cliente | lê o **stream canônico** com cursor (`StreamSubscriber`) | recebe **empurrado** do Kafka (`OutboundConsumer`: só texto, menu, digitando, fim) |
| queda da conexão | reconecta com o mesmo token; a sessão continua e o cursor entrega o que chegou | `WebSocketDisconnect` encerra o contato (`customer_disconnect`); o que chega com o socket caído se perde |
| anexos | upload em 2 etapas + `msg.image/document/video` | nenhum, nos dois sentidos |
| opções em árvore | seções, resposta pelo CAMINHO | só o 1º nível; responde o id da pasta (o beco que o webchat já fechou) |
| presença do agente | `presence.agent_joined/left` | não |
| dependência do SFU | nenhuma | recusa na porta sem plano de mídia (`media_plane_unavailable`, VOZ-01) e cria sala no SFU a cada contato atribuído |

Dois canais para o mesmo site fazem todo defeito de mensagem ser consertado duas vezes — e já foi
(timestamp da mensagem, aviso antes do fechamento, formato plano do menu, cada um com sua data).

## A pergunta que decide

*"O que o webchat tem que o webrtc em modo texto não possa ter?"* Tecnicamente nada — são dois
WebSockets no mesmo gateway. Mas duas diferenças não são defeitos, e sim **ciclo de vida**:

1. **Texto é longo e retomável; chamada é curta e cair é desligar.** A semântica de chamada imposta ao
   texto encerra conversa por F5, troca de rede ou aba suspensa.
2. **Texto não pode depender do plano de mídia.** Com o SFU fora, o cliente que só queria digitar fica
   sem atendimento; e cada contato de texto custa uma sala no SFU que ninguém usa.

O dono acrescentou o critério que fecha a questão: **custo de processamento e escalabilidade** — sala,
bot leg e SFU são recurso caro, e devem existir só enquanto há chamada.

## Decisão

1. **O canal `webchat` fica e é o contato do site**: texto, persistente, anexos, retomável, sem SFU.
2. **Chamada é MEIO que o contato ganha**, não canal: o cliente pede a chamada, a sala nasce ali e
   fecha quando a chamada acaba; a conversa segue em texto. É o `channel × medium` do `CLAUDE.md`
   aplicado ao site — canal é filtro de roteamento, meio é negociado.
3. **O caminho de mensagens é UM só** — o do webchat (stream canônico + cursor + anexos + árvore +
   presença), com o mesmo protocolo de WebSocket. O que for específico de chamada (`ready`, `media`,
   `hangup`, teclado de coleta, pausa de mídia) é camada sobre ele, nunca um segundo caminho de texto.
4. **O widget é um só**: o chat do webchat com um botão de chamada. Continua fora do `platform-ui`
   (ver `infra/demo/web/README.md`); onde mora o widget de PRODUTO é decisão de uma ficha própria.
5. **`voice` (SIP) não muda.** O canal `webrtc` fica até se decidir se existe entrada que já nasce
   chamada (*click-to-call*); não se aposenta nesta decisão.

## Consequências

- **Roteamento:** um pool `webchat` que aceite chamada precisa de `media_policy` e de atendentes com
  áudio. Contato roteado como chat pode cair num atendente sem voz — a política tem de dizer o que se
  oferece (recusar a chamada, transferir, ou não oferecer o botão). **Ausência de política nunca vira
  permissão** (VOZ-10), e isso continua valendo.
- **Relatórios e gravação:** a chamada vira um trecho dentro de um contato de chat, não um contato
  próprio. Gravação e política de mídia já são do participante e do pool (VOZ-06, VOZ-09), o que ajuda;
  a leitura por canal nos relatórios muda e tem de ser dita.
- **Fala da IA:** hoje ela nasce na entrega por Kafka, com a regra de falar ANTES de qualquer `await`
  para não inverter a ordem. Mover o texto para o leitor do stream muda a ordem de entrega — tem de ser
  medido, não suposto.
- **Perna SIP:** usa o adapter webrtc sem tela. Tirar dele o caminho de texto não pode quebrar a coleta
  por teclado e fala do telefone.
- **Não fazer antes:** anexos e árvore no webrtc-texto **isolados** — seriam a terceira correção em
  duplicata de um caminho que esta decisão elimina.

## Implementação

**WCH-01 (2026-09-21) — a chamada se PRENDE ao contato de chat.** A primeira versão da ficha era
fazer o canal `webrtc` falar o protocolo de mensagens do webchat; a leitura do adapter mostrou que,
no alvo desta decisão, a conexão da chamada não carrega texto nenhum — convergir o texto do `webrtc`
avulso seria trabalho num caminho que a `WCH-04` pode aposentar. Redefinida com o dono: uma segunda
conexão, só de chamada, presa à sessão do chat (`/ws/call`). Detalhe em `arc15-webrtc.md` § 20.

## Fichas

`WCH-01` fechada (`done.md`); `WCH-02..04` em `pending.md`, sob este documento.
