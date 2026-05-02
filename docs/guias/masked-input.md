# Masked Input — Captura Segura de Dados Sensíveis

> Spec de referência: v1.0 · Módulos: `skill-flow-engine`, `mcp-server-plughub`, `channel-gateway`, `@plughub/schemas`

---

## O que é

O modelo `masked input` permite que um skill colete dados altamente sensíveis (senhas, PINs, OTPs, números de cartão) com a garantia de que o valor **nunca entra no stream da sessão, nunca é persistido no `pipeline_state` e nunca aparece em replays ou auditorias**.

É complementar ao `MaskingService` existente:

| Mecanismo | Quando atua | Proteção |
|---|---|---|
| `MaskingService` | Dado chega como texto livre na conversa | Reativo — substitui por token após entrada |
| `masked: true` no menu | Coleta deliberada de credencial | Proativo — valor nunca entra no stream |

---

## Atributo `masked` no menu step

### Nível de step — todos os campos mascarados

```yaml
- id: coletar_credenciais
  type: menu
  interaction: form
  masked: true                  # todos os campos deste step são sensíveis
  prompt: "Informe suas credenciais para confirmar a operação"
  fields:
    - id: senha_atual
      label: Senha atual
      type: text
    - id: senha_nova
      label: Nova senha
      type: text
```

### Nível de campo — campos individuais

```yaml
- id: coletar_dados_pagamento
  type: menu
  interaction: form
  prompt: "Confirme os dados para continuar"
  fields:
    - id: nome_titular
      label: Nome do titular
      type: text
      masked: false             # aparece normalmente no histórico
    - id: cvv
      label: CVV
      type: text
      masked: true              # nunca entra no stream
```

Field-level tem precedência sobre step-level. Se `masked: true` no step e `masked: false` num campo, aquele campo não é mascarado.

---

## Comportamento por canal

Cada canal declara `supports_masked_input` em `ChannelCapabilities`. O Channel Gateway verifica antes de enviar a interação ao cliente.

| Canal | `supports_masked_input` | Comportamento |
|---|---|---|
| `webchat` | `true` | Overlay fora da lista de mensagens; `<input type="password">`; valor nunca no DOM |
| `whatsapp` | `false` | Executa `masked_fallback` configurado no canal |
| `voice` | `true` | Captura DTMF — mascarado por natureza; `masked: true` é semântico |
| `sms` | `false` | Executa `masked_fallback` |
| `email` | `false` | Executa `masked_fallback` |

### Fallback para canais sem suporte

```yaml
# ChannelCapabilities — channel-gateway/settings
webchat:
  supports_masked_input: true

whatsapp:
  supports_masked_input: false
  masked_fallback: message      # envia mensagem configurável ao cliente
  masked_fallback_message: >
    Esta operação requer o canal seguro. Por favor acesse o chat em nosso site.
```

Opções de `masked_fallback`:
- `"message"` — envia mensagem configurável (MVP)
- `"link"` — gera URL one-time para webchat seguro (Horizonte 2)
- `"decline"` — recusa a operação com mensagem de erro

### Webchat — renderização segura

Quando `masked: true`, o webchat:
1. Renderiza o formulário em **overlay/modal fora da lista de mensagens** — não como bubble de chat
2. Usa `<input type="password">` para campos mascarados — valor nunca aparece no DOM
3. Após submissão, o overlay fecha e a lista exibe apenas `"Informação segura enviada ✓"`
4. No cursor replay (reconexão), o servidor entrega o evento com valores substituídos por `"__masked__"` — o webchat renderiza o placeholder estático, nunca o campo interativo novamente

---

## Modelo de transação — `begin_transaction` / `end_transaction`

Qualquer fluxo que envolva captura sensível → validação → ação deve ser tratado como uma **unidade atômica**. Se qualquer step dentro do bloco falhar, o ciclo inteiro reinicia a partir do ponto declarado em `on_failure`.

### Sintaxe

```yaml
- id: iniciar_troca_senha
  type: begin_transaction
  on_failure: coletar_senha_atual   # ← ponto de rewind em caso de falha

- id: coletar_senha_atual
  type: menu
  interaction: form
  masked: true
  fields:
    - id: senha_atual
      masked: true
    - id: senha_nova
      masked: true

- id: validar_senha_atual
  type: invoke
  tool: mcp-server-crm/validate_password
  input:
    senha: "@masked.senha_atual"    # ← lê do masked_scope em memória

- id: aplicar_nova_senha
  type: invoke
  tool: mcp-server-crm/change_password
  input:
    senha_nova: "@masked.senha_nova"

- id: fechar_transacao
  type: end_transaction             # caminho feliz — limpa masked_scope
  result_as: troca_senha_status     # escreve status em pipeline_state

- id: confirmar_cliente
  type: notify
  message: "Sua senha foi alterada com sucesso."
```

