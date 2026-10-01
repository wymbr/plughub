# Channel Gateway — Adapter WebChat

> Última atualização: 2026-05-25 · Estado: Arc 16

> Spec de referência: seção 3.5
> Módulo pai: [channel-gateway.md](channel-gateway.md)
> ADR: [`docs/adr/adr-webchat-channel.md`](../adr/adr-webchat-channel.md)

---

## Visão geral

O adapter webchat implementa comunicação bidirecional via WebSocket com o cliente
no browser. É um dos três canais distintos baseados em browser/SFU: `webchat`
(WebSocket), `webrtc` (LiveKit SFU) e `whatsapp` (Meta Cloud API) — cada um com
adapter próprio.

Por ser um canal com suporte nativo completo, o adapter webchat nunca executa
coleta sequencial de menu — todos os tipos de interação (`text`, `button`, `list`,
`checklist`, `form`) são renderizados diretamente e retornam um único submit.

**Modelo de stream híbrido:** o cliente **não** é um participante nomeado da
sessão. O Channel Gateway faz `XREAD` diretamente sobre `session:{id}:stream`
(o canonical stream) para entregar eventos ao cliente, e publica as mensagens
inbound do cliente na plataforma. A reconexão usa um cursor sobre o stream —
zero mensagens perdidas quando o cliente cai e volta.

---

## Protocolo WebSocket

### Endpoint

```
GET /ws/chat?contact_id={uuid}   ← contato existente (reconexão)
GET /ws/chat                     ← novo contato (servidor gera contact_id)
```

O servidor retorna o `contact_id` na mensagem `connection.accepted` imediatamente
após a conexão. O cliente usa esse ID para reconectar em caso de queda.

### Autenticação — JWT via corpo da mensagem

O JWT é transmitido **no corpo da mensagem WebSocket**, nunca na URL (evita
vazamento em logs de proxy / histórico). O `jwt_secret` é resolvido por tenant
via Redis (`{tenant_id}:config:webchat:jwt_secret`).

### Reconexão por cursor

Na reconexão, o cliente envia o último `event_id` recebido. O adapter retoma o
`XREAD` do stream a partir desse cursor — todas as mensagens emitidas durante a
desconexão são entregues em ordem. Zero perda de mensagens.

### Tasks concorrentes do adapter

O `WebchatAdapter` mantém 3 tasks assíncronas por conexão:

| Task | Papel |
|---|---|
| `receive_loop` | Recebe eventos do cliente, normaliza, publica inbound |
| `stream_delivery_loop` | `XREAD` sobre `session:{id}:stream`, entrega ao cliente |
| `typing_listener` | Propaga indicadores de digitação |

---

## Upload de anexos — fluxo de 2 estágios

Anexos (imagem, documento, vídeo) usam um handshake de 2 estágios:

```
1. WS  upload.request    → cliente solicita upload (nome, mime, tamanho)
2. WS  upload.ready      → servidor responde com { file_id, upload_url }
3. HTTP POST binário     → cliente envia o arquivo bruto para upload_url
4. WS  upload.committed  → servidor confirma persistência
5. WS  msg.image / msg.document / msg.video → mensagem com o anexo entra no stream
```

**MIME allowlist:**

| Tipo | Formatos | Limite |
|---|---|---|
| Imagem | JPEG, PNG, WebP, GIF | 16 MB |
| Documento | PDF | 100 MB |
| Vídeo | MP4, WebM | 512 MB |

**Expiração:** soft-delete a cada hora; delete físico diário (com +24h de grace). O prazo é a classe
**`retention.attachment_days`** (ATT-04, 2026-10-01; era `webchat.attachment_expiry_days`), editada em
Mascaramento → Retenção junto das outras classes. Ele é carimbado no anexo quando o anexo chega
(`expires_at`), então mudar o valor vale para os **próximos** anexos. Com o config-api fora, o
gateway usa o último valor carregado, ou 30, e diz qual no log.

### A regra vale para TODO escritor, e a porta não serve página (ATT-01, 2026-10-01)

A tabela acima é a do **upload do webchat**. O que pode ser **gravado** é decidido por **classe de
artefato**, num lugar só (`attachment_store.validate_content`, chamado pelo `commit` dos dois
backends):

