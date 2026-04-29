# ADR: WebChat Channel — Canal de Mensagens Web com Modelo Híbrido de Stream

**Status:** Implementado  
**Data:** 2026-04-21  
**Componentes:** `packages/channel-gateway` (`adapters/webchat.py`, `stream_subscriber.py`, `attachment_store.py`, `upload_router.py`)

---

## Contexto

O PlugHub precisava de um canal de mensagens web (browser) que suportasse:
- Texto, imagens, documentos, vídeo (fase 1) e futuramente voz/vídeo em tempo real (fase 2)
- Reconexão sem perda de mensagens
- Upload de arquivos com controle de expiração sincronizado com o TTL da sessão
- Typing indicators efêmeros (não persistidos)
- Integração com o modelo de sessão como sala de conferência já existente na plataforma

Cinco decisões de arquitetura foram necessárias, cada uma com alternativas consideradas.

---

## Decisão 1 — Três canais distintos (webchat / webrtc / whatsapp)

### Opções consideradas

| Opção | Descrição |
|---|---|
| A | `webchat` + `webrtc` como um canal único "browser" |
| B | `webchat` + `webrtc` + `whatsapp` como canal único "mensagens" |
| **C** | **Três canais completamente separados** |

### Por que não A nem B

`channel` é um filtro hard no algoritmo de roteamento — um agente do pool `suporte_voz` não
pode atender um contato `webchat` e vice-versa. Essa é uma invariante da plataforma (ver CLAUDE.md).

Fundir canais forçaria o Routing Engine a introduzir um sub-tipo para fazer a distinção que o
`channel` faz hoje, recriando o problema dentro do conceito que deveria resolvê-lo. Além disso,
`webrtc` (tempo real, mídia, SLA de latência em milissegundos) e `webchat` (mensagens, SLA em
segundos) têm perfis de agente, ferramentas e comportamentos radicalmente diferentes.

### Decisão

Três canais distintos. Cada um tem seu próprio adapter, suas próprias rotas WebSocket/HTTP,
e seu próprio `channel` no evento de roteamento. A separação é permanente.

---

## Decisão 2 — WebSocket puro como protocolo de transporte

### Opções consideradas

| Opção | Prós | Contras |
|---|---|---|
| **Socket.IO** | Reconexão automática, ampla adoção | Reconexão própria conflita com cursor-based (D4); abstração desnecessária |
| **STOMP** | Protocolo padronizado, pub/sub nativo | Overhead de protocolo; complexidade desnecessária para este caso |
| **SSE (Server-Sent Events)** | Simples, HTTP nativo | Unidirecional — exige endpoint HTTP separado para cliente→servidor |
| **WebSocket puro** | Bidirecional, sem overhead de protocolo, controle total | Requer implementar handshake de auth e heartbeat manualmente |

### Decisão

WebSocket puro com **envelope tipado em JSON**:

```
cliente → servidor: conn.authenticate, msg.text, msg.image, upload.request, menu.submit, conn.ping
servidor → cliente: conn.hello, conn.authenticated, upload.ready, upload.committed, msg.*, conn.session_ended
```

**Token no corpo da mensagem, nunca na URL.** Tokens em query string aparecem em logs de acesso de
nginx/CDN — padrão de segurança exige que credentials não trafeguem em URLs.

O handshake de auth (`conn.hello` → `conn.authenticate` → `conn.authenticated`) é implementado
explicitamente no adapter antes de iniciar as tasks concorrentes. O cursor para reconnect é
retornado no `conn.authenticated` — o cliente sabe exatamente de onde retomar.

---

## Decisão 3 — Storage de anexos: filesystem + PostgreSQL na fase 1

### Opções consideradas

| Opção | Prós | Contras |
|---|---|---|
| S3/MinIO desde o início | Escala horizontal, sem disco local | Infraestrutura adicional, complexidade de setup |
| **Filesystem local + PostgreSQL** | Zero infra extra, simples, expurgo por `rm -rf` | Não escala horizontalmente (fase 2) |

### Decisão

Filesystem local na fase 1, com **interface `AttachmentStore` estável** que isola completamente
o Channel Gateway da implementação:

```python
class AttachmentStore(Protocol):
    async def reserve(...) -> tuple[str, str]    # file_id, upload_url
    async def commit(...) -> AttachmentMeta
    async def resolve(...) -> AttachmentMeta | None
    async def stream_bytes(...) -> AsyncIterator[bytes]
    async def soft_expire(...) -> None
```

A fase 2 (`S3AttachmentStore`) é uma troca de implementação — o adapter não muda.

**Path date-sharded:** `{root}/{tenant_id}/{YYYY}/{MM}/{DD}/{session_id}/{file_id}.{ext}`

A estratificação por data permite expurgo em massa com `rm -rf 2026/01/` sem consultar o banco.
O subdiretório de `session_id` permite limpeza atômica de todos os arquivos de uma sessão expirada.

**Cron de dois estágios:**
- Estágio 1 (horário): `SET deleted_at = NOW() WHERE expires_at < NOW()` → URL retorna 410 Gone imediatamente
- Estágio 2 (diário, grace 24h): delete físico + `SET file_path = NULL`

A grace period de 24h protege contra condições de corrida onde um cliente ainda está
fazendo download quando o soft-delete ocorre.

---

## Decisão 4 — Modelo híbrido: cliente não é participante nomeado da sessão

Esta foi a decisão mais consequente. O modelo de sessão do PlugHub é uma sala de conferência
onde todos os participantes (primary, specialist, supervisor) são registrados e recebem eventos
via suas filas. A questão: **o cliente webchat deve ser registrado como participante?**

### Opção A — Cliente como participante nomeado (rejeitada)

O cliente receberia um `participant_id`, se registraria na sessão via `session_invite` ou
mecanismo equivalente, e receberia eventos pela mesma fila que os agentes.

**Problemas identificados:**

1. **Propagação do role `customer` pelas MCP Tools.** Toda tool que lê participantes da sessão
   (`session_context_get`, `agent_list`, visibility filters) precisaria reconhecer e tratar
   o role `customer` diferentemente dos agentes. Isso afeta código em múltiplos componentes
   que hoje não têm essa preocupação.

2. **Multi-tab é complexo.** Cada aba do browser abriria uma nova conexão WebSocket. Com
   participantes nomeados, haveria dois `participant_id` do mesmo cliente na sessão. A plataforma
   precisaria de semântica de "mesmo cliente, múltiplas conexões", que não existe hoje.

3. **Boundary de segurança.** Um `participant_id` de cliente exposto cria uma superfície de
   ataque: se vazar, alguém poderia tentar usar o `participant_id` para consultar dados da
   sessão via tools que filtram por participante.

4. **Visibilidade `agents_only` já existe.** Os agentes precisam de comunicação privada sem
   o cliente. Se o cliente é participante, toda mensagem `agents_only` precisa explicitamente
   excluir o `participant_id` do cliente. Com o modelo híbrido, `agents_only` é filtrado no
   `StreamSubscriber` antes de chegar ao WebSocket — nenhum agente precisa saber que o cliente
   existe como participante.

### Opção B — Modelo híbrido: XREAD direto no stream canônico (adotada)

O Channel Gateway faz `XREAD BLOCK` diretamente no `session:{id}:stream` Redis Stream para
cada conexão WebSocket ativa. O cliente não existe como participante na sessão.

```
┌──────────────────────────────────────────────────┐
│  WebchatAdapter (por conexão WebSocket)           │
│                                                   │
│  receive_task   ← inbound do cliente              │
│  delivery_task  ← XREAD session:{id}:stream       │
│  typing_task    ← SUB session:{id}:typing         │
└──────────────────────────────────────────────────┘
```

**Vantagens:**

- **Reconnect por cursor, gratuito.** O cliente passa `cursor=<last_event_id>` no
  `conn.authenticate`. O `StreamSubscriber` inicia o XREAD a partir desse cursor.
  Zero mensagens perdidas na reconexão, sem buffer separado, sem lógica de replay adicional.

- **Multi-tab natural.** Cada aba tem sua própria conexão WebSocket e seu próprio cursor.
  O Redis Stream comporta N leitores independentes sem conflito.

- **Typing indicators efêmeros separados.** Indicators de digitação não pertencem ao stream
  canônico (não são eventos de sessão, não precisam ser persistidos). Ficam em
  `session:{id}:typing` via Redis pub/sub — efêmeros por natureza, sem poluir o stream.

