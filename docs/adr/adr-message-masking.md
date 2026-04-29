# ADR: Message Masking — Tokenização com Partial Display

**Status:** Implementado  
**Data:** 2026-04-20  
**Componentes:** `@plughub/schemas`, `mcp-server-plughub`

---

## Contexto

Mensagens trocadas em sessões podem conter dados sensíveis LGPD (CPF, cartão de crédito,
telefone, e-mail). O stream canônico (`session:{id}:stream`) é a fonte de verdade e
precisa proteger esses dados sem quebrar a cadeia operacional do agente AI.

## Decisão

Substituição por **token composto** em vez de mascaramento cego.

### Formato do token no stream

```
[{category}:{token_id}:{display_partial}]
```

Exemplos:

| Dado original            | Token no stream                          |
|--------------------------|------------------------------------------|
| `4539 1234 5678 1234`   | `[credit_card:tk_a8f3:****1234]`         |
| `123.456.789-00`         | `[cpf:tk_b7d2:***-00]`                   |
| `(11) 98765-4321`        | `[phone:tk_c1e9:(11) ****-4321]`         |
| `joao@empresa.com`       | `[email_addr:tk_d4f0:j***@empresa.com]`  |

O `display_partial` segue as regras da `MaskingRule`:
- `preserve_last_digits` — mantém os últimos N dígitos (PCI-DSS: 4 para cartão)
- `preserve_pattern` — regex que extrai o trecho visível (ex: domínio do e-mail)

### O que fica no stream

```
payload.content          → conteúdo tokenizado (entregue ao agente AI)
payload.original_content → conteúdo original (apenas para roles autorizados)
payload.masked           → true quando algum dado foi detectado
payload.masked_categories → categorias LGPD detectadas
```

### Token Vault

Chave Redis: `{tenant_id}:token:{token_id}`  
TTL: igual ao da sessão (padrão 4h)  
Resolve: apenas via MCP Tools autorizadas — nunca exposto diretamente ao agente

## Justificativa

### Por que token e não mascaramento simples (asteriscos)?

Mascaramento simples (`***.***.***-**`) quebra a cadeia operacional: o agente AI
não consegue confirmar dados com o cliente nem passar o valor para uma MCP Tool.

O token composto resolve ambos:
- **Confirmação com cliente**: AI lê `****1234` do token inline — pode dizer
  "seu cartão com final 1234, correto?" sem nenhuma chamada extra
- **Tool calls**: a MCP Tool recebe o token_id, resolve o valor original no vault,
  executa a operação no backend — o CPF/cartão nunca viaja no contexto do agente

### Por que primary/specialist não recebem original_content?

O risco LGPD não é só de leitura humana. Se o AI recebe `original_content`, o dado
pode vazar para logs de inferência, histórico de contexto enviado ao LLM, ou dados
de treinamento. O token isola esse risco: o dado sensível só aparece dentro do
MCP Tool, que tem `audit_policy` própria com `retention_days` configurado e registro
em `mcp.audit`.

### Alinhamento com PCI-DSS

O `preserve_last_digits: 4` para cartões de crédito é exatamente o requisito PCI-DSS
para exibição de números de cartão. A regra de negócio e a regra de compliance
coincidem — sem configuração adicional.

## Componentes implementados

### `@plughub/schemas` — `audit.ts`

- `MaskingRuleSchema`: adicionado campo `preserve_pattern` (regex de extração parcial)
- `MaskingAccessPolicySchema`: roles autorizados a receber `original_content` por tenant
- `DEFAULT_MASKING_RULES`: regras padrão para CPF, cartão, telefone, e-mail

### `mcp-server-plughub` — `lib/token-vault.ts`

- `TokenVault.generate()`: gera token, persiste no Redis com TTL, retorna inline string
- `TokenVault.resolve()`: resolve token_id → valor original (uso exclusivo de MCP Tools)
- `TokenVault.extractTokenIds()`: extrai token_ids de um texto com tokens inline

### `mcp-server-plughub` — `lib/masking.ts`

- `MaskingService.applyMasking()`: aplica regras do tenant (ou defaults), gera tokens
- `MaskingService.canReadOriginalContent()`: verifica autorização por role
- `MaskingService.loadConfig()`: carrega `MaskingConfig` do Redis por tenant
- `MaskingService.loadAccessPolicy()`: carrega `MaskingAccessPolicy` do Redis por tenant

### `@plughub/schemas` — `message.ts`

- `MessageSchema`: adicionado campo `original_content: MessageContentSchema.optional()`
  — necessário para que `SessionContextSchema.parse()` preserve o campo para roles
  autorizados sem descartá-lo como chave desconhecida

### `mcp-server-plughub` — `tools/session.ts`

- `message_send`: aplica `MaskingService.applyMasking` antes de gravar no stream
- `session_context_get`: monta objeto de mensagem completo combinando campos do stream
  (`event_id → message_id`, `timestamp`, `author`, `visibility`) com o `payload`
  (content, original_content, masked, masked_categories) — garante que `MessageSchema`
  seja satisfeito e que `original_content` flua para roles autorizados

## Redis keys

| Key | Conteúdo | TTL |
|-----|----------|-----|
| `{tenant_id}:masking:config` | `MaskingConfig` (regras do tenant) | sem expiração |
| `{tenant_id}:masking:access_policy` | `MaskingAccessPolicy` | sem expiração |
| `{tenant_id}:token:{token_id}` | `TokenEntry` (valor original) | igual à sessão |

## Pendente

- **Token resolution em MCP Tools de domínio**: `mcp-server-crm`, `mcp-server-billing`
  e similares devem importar `TokenVault` do SDK para resolver tokens antes de chamar
  o backend. Sem isso, o agente precisaria passar o token_id explicitamente.
- **Channel Gateway display**: ao renderizar mensagens para o cliente humano na tela,
  o Channel Gateway pode exibir apenas o `display_partial` sem o wrapper `[...]`.
- **Masking config UI**: interface no Agent Registry para o tenant configurar suas
  próprias regras de mascaramento além dos defaults.