| Classe | Aceita | Quem grava |
|---|---|---|
| `webchat_attachment` | a tabela acima + `audio/ogg` (nota de voz do WhatsApp) | webchat, WhatsApp, e-mail |
| `call_recording` | `audio/ogg` | egress da chamada (VOZ-06) |

- **Tamanho é o REAL** (bytes recebidos), nunca o declarado no `reserve`.
- **Magic bytes são fail-closed**: tipo sem assinatura conhecida é recusado.
- **O MIME é normalizado** (`audio/ogg; codecs=opus` → `audio/ogg`).
- **Classe desconhecida não grava nada**: classe nova entra na tabela junto com o escritor dela.
- WhatsApp e e-mail conferem **antes** do `reserve`, para não deixar slot pendente órfão. A mensagem
  do WhatsApp segue **sem arquivo** (`file_id: null`); o anexo de e-mail recusado sai da lista. Nos
  dois, o motivo vai ao log em `WARNING`.

Até a ATT-01, WhatsApp e e-mail gravavam o MIME que o **remetente** declarava, e o magic byte
aceitava tipo sem assinatura: um `text/html` recebido saía pela porta pública como página, na origem
do gateway. Outros áudios (`audio/mpeg`, `audio/mp4`, `audio/amr`) são **recusados** até haver quem
os consuma.

**A porta pública** (`GET /webchat/v1/attachments/{file_id}`):
- manda `X-Content-Type-Options: nosniff` e `Content-Security-Policy: sandbox; default-src 'none'`;
- só exibe **imagem** `inline`; PDF e vídeo saem como **download** (`attachment`). O link "abrir"
  do Console baixa o PDF em vez de abri-lo numa aba; `<img>` e `<video>` continuam funcionando,
  porque a disposição só vale para navegação;
- leva o nome em ASCII seguro mais `filename*` (RFC 5987), porque o nome é do remetente e, entre
  aspas, reescrevia o cabeçalho;
- serve tipo fora da allowlist (linha antiga) como `application/octet-stream`.

### A porta pública só abre com URL ASSINADA (ATT-03, 2026-10-01)

O `file_id` nu deixou de ser credencial. A URL entregue ao cliente é
`/webchat/v1/attachments/{file_id}?exp=…&sig=…`:

- **A assinatura** é HMAC-SHA256 sobre `(file_id, session_id, exp)`, com separação de domínio e o
  segredo do webchat (`PLUGHUB_JWT_SECRET`).
- **O `session_id` não aparece na URL.** A porta o lê do registro do anexo, então um link de uma
  sessão não abre arquivo de outra.
- **Vale 1 h** (`ATTACHMENT_URL_TTL_S`).
- **Respostas da porta:** sem assinatura, ou com assinatura errada, 404, igual a id desconhecido
  (sem oráculo); vencida, 403 `link_expired`.
- **A URL é cunhada na ENTREGA, nunca copiada da gravada.** São três lugares: o `upload.committed`,
  a mídia do webchat (`_handle_media`) e o `StreamSubscriber`, que recebe um `url_signer` e recunha
  pelo `file_id` a cada entrega ao widget. Uma reconexão, portanto, renova os links.
- **Sem segredo, não há URL**: o anexo vai sem link, e o motivo vai ao log em `ERROR`. Nunca sai uma
  URL nua.

A `url` que já estava gravada no histórico durável e no Kafka deixa de abrir, de propósito: nenhum
leitor da plataforma a usa desde a ATT-02. De passagem, o cliente de teste `infra/webchat-client`
deixou de mandar a URL como legenda, o que a fazia parar no texto do transcript.

### Quem atende vê pela porta INTERNA (ATT-02, 2026-10-01)

Console e transcript não usam mais a porta pública. Antes abriam a `url` gravada na mensagem:
absoluta para `:8010`, num `<img src>` sem credencial, durável no stream, no Postgres e no Kafka.
Hoje:

```
Console ── apiFetch (Bearer) ──▶ /analytics/v1/attachments/{file_id}   (borda 5174 → analytics-api)
                                   1. contacts.transcricao ANTES de resolver o id   → 403
                                   2. meta no gateway  (/v1/attachments/{id}/meta, X-Service-Token)
                                   3. authorize_session_scope(pool da SESSÃO)       → 403
                                   4. bytes no gateway (/v1/attachments/{id}/content)
                                   todo desfecho → audit_access_log (ok · denied · not_found · expired · pending_scan · unavailable)
```