- **Zero mudanças nas MCP Tools.** O role `customer` não existe na sessão. Nenhuma tool
  precisa tratá-lo. O `session_context_get`, o `agent_list`, o mascaramento — nada muda.

- **Entrega cross-instance.** O stream Redis é compartilhado entre todas as instâncias do
  Channel Gateway. Qualquer instância pode fazer XREAD na sessão de qualquer cliente — sem
  pub/sub de conteúdo, sem coordenação.

### O único tradeoff do modelo híbrido

Validação em tempo real de formulários (form fields com feedback imediato ao usuário enquanto
preenche) não cabe no modelo de stream de eventos — é inherentemente request/response síncrono.

**Resolução:** Validação de campos fica no WebSocket adapter como exchange direto
(cliente envia campo, adapter valida, devolve feedback imediatamente). Apenas o
`interaction_result` final vai para o stream. Isso vale igualmente para qualquer modelo de
entrega — não é razão para mudar a decisão.

---

## Decisão 5 — Upload em dois estágios via HTTP

### Opções consideradas

| Opção | Prós | Contras |
|---|---|---|
| WS binary frames | Tudo em um canal | Mistura binário com JSON; framing complexo; difícil de rotear no nginx |
| HTTP multipart (único estágio) | Padrão conhecido | Não tem handshake de validação antes do upload; cliente envia antes de saber se é aceito |
| **Dois estágios: reserve via WS, upload via HTTP** | Validação MIME/tamanho antes do envio; nginx pode rotear `/upload/` separado; `file_id` como token de capacidade | Dois round-trips em vez de um |

### Decisão

```
1. WS  → upload.request  {file_name, mime_type, size_bytes}
2. WS  ← upload.ready    {request_id, file_id, upload_url}    ← validação MIME/tamanho aqui
3. HTTP → POST upload_url  (corpo binário)
4. WS  ← upload.committed {file_id, url, mime_type, size_bytes, content_type}
5. WS  → msg.image|document|video {file_id, caption?}
```

O `file_id` (UUID v4) funciona como **capability token** para servir o arquivo via
`GET /webchat/v1/attachments/{file_id}` — sem auth adicional na fase 1 (alta entropia do
UUID é suficiente). Fase 2 adicionará assinatura de URL com expiração.

A separação HTTP para o binário permite que o nginx faça proxy reverso do endpoint de upload
com `client_max_body_size` configurado separadamente dos WebSockets, e que o upload seja
feito via streaming sem buffering no processo Python.

---

## Componentes implementados

| Arquivo | Responsabilidade |
|---|---|
| `adapters/webchat.py` | Auth handshake, 3 tasks concorrentes, handlers de inbound |
| `stream_subscriber.py` | XREAD BLOCK loop, filtro de visibilidade, mapeamento de tipos |
| `attachment_store.py` | Interface `AttachmentStore` + `FilesystemAttachmentStore` (fase 1) |
| `upload_router.py` | `POST /webchat/v1/upload/{file_id}` + `GET /webchat/v1/attachments/{file_id}` |
| `models.py` | Envelope tipado completo (WsAuthenticate, WsAuthenticated, WsUpload*, WsMedia*, ...) |
| `config.py` | Novos campos: jwt_secret, ws_auth_timeout_s, storage_root, attachment_*, database_url |

## Cobertura de testes

| Arquivo | Testes | Cobertura |
|---|---|---|
| `test_webchat_adapter.py` | 28 | Auth handshake, lifecycle, text/media/upload/menu, heartbeat |
| `test_stream_subscriber.py` | 25 | Cursor, visibility filter, todos os tipos de evento, resiliência |
| `test_attachment_store.py` | 30 | validate_mime, reserve, commit, resolve, soft_expire, stream_bytes |

## Pendente (fase 2)

- `S3AttachmentStore`: troca de implementação, interface `AttachmentStore` não muda
- Magic bytes validation no upload (python-filemagic ou filetype)
- Assinatura de URL com expiração para `GET /attachments/{file_id}`
- E2E scenario 12: auth flow completo + upload de mídia end-to-end