### `on_failure` — rewind explícito

O autor declara explicitamente o ponto de rewind em `on_failure` no `begin_transaction`. O engine não infere o ponto de restart — a decisão é do skill author.

`on_failure` pode apontar para:
- Um step **dentro** do bloco (mais comum — re-coleta os dados mascarados)
- Um step **fora** do bloco (tratamento de erro externo, ex: avisar limite de tentativas)

```yaml
# Rewind para dentro do bloco
- id: tx
  type: begin_transaction
  on_failure: coletar_senha        # reinicia a coleta

# Rewind para fora do bloco
- id: tx
  type: begin_transaction
  on_failure: avisar_falha_maxima  # vai para tratamento externo
```

### `end_transaction` — caminho feliz

`end_transaction` é sempre o caminho de sucesso. Nunca existe um rollback explícito no YAML — rollback é automático e interno ao engine quando qualquer step dentro do bloco falha após esgotar tentativas.

---

## `masked_scope` — escopo em memória

Quando um `menu` step com `masked: true` completa, os valores sensíveis são armazenados num `masked_scope` — um mapa em memória dentro do contexto de execução do engine.

**O `masked_scope` nunca é:**
- Escrito em `pipeline_state` ou Redis
- Incluído em logs
- Incluído em `input_snapshot` de audit records
- Disponível após o encerramento da transação

### Namespace `@masked.*`

Steps dentro do bloco de transação referenciam valores do `masked_scope` com o namespace `@masked.*`:

```yaml
input:
  senha:     "@masked.senha_atual"
  senha_nova: "@masked.senha_nova"
```

A resolução segue a mesma lógica do `@ctx.*` — lê do `masked_scope` e retorna o valor tipado. Se o campo não existe no scope, retorna string vazia.

---

## Contrato do stream e `pipeline_state`

### O que vai para `pipeline_state.results`

`end_transaction` escreve apenas o status da operação — nunca os valores:

```json
{
  "troca_senha_status": {
    "status": "ok",
    "fields_collected": ["senha_atual", "senha_nova"],
    "completed_at": "2026-04-26T14:32:10Z"
  }
}
```

Em caso de falha (quando `on_failure` aponta para fora do bloco):

```json
{
  "troca_senha_status": {
    "status": "failed",
    "fields_collected": [],
    "failed_step": "validar_senha_atual",
    "failed_at": "2026-04-26T14:32:15Z"
  }
}
```

### O que vai para o stream da sessão

```json
{ "type": "interaction_result",
  "step_id": "coletar_senha_atual",
  "result": { "senha_atual": "__masked__", "senha_nova": "__masked__" },
  "masked_fields": ["senha_atual", "senha_nova"] }
```

O stream registra **o que foi coletado** (nomes dos campos) e **que eram mascarados**, mas nunca os valores.

---

## Audit record — campos mascarados

O McpInterceptor registra `AuditRecord` para cada MCP tool call. Quando o invoke recebe campos do `masked_scope`, esses campos são excluídos do `input_snapshot`:

```json
{
  "event_type": "mcp.tool_call",
  "tool_name": "validate_password",
  "masked_input_fields": ["senha"],
  "input_snapshot": null,           // null quando todos os inputs são mascarados
  "allowed": true,
  "duration_ms": 42
}
```

O campo `masked_input_fields: string[]` registra quais campos foram omitidos — mantendo a rastreabilidade (a chamada ocorreu, com quais campos) sem expor os valores.

---

## Restrições de design

### `reason` step dentro de transação — proibido

Steps do tipo `reason` não podem existir dentro de um bloco `begin_transaction` / `end_transaction` quando recebem campos mascarados como input.

O agente-registry valida esta restrição ao registrar ou atualizar um skill. Razão: o LLM receberia `"__masked__"` como valor, o que é inútil e potencialmente confuso.

```
Erro de validação:
  step "analisar_senha" (reason) recebe "@masked.senha" como input.
  reason steps não podem receber campos mascarados.
  Separe a lógica de análise para fora do bloco begin_transaction.
```

### Retry dentro da transação — nunca re-usa o valor mascarado

