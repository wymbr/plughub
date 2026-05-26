# Módulo: Configuração → Mascaramento

> Última atualização: 2026-05-25 · Estado: Arc 16

> Rota UI: `/config/masking` | Roles: admin

## O que é

O módulo de Mascaramento permite que administradores configurem as regras de proteção de dados sensíveis em mensagens e chamadas MCP. Define quais roles têm acesso ao conteúdo original (não mascarado), políticas de auditoria por ferramenta, e visibilidade dos tokens mascarados por categoria.

## Funcionamento do mascaramento

O mascaramento é aplicado na **escrita** de cada mensagem via `message_send`. O stream canônico armazena dois campos por mensagem:
- `content` — versão mascarada com tokens inline `[cpf:tk_xxx:***-00]`
- `original_content` — versão original acessível apenas por roles autorizados via `session_context_get`

### Formato do token

```
[{category}:{token_id}:{display_partial}]

[credit_card:tk_a8f3:****1234]       → AI confirma "final 1234" com o cliente
[cpf:tk_b7d2:***-00]                 → AI confirma "termina em 00"
[phone:tk_c1e9:(11) ****-4321]
[email_addr:tk_d4f0:j***@empresa.com]
```

Para entrega ao cliente via WebSocket, `_strip_tokens()` extrai apenas o `display_partial` — o cliente nunca vê o wrapper `[...]`.

## Seções da MaskingPage

### Controle de Acesso

Configura `authorized_roles` — roles que podem visualizar o `original_content` das mensagens via `session_context_get`. Default: `["evaluator", "reviewer"]`. Agentes primary e specialist recebem apenas tokens com display_partial.

### Audit Capture

- `capture_input_default` — se inputs de MCP tools são registrados no audit record
- `capture_output_default` — se outputs de MCP tools são registrados
- Nota: auditores nunca podem optar por *não* registrar — a política é definida na tool, não na chamada

### Retenção

`default_retention_days` — TTL padrão dos audit records no Kafka/ClickHouse.

### Categorias

Visualização das categorias de mascaramento configuradas (`DEFAULT_MASKING_RULES`): credit_card, cpf, cnpj, phone, email_addr, account_number. Para cada categoria:
- Padrão regex de detecção
- Formato de display_partial

## Componentes internos

| Componente Redis | TTL | Conteúdo |
|---|---|---|
| `{tenant_id}:token:{token_id}` | TTL da sessão | Mapeamento token → valor original |
| `{tenant_id}:masking:access_policy` | Sem TTL | `MaskingAccessPolicy` (authorized_roles + outras configs) |

**Fallback chain** de `loadAccessPolicy()`: legacy key → Config API tenant cache → Config API global cache → hardcoded default.

## Masked Input — begin_transaction / end_transaction

Para captura de dados altamente sensíveis (PINs, senhas OTPs) que nunca devem entrar no stream:

```yaml
- id: tx_inicio
  type: begin_transaction
  on_failure: coletar_senha   # rewind explícito

- id: coletar_senha
  type: menu
  masked: true                # step-level: todos os campos mascarados

- id: validar
  type: invoke
  input:
    senha: "@masked.senha"    # namespace @masked.* — lê do scope em memória

- id: tx_fim
  type: end_transaction
```

**Invariantes**:
- `masked_scope` existe apenas em memória — nunca escrito em Redis, `pipeline_state` ou stream
- `end_transaction` é exclusivamente o caminho de sucesso; rollback é automático e implícito
- `reason` step dentro do bloco é erro de design — rejeitado pelo agent-registry (HTTP 422)

## Relação com a Auditoria LGPD

O módulo de Auditoria LGPD (`/audit`) é o consumidor designado do `original_content` desmascarado: usuários com `module_config.audit.sessions` no JWT (DPO/compliance) têm acesso ao conteúdo original das mensagens, com cada acesso registrado de forma imutável em `audit_access_log`. A política `authorized_roles` definida nesta página continua governando quais roles operacionais (`evaluator`, `reviewer`) veem o `original_content` via `session_context_get`.

> A **Fase 2 do Audit LGPD** — resolução em batch dos tokens mascarados via endpoint dedicado no Core, exibindo o `original_content` diretamente na Auditoria — está pendente. Até lá, a Auditoria mostra o conteúdo mascarado.

## APIs envolvidas

A MaskingPage usa o Config API para leitura e escrita:
- `GET /config/masking` — todas as keys do namespace
- `PUT /config/masking/{key}` — atualiza configuração (ex: authorized_roles, default_retention_days)

O `MaskingService` em `mcp-server-plughub` aplica a política em runtime.

## Pacotes envolvidos

| Pacote | Responsabilidade |
|---|---|
| `mcp-server-plughub` | `lib/masking.ts` (aplicação), `lib/token-vault.ts` (Redis tokens), `tools/session.ts` (message_send + session_context_get) |
| `schemas` | `audit.ts` — `MaskingAccessPolicySchema`, `DEFAULT_MASKING_RULES`, `preserve_pattern` |
| `config-api` | Armazena `masking.*` configurações |
| `platform-ui` | `modules/masking/MaskingPage.tsx` |

## Referências

- ADR: `docs/adr/adr-message-masking.md`
- Backend: `packages/mcp-server-plughub/src/lib/masking.ts`, `packages/mcp-server-plughub/src/lib/token-vault.ts`
- Frontend: `packages/platform-ui/src/modules/masking/MaskingPage.tsx`
- Skill Flow: `docs/guias/masked-input.md`