- **A capacidade é `contacts.transcricao`** (decisão do dono, 2026-10-01): o anexo é conteúdo da
  conversa, e quem lê a transcrição vê o anexo. Não há campo novo nem backfill.
- **O escopo é o da SESSÃO**, a mesma resposta do transcript. `session_attachments` não tem pool.
- **A rota interna do gateway é só de serviço.** Usuário com grant recebe 403 ali: ela não é
  porta alternativa para quem a analytics-api recusaria.
- **O componente `AttachmentView`** (`platform-ui/src/components`) monta o caminho pelo `file_id`.
  Imagem vira miniatura (clicar abre numa aba); o resto vira "Baixar".

Gate: `probe_att02_attachment_internal_door.sh`, com bateria em `mut_att02_attachment_internal_door.sh`.

Gate: `infra/test/probe_att01_attachment_door.sh`, com bateria em `mut_att01_attachment_door.sh`.

### Esteira de ingestão: re-codifica, hash, antivírus (ATT-05, 2026-10-01)

Todo `commit` de anexo de **contato** (`webchat_attachment`: webchat, WhatsApp, e-mail) passa por
`attachment_store.prepare_content`, nos dois backends:

1. allowlist da classe, tamanho real e assinatura (ATT-01);
2. **imagem RE-CODIFICADA** (`media_sanitize.py`, Pillow): sai o EXIF (GPS, aparelho), a orientação
   é aplicada aos pixels, e a carga de arquivo poliglota não sobrevive. GIF animado mantém os
   quadros. Imagem que não decodifica, ou acima de 50 Mpx (conferido no cabeçalho), é RECUSADA;
3. **sha256** do que vai ser gravado (`session_attachments.sha256`);
4. **antivírus** (`antivirus.py`, INSTREAM no `clamav` do compose, porta 3310):

| Veredicto | O que acontece | `scan_status` | Servido? |
|---|---|---|---|
| limpo | grava | `clean` | sim |
| infectado | **não grava**; a linha fica `rejected` com o motivo; upload responde 422 `attachment_infected` | `infected` | não (404) |
| não deu para perguntar (fora do ar, não configurado, timeout) | grava em **QUARENTENA**, com o motivo em `attrs.scan_reason` | `quarantined` | não (**423** `attachment_pending_scan`) |

- **Só a nova varredura libera a quarentena** — a task de boot `attachment-rescan`
  (`attachment_rescan.py`, a cada 60 s, lote de 20). Ela também varre o anexo anterior à ATT-05
  (`scan_status` NULL), que não é servido até passar: chamá-lo de limpo seria o valor plausível.
  O anexo anterior é só varrido, **não** re-codificado: o EXIF dele fica até expirar.
- **A regra de servir mora numa casa** (`serve_refusal`), usada pelas duas portas — a pública e a
  interna. A analytics-api repassa o 423 (trilha `pending_scan`) e o Console mostra *"Em
  verificação pelo antivírus"*.
- **A gravação de chamada é isenta, dito**: saída da própria plataforma (egress do SFU), fica com
  `scan_status` NULL e é servida pela regra da classe.
- **O gateway não depende do `clamav` no compose.** A primeira carga da base leva minutos; enquanto
  isso o anexo vai à quarentena e sai depois. `PLUGHUB_CLAMAV_HOST` vazio loga ERROR no boot.
- O clamd vem com teto de 100 MB por stream; `infra/clamav/clamd.conf` sobe para 600 MB, porque a
  allowlist aceita vídeo de 512 MB.
- ⚠️ O EICAR só é achado sozinho ou dentro de um stream de PDF: anexado ao fim de um PNG ou de um PDF
  ele passa limpo (medido). A esteira não depende disso — a re-codificação tira a carga da imagem.

Gate: `infra/test/probe_att05_ingest_pipeline.sh`, com bateria em `mut_att05_ingest_pipeline.sh`.

---

## Masked fields delivery chain

Quando um step `menu` declara campos mascarados, a cadeia de entrega é:

```
step.masked
  → notification_send args
  → entrada `interaction_request` no stream canônico (payload.masked_fields)
  → StreamSubscriber._map → dict `interaction.request` (stream_subscriber.py, repassa masked_fields)
  → webchat.py send_json (sem modelo tipado)
  → <input type="password"> overlay no webchat
```