`catch` steps dentro do bloco de transação não fazem retry do step individual com o mesmo valor mascarado. O `catch` step propaga a falha para o `begin_transaction`, que executa o `on_failure`. A re-coleta é sempre do usuário — nunca da memória de uma tentativa anterior.

Razão: senha digitada há 30 segundos num ciclo que falhou não deve ser reusada. A segurança exige nova entrada do usuário.

---

## Exemplos de uso

### Troca de senha

```yaml
steps:
  - id: inicio_tx
    type: begin_transaction
    on_failure: coletar_senhas

  - id: coletar_senhas
    type: menu
    interaction: form
    masked: true
    prompt: "Para trocar sua senha, informe a senha atual e a nova senha"
    fields:
      - id: senha_atual
        label: Senha atual
        type: text
        masked: true
      - id: senha_nova
        label: Nova senha (mínimo 8 caracteres)
        type: text
        masked: true

  - id: validar_atual
    type: invoke
    tool: mcp-server-crm/validate_password
    input:
      customer_id: "@ctx.caller.customer_id"
      senha: "@masked.senha_atual"
    on_failure: coletar_senhas    # catch → rewind

  - id: aplicar_nova
    type: invoke
    tool: mcp-server-crm/change_password
    input:
      customer_id: "@ctx.caller.customer_id"
      senha_nova: "@masked.senha_nova"

  - id: fim_tx
    type: end_transaction
    result_as: troca_senha_ok

  - id: confirmar
    type: notify
    message: "Senha alterada com sucesso."
    on_success: complete_flow

  - id: complete_flow
    type: complete
    outcome: resolved
```

### PIN de autorização com formulário misto

```yaml
- id: inicio_tx
  type: begin_transaction
  on_failure: coletar_autorizacao

- id: coletar_autorizacao
  type: menu
  interaction: form
  prompt: "Confirme a operação informando seu PIN"
  fields:
    - id: valor_aprovado
      label: Valor confirmado (R$)
      type: text
      masked: false              # aparece no histórico — não é sensível
    - id: pin
      label: PIN de 4 dígitos
      type: text
      masked: true               # nunca entra no stream

- id: validar_pin
  type: invoke
  tool: mcp-server-billing/authorize_transaction
  input:
    account_id: "@ctx.caller.account_id"
    valor:      "@masked.valor_aprovado"   # resolvido do scope
    pin:        "@masked.pin"

- id: fim_tx
  type: end_transaction
  result_as: autorizacao_status
```

---

## Superfície de implementação

| Componente | Mudança |
|---|---|
| `@plughub/schemas / skill.ts` | `masked?: boolean` em `MenuStep` e `FormField`; novos step types `begin_transaction` e `end_transaction` com `on_failure` e `result_as` |
| `@plughub/schemas / channel-events.ts` | `supports_masked_input?: boolean` e `masked_fallback?` em `ChannelCapabilities` |
| `skill-flow-engine / executor.ts` | dispatch para `begin_transaction` e `end_transaction` |
| `skill-flow-engine / steps/begin-transaction.ts` | novo — abre masked_scope no contexto de execução; registra on_failure |
| `skill-flow-engine / steps/end-transaction.ts` | novo — limpa masked_scope; escreve result_as em pipeline_state |
| `skill-flow-engine / interpolate.ts` | namespace `@masked.*` resolve do masked_scope em memória |
| `skill-flow-engine / engine.ts` | detecção de falha dentro de bloco de transação → rewind para on_failure |
| `mcp-server-plughub / menu handler` | separação de campos mascarados antes da persistência; alimenta masked_scope via callback do engine |
| `channel-gateway / webchat adapter` | renderização em overlay; `<input type="password">`; placeholder no cursor replay |
| `channel-gateway / whatsapp adapter` | verificação de capabilities; execução de masked_fallback |
| `agent-registry` | validação de skill: `reason` step dentro de bloco masked → erro de registro |
| `AuditRecord` (`@plughub/schemas`) | campo `masked_input_fields: string[]` |

---

## Invariantes

- Valores mascarados **nunca** são escritos em `pipeline_state`, Redis, stream ou logs
- `masked_scope` existe apenas em memória durante a execução da transação
- Falha dentro de `begin_transaction`/`end_transaction` sempre executa `on_failure` — nunca re-usa valores do scope
- `reason` step dentro de bloco masked é erro de design, rejeitado pelo agent-registry
- `end_transaction` é exclusivamente o caminho de sucesso — rollback é sempre implícito e automático
- O audit record registra **que** campos mascarados foram enviados, mas nunca seus valores
- Channels que não suportam masked input executam `masked_fallback` — nunca tentam enviar o formulário