Em paralelo, o `menu.payload` em `conversations.outbound` (Kafka) chega ao `OutboundConsumer`,
que guarda os `masked_fields` no `SessionRegistry` para o `_handle_menu_submit` redigir a resposta.

> ⚠️ **Corrigido em 2026-09-23 (ORQ-08).** A cadeia passava por um `WsMenuRender.masked_fields` que
> nenhum código construía — o modelo existia só em `models.py` e no próprio teste, e saiu. O
> contrato de verdade é o mapeamento do `StreamSubscriber`, testado em
> `test_stream_subscriber.py::test_interaction_request_carries_masked_fields_ORQ08`. Esse
> mapeamento **não repassa `masked_types`**, e nenhum widget o lê (ver `ALW-15`).

O cliente renderiza os campos mascarados como `<input type="password">`. Os valores
nunca trafegam em claro nem são persistidos no stream sem mascaramento.

---

## Eventos WebSocket — cliente → servidor

### Mensagem de texto

```json
{ "type": "message.text", "text": "Quero verificar minha portabilidade" }
```

### Submit de menu

```json
{
  "type": "menu.submit",
  "menu_id": "uuid",
  "interaction": "button | list | checklist | form",
  "result": "string | string[] | object"
}
```

| `interaction` | Tipo de `result` | Exemplo |
|---|---|---|
| `button` | `string` (option id) | `"opt_portabilidade"` |
| `list` | `string` (option id) | `"opt_portabilidade"` |
| `checklist` | `string[]` (option ids) | `["opt_a", "opt_c"]` |
| `form` | `object` (field → valor) | `{"nome": "João", "cpf": "123"}` |

### Upload

`upload.request` → servidor responde `upload.ready`. Após o POST binário, `upload.committed`.

---

## Eventos WebSocket — servidor → cliente

### Mensagem de texto

```json
{
  "type": "message.text",
  "message_id": "uuid",
  "author": { "type": "agent_human | agent_ai | system", "display_name": "..." },
  "text": "Olá, como posso ajudar?",
  "timestamp": "2026-05-25T14:00:00Z"
}
```

### MenuPayload — renderização de menu interativo

```json
{
  "type": "menu.render",
  "menu_id": "uuid",
  "interaction": "button | list | checklist | form | text",
  "prompt": "Qual é o motivo do contato?",
  "options": [
    { "id": "opt_portabilidade", "label": "Portabilidade" },
    { "id": "opt_cobranca",      "label": "Cobrança" }
  ],
  "fields": null,
  "masked_fields": []
}
```

Para `interaction: form`, `options` é null e `fields` contém a definição dos campos.
`masked_fields` lista os campos que o cliente deve renderizar como `<input type="password">`.

### Confirmação de conexão / indicador de digitação

```json
{ "type": "connection.accepted", "contact_id": "uuid", "session_id": "uuid" }
{ "type": "agent.typing", "author_type": "agent_human | agent_ai" }
```

---

## Estado Redis

O adapter webchat usa Redis para mapear `contact_id` → conexão WebSocket ativa
(necessário para entrega outbound correta em ambientes multi-instância) e para
resolver o `jwt_secret` do tenant.

```
key:   webchat:session:{contact_id}
value: { instance_id, connected_at }
TTL:   duração máxima do contato (default: 4h)

key:   {tenant_id}:config:webchat:jwt_secret
value: segredo HS256 do tenant
```

Não acessa `pipeline_state`. Não acessa estado de avaliação.

---

## O que o adapter webchat não faz

- Não autentica o cliente como usuário do PlugHub — valida apenas o JWT de canal
- Não persiste mensagens no transcript — o canonical stream é a fonte; o Stream Persister persiste no `session_closed`
- Não roteia conversas — publica em Kafka e o Routing Engine decide
- Não executa coleta sequencial — web chat tem suporte nativo a todos os tipos
- Não conhece o estado do Skill Flow ou do pipeline

---

## Relações com outros módulos

| Módulo | Relação |
|---|---|
| `session:{id}:stream` (Redis) | `XREAD` direto — fonte de entrega outbound ao cliente |
| `conversations.inbound` (Kafka) | Publica todas as mensagens e submits normalizados |
| `conversations.outbound` (Kafka) | Consome MenuPayloads e eventos para entrega |
| `Routing Engine` | Consome `conversations.inbound` para alocação de agente |
| `Stream Persister` | Persiste o stream em PostgreSQL no `session_closed` |
